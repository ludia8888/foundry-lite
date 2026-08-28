"""Helper functions for product-facing Source onboarding workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import BinaryIO

from foundry_lite.application.ports import (
    ConnectorConnectionRow,
    ConnectorRegistryRepository,
    DatasetTransactionRepository,
    SourceConnectionRecord,
    SourceConnectionRow,
    SourceConnectionUpdate,
    SourceRegistryRepository,
    StreamArchiveConfig,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.ports.source_upload_staging_store import SourceUploadStagingStore
from foundry_lite.application.services.media.uploads import MediaUploadInput
from foundry_lite.application.services.source_onboarding_config import (
    StagedMediaSourceUpload,
    StagedSourceUpload,
    reject_raw_secret_payload,
    source_record,
)
from foundry_lite.application.services.source_onboarding_protocols import (
    DatasetCsvIngestBoundary,
    DatasetRegistryBoundary,
    MediaTransactionBoundary,
    MediaUploadBoundary,
    SourcePolicyBoundary,
    SourceRuntimeBoundary,
)
from foundry_lite.application.services.source_onboarding_views import (
    CommitPayloadResult,
    commit_payload,
    media_upload_payload,
    rest_source_view,
    source_result,
    source_view,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed


def commit_batch_uploads(
    staging_store: SourceUploadStagingStore,
    engine: TransactionManager,
    dataset_transaction_repository: DatasetTransactionRepository,
    dataset_registry_service: DatasetRegistryBoundary,
    dataset_ingest_service: DatasetCsvIngestBoundary,
    ctx: RequestContext,
    staged: Sequence[StagedSourceUpload],
    sync_name: str | None,
) -> list[dict[str, object]]:
    commits: list[dict[str, object]] = []
    for upload in staged:
        dataset_ref = upload.dataset_ref
        dataset_registry_service.ensure_dataset(dataset_ref, ctx=ctx)
        with staging_store.materialize_path(upload.storage_uri) as csv_path:
            commit = dataset_ingest_service.upload_csv(dataset_ref, csv_path, ctx=ctx, sync_name=sync_name)
        run_id = sync_run_id(engine, dataset_transaction_repository, ctx, commit.transaction_id)
        commits.append(commit_payload(commit, run_id))
    return commits


def commit_staged_media(
    staging_store: SourceUploadStagingStore,
    media_transaction_service: MediaTransactionBoundary,
    media_upload_service: MediaUploadBoundary,
    ctx: RequestContext,
    row: SourceConnectionRow,
    staged: StagedMediaSourceUpload,
    logical_path: str,
    supplied_mime_type: str,
    schema_type: str,
    format: str,
    security_envelope: Mapping[str, object],
    probe_metadata: Mapping[str, object] | None,
    idempotency_key: str,
) -> dict[str, object]:
    with staging_store.open_upload(staged.storage_uri) as media_source:
        return upload_and_commit_media(
            media_transaction_service,
            media_upload_service,
            ctx,
            row["target_media_set_id"] or "",
            logical_path,
            media_source,
            supplied_mime_type,
            schema_type,
            format,
            security_envelope,
            probe_metadata,
            idempotency_key,
            row["config_fingerprint"],
        )


def upload_and_commit_media(
    media_transaction_service: MediaTransactionBoundary,
    media_upload_service: MediaUploadBoundary,
    ctx: RequestContext,
    media_set_id: str,
    logical_path: str,
    media_source: BinaryIO,
    supplied_mime_type: str,
    schema_type: str,
    format: str,
    security_envelope: Mapping[str, object],
    probe_metadata: Mapping[str, object] | None,
    idempotency_key: str,
    request_fingerprint: str,
) -> dict[str, object]:
    media_transaction_id = open_media_transaction(
        media_transaction_service, ctx, media_set_id, idempotency_key, request_fingerprint
    )
    upload = media_upload_service.initiate(
        ctx, media_set_id=media_set_id, logical_path=logical_path, supplied_mime_type=supplied_mime_type
    )
    staged = media_upload_service.complete(
        ctx,
        inputs=media_upload_input(
            media_set_id,
            media_transaction_id,
            logical_path,
            supplied_mime_type,
            schema_type,
            format,
            security_envelope,
            probe_metadata,
        ),
        upload=upload,
        source=media_source,
    )
    commit = media_transaction_service.commit(ctx, media_transaction_id=media_transaction_id)
    return media_upload_payload(staged, commit)


def open_media_transaction(
    media_transaction_service: MediaTransactionBoundary,
    ctx: RequestContext,
    media_set_id: str,
    idempotency_key: str,
    request_fingerprint: str,
) -> str:
    return media_transaction_service.open(
        ctx,
        media_set_id=media_set_id,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
    )


def media_upload_input(
    media_set_id: str,
    media_transaction_id: str,
    logical_path: str,
    supplied_mime_type: str,
    schema_type: str,
    format: str,
    security_envelope: Mapping[str, object],
    probe_metadata: Mapping[str, object] | None,
) -> MediaUploadInput:
    return MediaUploadInput(
        media_set_id=media_set_id,
        media_transaction_id=media_transaction_id,
        logical_path=logical_path,
        supplied_mime_type=supplied_mime_type,
        schema_type=schema_type,
        format=format,
        security_envelope=dict(security_envelope),
        probe_metadata=dict(probe_metadata or {}),
    )


def record_single_commit(
    engine: TransactionManager,
    source_registry_repository: SourceRegistryRepository,
    dataset_transaction_repository: DatasetTransactionRepository,
    ctx: RequestContext,
    source_name: str,
    commit: CommitPayloadResult,
    is_replayed: bool,
) -> dict[str, object]:
    run_id = sync_run_id(engine, dataset_transaction_repository, ctx, commit.transaction_id)
    payload = commit_payload(commit, run_id)
    row = update_source_commit(engine, source_registry_repository, ctx, source_name, run_id, payload)
    return source_result(row, commit=payload, is_replayed=is_replayed)


def record_batch_commits(
    engine: TransactionManager,
    source_registry_repository: SourceRegistryRepository,
    ctx: RequestContext,
    source_name: str,
    commits: Sequence[Mapping[str, object]],
    is_replayed: bool,
) -> dict[str, object]:
    first_run = first_run_id(commits)
    row = update_source_commit(
        engine,
        source_registry_repository,
        ctx,
        source_name,
        first_run,
        {"commits": list(commits)},
    )
    return source_result(row, commits=commits, is_replayed=is_replayed)


def record_optional_commit(
    engine: TransactionManager,
    source_registry_repository: SourceRegistryRepository,
    dataset_transaction_repository: DatasetTransactionRepository,
    ctx: RequestContext,
    source_name: str,
    commit: CommitPayloadResult | None,
) -> dict[str, object]:
    if commit is None:
        row = source_row(engine, source_registry_repository, ctx, source_name)
        return source_result(row, test_result={"status": "no_events"})
    return record_single_commit(
        engine,
        source_registry_repository,
        dataset_transaction_repository,
        ctx,
        source_name,
        commit,
        False,
    )


def record_media_commit(
    engine: TransactionManager,
    source_registry_repository: SourceRegistryRepository,
    ctx: RequestContext,
    source_name: str,
    media_result: Mapping[str, object],
    is_replayed: bool,
) -> dict[str, object]:
    row = update_source_commit(engine, source_registry_repository, ctx, source_name, None, media_result)
    return source_result(row, media_commit=media_result, is_replayed=is_replayed)


def update_source_commit(
    engine: TransactionManager,
    source_registry_repository: SourceRegistryRepository,
    ctx: RequestContext,
    source_name: str,
    run_id: str | None,
    commit: Mapping[str, object],
) -> SourceConnectionRow:
    with engine.begin() as conn:
        row = source_registry_repository.update_source(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            source_name=source_name,
            patch=SourceConnectionUpdate(
                last_run_id=run_id,
                last_commit_ref=commit,
                updated_at=now_text(),
            ),
        )
    if row is None:
        raise NotFound("source not found", details={"source_name": source_name})
    return row


def source_row(
    engine: TransactionManager,
    source_registry_repository: SourceRegistryRepository,
    ctx: RequestContext,
    source_name: str,
) -> SourceConnectionRow:
    with engine.begin() as conn:
        return source_row_in_tx(source_registry_repository, conn, ctx, source_name)


def source_row_in_tx(
    source_registry_repository: SourceRegistryRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    source_name: str,
) -> SourceConnectionRow:
    row = source_registry_repository.source_by_name(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        source_name=source_name,
    )
    if row is None:
        raise NotFound("source not found", details={"source_name": source_name})
    return row


def sync_run_id(
    engine: TransactionManager,
    dataset_transaction_repository: DatasetTransactionRepository,
    ctx: RequestContext,
    transaction_id: str,
) -> str | None:
    with engine.begin() as conn:
        row = dataset_transaction_repository.sync_run_by_transaction_id(
            transaction=conn, tenant_id=ctx.tenant_id, transaction_id=transaction_id
        )
    return row["id"] if row is not None else None


def rest_connections(
    connector_registry_repository: ConnectorRegistryRepository, ctx: RequestContext
) -> list[ConnectorConnectionRow]:
    return connector_registry_repository.list_connections(tenant_id=ctx.tenant_id)


def rest_connection(
    engine: TransactionManager,
    connector_registry_repository: ConnectorRegistryRepository,
    ctx: RequestContext,
    connector_name: str,
) -> ConnectorConnectionRow:
    with engine.begin() as conn:
        row = connector_registry_repository.connection_by_name(
            transaction=conn, tenant_id=ctx.tenant_id, connector_name=connector_name
        )
    if row is None:
        raise NotFound("source not found", details={"source_name": connector_name})
    return row


def rest_source_view_for_connection(
    engine: TransactionManager,
    connector_registry_repository: ConnectorRegistryRepository,
    ctx: RequestContext,
    row: ConnectorConnectionRow,
) -> dict[str, object]:
    with engine.begin() as conn:
        resources = connector_registry_repository.resources_for_connection(
            transaction=conn, tenant_id=ctx.tenant_id, connector_name=row["connector_name"]
        )
    return rest_source_view(row, resources)


def require_write(
    policy: SourcePolicyBoundary,
    runtime_service: SourceRuntimeBoundary,
    ctx: RequestContext,
    operation: str,
    resource_id: str,
) -> None:
    policy.require(ctx, "source:write")
    runtime_service._require_write_traffic_open(
        ctx,
        operation=operation,
        resource_type="source",
        resource_id=resource_id,
    )


def require_same_config(existing: SourceConnectionRow, record: SourceConnectionRecord) -> None:
    if existing["config_fingerprint"] != record.config_fingerprint:
        raise ConflictDetected(
            "source already exists with different config",
            details={"source_name": record.source_name},
        )


def require_kind(row: SourceConnectionRow, kind: str) -> None:
    if row["kind"] != kind:
        raise ValidationFailed("source kind does not support this operation", details={"kind": row["kind"]})


def require_source_fingerprint(row: SourceConnectionRow, expected: str | None) -> None:
    if expected is not None and expected != row["config_fingerprint"]:
        raise ConflictDetected(
            "source config changed after sync request",
            details={"source_name": row["source_name"]},
        )


def audit_source_created(
    runtime_service: SourceRuntimeBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    row: SourceConnectionRow,
) -> None:
    runtime_service._audit(
        conn,
        ctx,
        event_type="source.created",
        resource_type="source",
        resource_id=row["source_name"],
        action="create",
        after_ref=source_view(row),
    )


def commit_list(value: Mapping[str, object]) -> list[Mapping[str, object]]:
    commits = value.get("commits")
    return list(commits) if isinstance(commits, list) else []


def webhook_record(
    ctx: RequestContext,
    source_name: str,
    display_name: str,
    dataset_ref: str,
    connector_name: str,
    resource_name: str,
    signing_secret_ref: str,
    inbound_url: str,
) -> SourceConnectionRecord:
    summary = {
        "datasetRef": dataset_ref,
        "connectorName": connector_name,
        "resourceName": resource_name,
        "signingSecretRef": signing_secret_ref,
        "inboundUrl": inbound_url,
        "requiredHeaders": ["X-Foundry-Lite-Signature", "X-Foundry-Lite-Timestamp", "X-Foundry-Lite-Event-ID"],
    }
    return source_record(
        ctx,
        source_name=source_name,
        display_name=display_name,
        kind="webhook_listener",
        target_dataset_ref=dataset_ref,
        target_media_set_id=None,
        config_summary=summary,
    )


def debezium_record(
    ctx: RequestContext,
    source_name: str,
    display_name: str,
    dataset_ref: str,
    stream_name: str,
    topic: str,
    consumer_group: str,
    secret_refs: Mapping[str, object],
    primary_key: Sequence[str] = (),
) -> SourceConnectionRecord:
    reject_raw_secret_payload(secret_refs)
    summary = {
        "datasetRef": dataset_ref,
        "streamName": stream_name,
        "topic": topic,
        "consumerGroup": consumer_group,
        "primaryKey": list(primary_key),
        "schemaStrategy": "cdc_envelope_json",
        "secretRefs": dict(secret_refs),
    }
    return source_record(
        ctx,
        source_name=source_name,
        display_name=display_name,
        kind="debezium_cdc",
        target_dataset_ref=dataset_ref,
        target_media_set_id=None,
        config_summary=summary,
    )


def stream_config(summary: Mapping[str, object], limit: int | None) -> StreamArchiveConfig:
    return StreamArchiveConfig(
        stream_name=summary_text(summary, "streamName"),
        topic=summary_text(summary, "topic"),
        consumer_group=summary_text(summary, "consumerGroup"),
        schema_strategy="cdc_envelope_json",
        limit=limit or 100,
    )


def summary_text(summary: Mapping[str, object], field: str) -> str:
    value = summary.get(field)
    if isinstance(value, str) and value:
        return value
    raise ValidationFailed("source config is missing required field", details={"field": field})


def first_run_id(commits: Sequence[Mapping[str, object]]) -> str | None:
    if not commits:
        return None
    value = commits[0].get("runId")
    return value if isinstance(value, str) else None


def now_text() -> str:
    return datetime.now().astimezone().isoformat()
