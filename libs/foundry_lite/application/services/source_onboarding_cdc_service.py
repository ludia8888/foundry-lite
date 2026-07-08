"""Debezium CDC source onboarding service helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from foundry_lite.application.ports import (
    DatasetTransactionRepository,
    RuntimeRepository,
    SourceConnectionRecord,
    SourceConnectionRow,
    SourceConnectionUpdate,
    SourceRegistryRepository,
    SyncRunRecord,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.dataset.ingest import DatasetIngestService
from foundry_lite.application.services.source_onboarding_cdc_backlog import (
    CDC_OBJECT_INDEXER_WORKFLOW_NAME,
    CdcBacklogDatasetBoundary,
    cdc_backlog_payload,
    cdc_cursor_from_workflow,
    cdc_indexer_workflow_key,
    cdc_next_action,
    cdc_version_backlog,
    int_cursor,
)
from foundry_lite.application.services.source_onboarding_cdc_plan import debezium_operation_plan as cdc_plan_view
from foundry_lite.application.services.source_onboarding_config import require_idempotency_key
from foundry_lite.application.services.source_onboarding_helpers import (
    debezium_record,
    record_optional_commit,
    require_kind,
    require_source_fingerprint,
    require_write,
    source_row,
    stream_config,
    summary_text,
)
from foundry_lite.application.services.source_onboarding_protocols import (
    DatasetRegistryBoundary,
    SourcePolicyBoundary,
    SourceRuntimeBoundary,
)
from foundry_lite.application.services.source_onboarding_views import source_result
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound


class CdcSourceServiceBoundary(Protocol):
    @property
    def policy(self) -> SourcePolicyBoundary: ...

    @property
    def runtime_service(self) -> SourceRuntimeBoundary: ...

    @property
    def dataset_registry_service(self) -> DatasetRegistryBoundary: ...

    @property
    def engine(self) -> TransactionManager: ...

    @property
    def source_registry_repository(self) -> SourceRegistryRepository: ...

    @property
    def dataset_transaction_repository(self) -> DatasetTransactionRepository: ...

    @property
    def dataset_ingest_service(self) -> DatasetIngestService: ...

    def _create_source(
        self, ctx: RequestContext, record: SourceConnectionRecord
    ) -> tuple[SourceConnectionRow, bool]: ...


def create_debezium_source(
    service: CdcSourceServiceBoundary,
    source_name: str,
    display_name: str,
    dataset_ref: str,
    stream_name: str,
    topic: str,
    idempotency_key: str,
    ctx: RequestContext | None,
    consumer_group: str,
    secret_refs: Mapping[str, object] | None,
    primary_key: Sequence[str],
) -> dict[str, object]:
    ctx = ctx or RequestContext()
    require_write(service.policy, service.runtime_service, ctx, "create_debezium_source", source_name)
    require_idempotency_key(idempotency_key)
    service.dataset_registry_service.ensure_dataset(dataset_ref, ctx=ctx)
    row, is_replayed = service._create_source(
        ctx,
        debezium_record(
            ctx,
            source_name,
            display_name,
            dataset_ref,
            stream_name,
            topic,
            consumer_group,
            secret_refs or {},
            primary_key,
        ),
    )
    return source_result(row, is_replayed=is_replayed)


def debezium_operation_plan(
    service: CdcSourceServiceBoundary,
    source_name: str,
    ctx: RequestContext | None,
    object_type_api_name: str,
    runtime_repository: RuntimeRepository,
    dataset_versions: CdcBacklogDatasetBoundary,
) -> dict[str, object]:
    ctx = ctx or RequestContext()
    service.policy.require(ctx, "source:read")
    row = source_row(service.engine, service.source_registry_repository, ctx, source_name)
    require_kind(row, "debezium_cdc")
    dataset_ref = summary_text(row["config_summary"], "datasetRef")
    status = _object_indexing_status(
        service,
        runtime_repository,
        dataset_versions,
        ctx,
        object_type_api_name,
        dataset_ref,
    )
    return cdc_plan_view(row, object_type_api_name=object_type_api_name, object_indexing_status=status)


def _object_indexing_status(
    service: CdcSourceServiceBoundary,
    runtime_repository: RuntimeRepository,
    dataset_versions: CdcBacklogDatasetBoundary,
    ctx: RequestContext,
    object_type_api_name: str,
    dataset_ref: str,
) -> dict[str, object]:
    workflow = _object_indexer_workflow(service, runtime_repository, ctx, object_type_api_name, dataset_ref)
    cursor = cdc_cursor_from_workflow(workflow)
    backlog = cdc_version_backlog(dataset_versions, ctx, dataset_ref, cursor)
    return {
        "objectTypeApiName": object_type_api_name,
        "sourceDatasetRef": dataset_ref,
        "workflowName": CDC_OBJECT_INDEXER_WORKFLOW_NAME,
        "workflowRunId": str(workflow["id"]) if workflow is not None else None,
        "workflowOperationPath": _workflow_operation_path(workflow),
        "lastIndexedVersionNumber": int_cursor(cursor, "lastIndexedVersionNumber"),
        "lastIndexedVersionId": cursor.get("lastIndexedVersionId"),
        "lastIndexRunId": cursor.get("lastIndexRunId"),
        "cursor": cursor,
        "backlog": cdc_backlog_payload(backlog),
        "nextAction": cdc_next_action(is_indexed=bool(cursor), backlog=backlog),
    }


def _object_indexer_workflow(
    service: CdcSourceServiceBoundary,
    runtime_repository: RuntimeRepository,
    ctx: RequestContext,
    object_type_api_name: str,
    dataset_ref: str,
) -> Mapping[str, object] | None:
    with service.engine.begin() as conn:
        return runtime_repository.workflow_run_by_idempotency(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            workflow_name=CDC_OBJECT_INDEXER_WORKFLOW_NAME,
            idempotency_key=cdc_indexer_workflow_key(object_type_api_name, dataset_ref),
        )


def _workflow_operation_path(workflow: Mapping[str, object] | None) -> str | None:
    if workflow is None:
        return None
    return f"/api/operations/runs/workflow/{workflow['id']}"


def start_debezium_sync(
    service: CdcSourceServiceBoundary,
    source_name: str,
    idempotency_key: str,
    ctx: RequestContext | None,
    expected_config_fingerprint: str | None,
    after_offset: int | None,
    limit: int | None,
) -> dict[str, object]:
    ctx = ctx or RequestContext()
    require_write(service.policy, service.runtime_service, ctx, "start_debezium_sync", source_name)
    require_idempotency_key(idempotency_key)
    row = source_row(service.engine, service.source_registry_repository, ctx, source_name)
    require_source_fingerprint(row, expected_config_fingerprint)
    commit = service.dataset_ingest_service.archive_stream_events(
        summary_text(row["config_summary"], "datasetRef"),
        stream=stream_config(row["config_summary"], limit),
        ctx=ctx,
        after_offset=after_offset,
        sync_name=f"cdc:{source_name}",
    )
    if commit is None:
        return _record_no_event_sync(service, row, ctx)
    return record_optional_commit(
        service.engine,
        service.source_registry_repository,
        service.dataset_transaction_repository,
        ctx,
        row["source_name"],
        commit,
    )


def _record_no_event_sync(
    service: CdcSourceServiceBoundary,
    row: SourceConnectionRow,
    ctx: RequestContext,
) -> dict[str, object]:
    summary = row["config_summary"]
    dataset_ref = summary_text(summary, "datasetRef")
    dataset = service.dataset_registry_service.ensure_dataset(dataset_ref, ctx=ctx)
    run_id = _new_id("sync_run")
    sync_name = f"cdc:{row['source_name']}"
    checked_at = _now()
    evidence = _no_event_evidence(row, run_id, sync_name, checked_at)
    with service.engine.begin() as conn:
        service.dataset_transaction_repository.insert_sync_run(
            transaction=conn,
            record=SyncRunRecord(
                sync_run_id=run_id,
                tenant_id=ctx.tenant_id,
                sync_name=sync_name,
                source_type=f"stream.{summary_text(summary, 'streamName')}",
                output_dataset_id=str(dataset["id"]),
                transaction_id=None,
                committed_version_id=None,
                status="COMMITTED",
                error=None,
                created_at=checked_at,
                completed_at=checked_at,
            ),
        )
        updated = service.source_registry_repository.update_source(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            source_name=row["source_name"],
            patch=SourceConnectionUpdate(last_run_id=run_id, last_commit_ref=evidence, updated_at=checked_at),
        )
        if updated is None:
            raise NotFound("source not found", details={"source_name": row["source_name"]})
        _audit_no_event(service, conn, ctx, row["source_name"], evidence)
    return source_result(updated, test_result=evidence)


def _no_event_evidence(
    row: SourceConnectionRow,
    run_id: str,
    sync_name: str,
    checked_at: str,
) -> dict[str, object]:
    summary = row["config_summary"]
    return {
        "status": "no_events",
        "runId": run_id,
        "syncName": sync_name,
        "datasetRef": summary_text(summary, "datasetRef"),
        "streamName": summary_text(summary, "streamName"),
        "topic": summary_text(summary, "topic"),
        "checkedAt": checked_at,
    }


def _audit_no_event(
    service: CdcSourceServiceBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    source_name: str,
    evidence: dict[str, object],
) -> None:
    service.runtime_service._audit(
        conn,
        ctx,
        event_type="source.cdc.no_events",
        resource_type="source",
        resource_id=source_name,
        action="cdc_archive_no_events",
        after_ref=evidence,
        correlation_id=str(evidence["runId"]),
    )
