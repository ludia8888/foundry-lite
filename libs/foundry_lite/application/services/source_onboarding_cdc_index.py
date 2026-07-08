"""Browser-bounded CDC object indexing helpers for source onboarding."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast
from uuid import uuid4

from foundry_lite.application.ports import (
    DatasetRow,
    DatasetVersionRow,
    ObjectIndexCdcResult,
    ObjectPropertyMap,
    RuntimeJsonObject,
    RuntimeRepository,
    SourceConnectionRow,
    SourceRegistryRepository,
    TransactionManager,
    WorkflowLedgerStatus,
    WorkflowRunRecord,
    WorkflowStartRequest,
    workflow_request_fingerprint,
    workflow_run_id,
)
from foundry_lite.application.services.runtime_error_payloads import runtime_error_payload
from foundry_lite.application.services.source_onboarding_cdc_backlog import (
    CDC_OBJECT_INDEXER_WORKFLOW_NAME,
    CdcObjectIndexBacklog,
    cdc_backlog_payload,
    cdc_cursor_from_output,
    cdc_cursor_from_workflow,
    cdc_indexer_workflow_key,
    cdc_next_action,
    cdc_version_backlog,
    int_cursor,
)
from foundry_lite.application.services.source_onboarding_config import require_idempotency_key
from foundry_lite.application.services.source_onboarding_helpers import (
    require_kind,
    require_source_fingerprint,
    source_row,
    summary_text,
)
from foundry_lite.application.services.source_onboarding_protocols import SourcePolicyBoundary
from foundry_lite.application.services.source_onboarding_views import source_view
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed


class CdcObjectIndexDatasetBoundary(Protocol):
    def ensure_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        primary_key: list[str] | None = None,
        storage_kind: str = "parquet_manifest",
        partition_spec: list[str] | None = None,
        sort_order: list[str] | None = None,
        target_file_size_bytes: int | None = None,
    ) -> DatasetRow: ...

    def list_dataset_versions(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[DatasetVersionRow]: ...

    def preview_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        limit: int = 100,
        version: str = "latest",
    ) -> list[Mapping[str, object]]: ...


class CdcObjectIndexBoundary(Protocol):
    def index_cdc_events(
        self,
        object_type_api_name: str,
        events: Sequence[ObjectPropertyMap],
        *,
        ctx: RequestContext | None = None,
    ) -> ObjectIndexCdcResult: ...


class CdcObjectIndexRuntimeBoundary(Protocol):
    def _require_write_traffic_open(
        self,
        ctx: RequestContext,
        *,
        operation: str,
        resource_type: str,
        resource_id: str,
    ) -> None: ...


@dataclass(frozen=True)
class CdcObjectIndexRuntime:
    policy: SourcePolicyBoundary
    runtime_service: CdcObjectIndexRuntimeBoundary
    engine: TransactionManager
    runtime_repository: RuntimeRepository
    source_registry_repository: SourceRegistryRepository
    dataset_registry_service: CdcObjectIndexDatasetBoundary
    object_cdc_indexing_service: CdcObjectIndexBoundary


@dataclass(frozen=True)
class _CdcObjectIndexBatch:
    source_dataset_version_id: str
    source_dataset_version_number: int
    event_count: int
    index_run_id: str
    objects_upserted: int
    objects_deleted: int
    events_skipped: int


@dataclass(frozen=True)
class _IndexerLease:
    workflow_run_id: str
    owner_id: str
    token: str
    cursor: Mapping[str, object]


def start_debezium_object_index(
    runtime: CdcObjectIndexRuntime,
    source_name: str,
    object_type_api_name: str,
    idempotency_key: str,
    ctx: RequestContext | None,
    expected_config_fingerprint: str | None,
    max_rows_per_version: int,
) -> dict[str, object]:
    ctx = ctx or RequestContext()
    _validate_max_rows(max_rows_per_version)
    _require_write(runtime.policy, runtime.runtime_service, ctx, "start_debezium_object_index", source_name)
    require_idempotency_key(idempotency_key)
    row = source_row(runtime.engine, runtime.source_registry_repository, ctx, source_name)
    require_kind(row, "debezium_cdc")
    require_source_fingerprint(row, expected_config_fingerprint)
    dataset_ref = summary_text(row["config_summary"], "datasetRef")
    lease = _claim_indexer_lease(runtime, ctx, object_type_api_name, dataset_ref)
    try:
        batch = _run_indexer_once(runtime, ctx, object_type_api_name, dataset_ref, max_rows_per_version, lease.cursor)
    except Exception as exc:
        _release_indexer_lease(runtime, ctx, lease, "failed", _single_output(None, lease.cursor), exc)
        raise
    output = _single_output(batch, _cursor_after_batch(batch, lease.cursor))
    _release_indexer_lease(runtime, ctx, lease, "succeeded", output, None)
    backlog = cdc_version_backlog(runtime.dataset_registry_service, ctx, dataset_ref, cdc_cursor_from_output(output))
    return _response_payload(row, object_type_api_name, dataset_ref, batch, lease, output, backlog)


def _validate_max_rows(max_rows_per_version: int) -> None:
    if max_rows_per_version <= 0:
        raise ValidationFailed(
            "CDC object index maxRowsPerVersion must be positive",
            details={"maxRowsPerVersion": max_rows_per_version},
        )


def _require_write(
    policy: SourcePolicyBoundary,
    runtime_service: CdcObjectIndexRuntimeBoundary,
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


def _run_indexer_once(
    runtime: CdcObjectIndexRuntime,
    ctx: RequestContext,
    object_type_api_name: str,
    dataset_ref: str,
    max_rows_per_version: int,
    cursor: Mapping[str, object],
) -> _CdcObjectIndexBatch | None:
    version = _next_source_version(runtime, ctx, dataset_ref, cursor)
    if version is None:
        return None
    _validate_version_limit(dataset_ref, version, max_rows_per_version)
    events = _events_for_version(runtime, ctx, dataset_ref, version, max_rows_per_version)
    result = runtime.object_cdc_indexing_service.index_cdc_events(object_type_api_name, events, ctx=ctx)
    return _batch_result(version, events, result)


def _next_source_version(
    runtime: CdcObjectIndexRuntime,
    ctx: RequestContext,
    dataset_ref: str,
    cursor: Mapping[str, object],
) -> DatasetVersionRow | None:
    last_number = int_cursor(cursor, "lastIndexedVersionNumber")
    for version in runtime.dataset_registry_service.list_dataset_versions(dataset_ref, ctx=ctx):
        if last_number is None or int(version["version_number"]) > last_number:
            return version
    return None


def _validate_version_limit(dataset_ref: str, version: DatasetVersionRow, max_rows_per_version: int) -> None:
    row_count = int(version["row_count"])
    if row_count <= max_rows_per_version:
        return
    raise ValidationFailed(
        "CDC object indexer source version exceeds maxRowsPerVersion",
        details={
            "sourceDatasetRef": dataset_ref,
            "sourceDatasetVersionId": version["id"],
            "sourceDatasetVersionNumber": version["version_number"],
            "rowCount": row_count,
            "maxRowsPerVersion": max_rows_per_version,
        },
    )


def _events_for_version(
    runtime: CdcObjectIndexRuntime,
    ctx: RequestContext,
    dataset_ref: str,
    version: DatasetVersionRow,
    max_rows_per_version: int,
) -> tuple[ObjectPropertyMap, ...]:
    rows = runtime.dataset_registry_service.preview_dataset(
        dataset_ref,
        ctx=ctx,
        version=version["id"],
        limit=max_rows_per_version,
    )
    return tuple(cast(ObjectPropertyMap, dict(row)) for row in rows)


def _batch_result(
    version: DatasetVersionRow,
    events: Sequence[ObjectPropertyMap],
    result: ObjectIndexCdcResult,
) -> _CdcObjectIndexBatch:
    return _CdcObjectIndexBatch(
        source_dataset_version_id=version["id"],
        source_dataset_version_number=int(version["version_number"]),
        event_count=len(events),
        index_run_id=str(result["index_run_id"]),
        objects_upserted=int(result["objects_upserted"]),
        objects_deleted=int(result["objects_deleted"]),
        events_skipped=int(result["events_skipped"]),
    )


def _claim_indexer_lease(
    runtime: CdcObjectIndexRuntime,
    ctx: RequestContext,
    object_type_api_name: str,
    dataset_ref: str,
) -> _IndexerLease:
    now = _utc_now()
    token = f"lease-{uuid4().hex}"
    with runtime.engine.begin() as conn:
        existing = runtime.runtime_repository.workflow_run_by_idempotency(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            workflow_name=CDC_OBJECT_INDEXER_WORKFLOW_NAME,
            idempotency_key=cdc_indexer_workflow_key(object_type_api_name, dataset_ref),
        )
        cursor = cdc_cursor_from_workflow(existing)
        row = runtime.runtime_repository.claim_workflow_run_lease(
            transaction=conn,
            record=_lease_record(ctx, object_type_api_name, dataset_ref, token, now, cursor),
            lease_owner_id=_BROWSER_LEASE_OWNER,
            now=_utc_iso(now),
        )
    if row is None:
        raise ConflictDetected(
            "CDC object indexer is already running",
            details={"objectTypeApiName": object_type_api_name, "sourceDatasetRef": dataset_ref},
        )
    return _IndexerLease(str(row["id"]), _BROWSER_LEASE_OWNER, token, cursor)


def _lease_record(
    ctx: RequestContext,
    object_type_api_name: str,
    dataset_ref: str,
    token: str,
    now: datetime,
    cursor: Mapping[str, object],
) -> WorkflowRunRecord:
    request = _workflow_request(ctx, object_type_api_name, dataset_ref)
    return WorkflowRunRecord(
        workflow_run_id=workflow_run_id(request),
        tenant_id=ctx.tenant_id,
        workflow_name=request.workflow_name,
        workflow_profile="cdc-object-indexer-worker",
        status="running",
        idempotency_key=request.idempotency_key,
        request_fingerprint=workflow_request_fingerprint(request),
        input=dict(request.input),
        output=_lease_output(object_type_api_name, dataset_ref, token, now, cursor),
        error=None,
        dataset_id=None,
        audit_event_id=None,
        attempts=1,
        created_at=_utc_iso(now),
        started_at=_utc_iso(now),
        completed_at=None,
    )


def _workflow_request(ctx: RequestContext, object_type_api_name: str, dataset_ref: str) -> WorkflowStartRequest:
    return WorkflowStartRequest(
        workflow_name=CDC_OBJECT_INDEXER_WORKFLOW_NAME,
        tenant_id=ctx.tenant_id,
        request_id=ctx.request_id,
        idempotency_key=cdc_indexer_workflow_key(object_type_api_name, dataset_ref),
        input={"objectTypeApiName": object_type_api_name, "sourceDatasetRef": dataset_ref},
    )


def _lease_output(
    object_type_api_name: str,
    dataset_ref: str,
    token: str,
    now: datetime,
    cursor: Mapping[str, object],
) -> dict[str, object]:
    return {
        "workerLease": {
            "ownerId": _BROWSER_LEASE_OWNER,
            "leaseToken": token,
            "leaseExpiresAt": _utc_iso(now + timedelta(seconds=30)),
            "lastHeartbeatAt": _utc_iso(now),
        },
        "cdcObjectIndexer": _indexer_output(object_type_api_name, dataset_ref, cursor),
    }


def _release_indexer_lease(
    runtime: CdcObjectIndexRuntime,
    ctx: RequestContext,
    lease: _IndexerLease,
    status: WorkflowLedgerStatus,
    output: Mapping[str, object],
    exc: Exception | None,
) -> None:
    error = runtime_error_payload(exc, ctx, adapter="cdc_object_indexer_browser") if exc is not None else None
    with runtime.engine.begin() as conn:
        runtime.runtime_repository.release_workflow_run_lease(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            workflow_run_id=lease.workflow_run_id,
            lease_owner_id=lease.owner_id,
            lease_token=lease.token,
            status=status,
            output=cast(RuntimeJsonObject, dict(output)),
            error=cast(RuntimeJsonObject, error) if error is not None else None,
            completed_at=_utc_iso(_utc_now()),
        )


def _response_payload(
    row: SourceConnectionRow,
    object_type_api_name: str,
    dataset_ref: str,
    batch: _CdcObjectIndexBatch | None,
    lease: _IndexerLease,
    output: Mapping[str, object],
    backlog: CdcObjectIndexBacklog,
) -> dict[str, object]:
    base = _base_response(row, object_type_api_name, dataset_ref, batch, lease, output, backlog)
    if batch is None:
        return base
    return {
        **base,
        "sourceDatasetVersionId": batch.source_dataset_version_id,
        "sourceDatasetVersionNumber": batch.source_dataset_version_number,
        "eventCount": batch.event_count,
        "indexRunId": batch.index_run_id,
        "objectsUpserted": batch.objects_upserted,
        "objectsDeleted": batch.objects_deleted,
        "eventsSkipped": batch.events_skipped,
        "operationsPath": f"/api/operations/runs/index/{batch.index_run_id}",
    }


def _base_response(
    row: SourceConnectionRow,
    object_type_api_name: str,
    dataset_ref: str,
    batch: _CdcObjectIndexBatch | None,
    lease: _IndexerLease,
    output: Mapping[str, object],
    backlog: CdcObjectIndexBacklog,
) -> dict[str, object]:
    return {
        "status": "INDEXED" if batch is not None else "NO_VERSIONS",
        "source": source_view(row),
        "objectTypeApiName": object_type_api_name,
        "sourceDatasetRef": dataset_ref,
        "sourceDatasetVersionId": None,
        "sourceDatasetVersionNumber": None,
        "eventCount": 0,
        "indexRunId": None,
        "objectsUpserted": 0,
        "objectsDeleted": 0,
        "eventsSkipped": 0,
        "operationsPath": None,
        "workflowRunId": lease.workflow_run_id,
        "workflowOperationPath": f"/api/operations/runs/workflow/{lease.workflow_run_id}",
        "cursor": cdc_cursor_from_output(output),
        "backlog": cdc_backlog_payload(backlog),
        "nextAction": cdc_next_action(is_indexed=batch is not None, backlog=backlog),
    }


def _single_output(batch: _CdcObjectIndexBatch | None, cursor: Mapping[str, object]) -> dict[str, object]:
    if batch is None:
        return {"status": "NO_VERSIONS", "cdcObjectIndexer": {"cursor": dict(cursor)}}
    return {"status": "INDEXED", "batch": _batch_payload(batch), "cdcObjectIndexer": {"cursor": dict(cursor)}}


def _batch_payload(batch: _CdcObjectIndexBatch) -> dict[str, object]:
    return {
        "sourceDatasetVersionId": batch.source_dataset_version_id,
        "sourceDatasetVersionNumber": batch.source_dataset_version_number,
        "eventCount": batch.event_count,
        "indexRunId": batch.index_run_id,
        "objectsUpserted": batch.objects_upserted,
        "objectsDeleted": batch.objects_deleted,
        "eventsSkipped": batch.events_skipped,
    }


def _cursor_after_batch(
    batch: _CdcObjectIndexBatch | None,
    previous: Mapping[str, object],
) -> Mapping[str, object]:
    if batch is None:
        return dict(previous)
    return {
        "lastIndexedVersionId": batch.source_dataset_version_id,
        "lastIndexedVersionNumber": batch.source_dataset_version_number,
        "lastIndexRunId": batch.index_run_id,
    }


def _indexer_output(
    object_type_api_name: str,
    dataset_ref: str,
    cursor: Mapping[str, object],
) -> dict[str, object]:
    return {"objectTypeApiName": object_type_api_name, "sourceDatasetRef": dataset_ref, "cursor": dict(cursor)}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_iso(value: datetime) -> str:
    return value.isoformat()


_BROWSER_LEASE_OWNER = "browser-cdc-object-indexer"
