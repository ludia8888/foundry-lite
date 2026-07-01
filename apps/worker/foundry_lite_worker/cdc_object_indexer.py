from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import (
    DatasetVersionRow,
    ObjectIndexCdcResult,
    ObjectPropertyMap,
    RuntimeRow,
    WorkflowLedgerStatus,
    WorkflowRunRecord,
    WorkflowRunRow,
    WorkflowStartRequest,
    workflow_request_fingerprint,
    workflow_run_id,
)
from foundry_lite.application.services.runtime_error_payloads import runtime_error_payload
from foundry_lite.domain.context import DEFAULT_TENANT_ID, DEMO_ADMIN_ROLES, RequestContext
from foundry_lite.domain.errors import ConflictDetected, FoundryLiteError, ValidationFailed
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


@dataclass(frozen=True)
class CdcObjectIndexerWorkerConfig:
    object_type_api_name: str
    source_dataset_ref: str
    storage_root: Path
    db_url: str | None = None
    adapter_profile: str = "local"
    max_rows_per_version: int = 10_000
    is_continuous: bool = False
    continuous_max_batches: int | None = None
    continuous_max_empty_polls: int = 1
    worker_id: str = "cdc-object-indexer-worker"
    lease_ttl_seconds: int = 30
    tenant_id: str = DEFAULT_TENANT_ID
    actor_user_id: str = "worker-cdc-object-indexer"
    request_id: str = "req-worker-cdc-object-indexer"

    def request_context(self, *, batch_number: int | None = None) -> RequestContext:
        request_id = _required_worker_value("request_id", self.request_id)
        if batch_number is not None:
            request_id = f"{request_id}:batch-{batch_number}"
        return RequestContext(
            tenant_id=_required_worker_value("tenant_id", self.tenant_id),
            actor_user_id=_required_worker_value("actor_user_id", self.actor_user_id),
            request_id=request_id,
            roles=DEMO_ADMIN_ROLES,
        )


@dataclass(frozen=True)
class CdcObjectIndexBatchResult:
    source_dataset_version_id: str
    source_dataset_version_number: int
    event_count: int
    index_run_id: str
    objects_upserted: int
    objects_deleted: int
    events_skipped: int


@dataclass(frozen=True)
class ContinuousCdcObjectIndexerResult:
    iterations: int
    indexed_batches: int
    empty_polls: int
    events_indexed: int
    last_version_id: str | None
    last_index_run_id: str | None
    stop_reason: Literal["empty_polls", "max_batches", "stop_requested", "lease_lost"]
    workflow_run_id: str | None = None
    lease_owner_id: str | None = None


@dataclass(frozen=True)
class CdcObjectIndexerWorkerRuntime:
    foundry: FoundryLite


@dataclass(frozen=True)
class _WorkerLease:
    workflow_run_id: str
    owner_id: str
    token: str
    cursor: Mapping[str, object]


def run_cdc_object_indexer_once(config: CdcObjectIndexerWorkerConfig) -> CdcObjectIndexBatchResult | None:
    _validate_worker_config(config)
    runtime = build_cdc_object_indexer_runtime(config)
    lease = _claim_worker_lease(config, runtime, ctx=config.request_context())
    try:
        result = _run_cdc_object_indexer_once(
            config,
            runtime,
            cursor=lease.cursor,
            ctx=config.request_context(batch_number=1),
        )
    except Exception as exc:
        _release_worker_lease(config, runtime, lease, _single_output(None, lease.cursor), "failed", exc)
        raise
    cursor = _cursor_after_result(result, lease.cursor)
    _release_worker_lease(config, runtime, lease, _single_output(result, cursor), "succeeded", None)
    return result


def run_cdc_object_indexer_continuously(
    config: CdcObjectIndexerWorkerConfig,
    *,
    should_stop: Callable[[], bool] | None = None,
) -> ContinuousCdcObjectIndexerResult:
    _validate_worker_config(config)
    stop_requested = should_stop or _never_stop
    runtime = build_cdc_object_indexer_runtime(config)
    lease = _claim_worker_lease(config, runtime, ctx=config.request_context())
    state = _LoopState()
    while True:
        if stop_requested():
            return _finish_continuous_result(config, runtime, lease, state, "stop_requested")
        if _max_batches_reached(state.indexed_batches, config.continuous_max_batches):
            return _finish_continuous_result(config, runtime, lease, state, "max_batches")
        if not _worker_lease_is_current(runtime, config.request_context(), lease):
            return _continuous_result(state, "lease_lost", lease)
        result = _run_cdc_object_indexer_once(
            config,
            runtime,
            cursor=lease.cursor,
            ctx=config.request_context(batch_number=state.iterations + 1),
        )
        state = state.after_iteration(result)
        lease = _claim_worker_lease(config, runtime, ctx=config.request_context(), lease=lease, cursor=state.cursor)
        if result is None and state.empty_polls >= config.continuous_max_empty_polls:
            return _finish_continuous_result(config, runtime, lease, state, "empty_polls")


def build_cdc_object_indexer_runtime(config: CdcObjectIndexerWorkerConfig) -> CdcObjectIndexerWorkerRuntime:
    dependencies = create_local_core_dependencies(
        db_url=config.db_url,
        storage_root=config.storage_root,
        adapter_profile=config.adapter_profile,
    )
    foundry = FoundryLite(dependencies=dependencies)
    ctx = config.request_context()
    foundry.datasets.get(config.source_dataset_ref, ctx=ctx)
    return CdcObjectIndexerWorkerRuntime(foundry=foundry)


def _run_cdc_object_indexer_once(
    config: CdcObjectIndexerWorkerConfig,
    runtime: CdcObjectIndexerWorkerRuntime,
    *,
    cursor: Mapping[str, object],
    ctx: RequestContext,
) -> CdcObjectIndexBatchResult | None:
    version = _next_source_version(config, runtime, ctx, cursor)
    if version is None:
        return None
    events = _events_for_version(config, runtime, ctx, version)
    result = runtime.foundry.objects.index_cdc_events(config.object_type_api_name, events, ctx=ctx)
    return _batch_result(version, events, result)


def config_from_env(env: Mapping[str, str] | None = None) -> CdcObjectIndexerWorkerConfig:
    values = env or os.environ
    return CdcObjectIndexerWorkerConfig(
        object_type_api_name=values.get("FOUNDRY_LITE_CDC_OBJECT_TYPE", "Order"),
        source_dataset_ref=values.get("FOUNDRY_LITE_CDC_SOURCE_DATASET", "raw_cdc.erp_orders"),
        storage_root=Path(values.get("FOUNDRY_LITE_HOME", ".foundry-lite")),
        db_url=values.get("FOUNDRY_LITE_DB_URL"),
        adapter_profile=values.get("FOUNDRY_LITE_ADAPTER_PROFILE", "local"),
        max_rows_per_version=_env_int(values, "FOUNDRY_LITE_CDC_INDEXER_MAX_ROWS_PER_VERSION", 10_000),
        is_continuous=_env_bool(values, "FOUNDRY_LITE_CDC_INDEXER_CONTINUOUS", False),
        continuous_max_batches=_env_optional_int(values, "FOUNDRY_LITE_CDC_INDEXER_MAX_BATCHES"),
        continuous_max_empty_polls=_env_int(values, "FOUNDRY_LITE_CDC_INDEXER_MAX_EMPTY_POLLS", 1),
        worker_id=values.get("FOUNDRY_LITE_CDC_INDEXER_WORKER_ID", "cdc-object-indexer-worker"),
        lease_ttl_seconds=_env_int(values, "FOUNDRY_LITE_CDC_INDEXER_LEASE_TTL_SECONDS", 30),
        tenant_id=values.get("FOUNDRY_LITE_TENANT_ID", DEFAULT_TENANT_ID),
        actor_user_id=values.get("FOUNDRY_LITE_WORKER_ACTOR_USER_ID", "worker-cdc-object-indexer"),
        request_id=values.get("FOUNDRY_LITE_WORKER_REQUEST_ID", "req-worker-cdc-object-indexer"),
    )


def main(argv: list[str] | None = None) -> int:
    config: CdcObjectIndexerWorkerConfig | None = None
    try:
        config = _config_from_args(_parser().parse_args(argv))
        if config.is_continuous:
            print(_continuous_result_json(run_cdc_object_indexer_continuously(config)))
            return 0
        print(_batch_result_json(run_cdc_object_indexer_once(config)))
        return 0
    except (FoundryLiteError, ValueError) as exc:
        print(_failure_json(exc, config))
        return 1


@dataclass(frozen=True)
class _LoopState:
    iterations: int = 0
    indexed_batches: int = 0
    empty_polls: int = 0
    events_indexed: int = 0
    cursor: Mapping[str, object] | None = None
    last_version_id: str | None = None
    last_index_run_id: str | None = None

    def after_iteration(self, result: CdcObjectIndexBatchResult | None) -> _LoopState:
        cursor = _cursor_after_result(result, self.cursor or {})
        return _LoopState(
            iterations=self.iterations + 1,
            indexed_batches=self.indexed_batches + (1 if result is not None else 0),
            empty_polls=self.empty_polls + 1 if result is None else 0,
            events_indexed=self.events_indexed + (result.event_count if result is not None else 0),
            cursor=cursor,
            last_version_id=result.source_dataset_version_id if result is not None else self.last_version_id,
            last_index_run_id=result.index_run_id if result is not None else self.last_index_run_id,
        )


def _next_source_version(
    config: CdcObjectIndexerWorkerConfig,
    runtime: CdcObjectIndexerWorkerRuntime,
    ctx: RequestContext,
    cursor: Mapping[str, object],
) -> DatasetVersionRow | None:
    last_number = _int_cursor(cursor, "lastIndexedVersionNumber")
    for version in runtime.foundry.datasets.list_versions(config.source_dataset_ref, ctx=ctx):
        if last_number is None or int(version["version_number"]) > last_number:
            return version
    return None


def _events_for_version(
    config: CdcObjectIndexerWorkerConfig,
    runtime: CdcObjectIndexerWorkerRuntime,
    ctx: RequestContext,
    version: DatasetVersionRow,
) -> tuple[ObjectPropertyMap, ...]:
    _validate_version_row_limit(config, version)
    rows = runtime.foundry.datasets.preview(
        config.source_dataset_ref,
        ctx=ctx,
        version=version["id"],
        limit=config.max_rows_per_version,
    )
    return tuple(cast(ObjectPropertyMap, dict(row)) for row in rows)


def _validate_version_row_limit(config: CdcObjectIndexerWorkerConfig, version: DatasetVersionRow) -> None:
    row_count = int(version["row_count"])
    if row_count <= config.max_rows_per_version:
        return
    raise ValidationFailed(
        "CDC object indexer source version exceeds max_rows_per_version",
        details={
            "sourceDatasetRef": config.source_dataset_ref,
            "sourceDatasetVersionId": version["id"],
            "sourceDatasetVersionNumber": version["version_number"],
            "rowCount": row_count,
            "maxRowsPerVersion": config.max_rows_per_version,
        },
    )


def _validate_worker_config(config: CdcObjectIndexerWorkerConfig) -> None:
    if config.max_rows_per_version <= 0:
        raise ValueError("CDC object indexer max_rows_per_version must be positive")


def _batch_result(
    version: DatasetVersionRow,
    events: Sequence[ObjectPropertyMap],
    result: ObjectIndexCdcResult,
) -> CdcObjectIndexBatchResult:
    return CdcObjectIndexBatchResult(
        source_dataset_version_id=version["id"],
        source_dataset_version_number=int(version["version_number"]),
        event_count=len(events),
        index_run_id=str(result["index_run_id"]),
        objects_upserted=int(result["objects_upserted"]),
        objects_deleted=int(result["objects_deleted"]),
        events_skipped=int(result["events_skipped"]),
    )


def _claim_worker_lease(
    config: CdcObjectIndexerWorkerConfig,
    runtime: CdcObjectIndexerWorkerRuntime,
    *,
    ctx: RequestContext,
    lease: _WorkerLease | None = None,
    cursor: Mapping[str, object] | None = None,
) -> _WorkerLease:
    now = _utc_now()
    token = lease.token if lease is not None else f"lease-{uuid4().hex}"
    with runtime.foundry.engine.begin() as transaction:
        existing = runtime.foundry.runtime_repository.workflow_run_by_idempotency(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            workflow_name=_WORKFLOW_NAME,
            idempotency_key=_worker_lease_key(config),
        )
        next_cursor = cursor or _cursor_from_workflow(existing)
        record = _worker_lease_record(config, ctx, token, now, next_cursor)
        row = runtime.foundry.runtime_repository.claim_workflow_run_lease(
            transaction=transaction,
            record=record,
            lease_owner_id=config.worker_id,
            now=_utc_iso(now),
        )
    if row is None:
        raise ConflictDetected(
            "CDC object indexer worker lease is owned by another worker",
            details=_lease_details(config),
        )
    return _WorkerLease(workflow_run_id=row["id"], owner_id=config.worker_id, token=token, cursor=next_cursor)


def _worker_lease_record(
    config: CdcObjectIndexerWorkerConfig,
    ctx: RequestContext,
    lease_token: str,
    now: datetime,
    cursor: Mapping[str, object],
) -> WorkflowRunRecord:
    request = _worker_lease_request(config, ctx)
    return WorkflowRunRecord(
        workflow_run_id=workflow_run_id(request),
        tenant_id=ctx.tenant_id,
        workflow_name=request.workflow_name,
        workflow_profile="cdc-object-indexer-worker",
        status="running",
        idempotency_key=request.idempotency_key,
        request_fingerprint=workflow_request_fingerprint(request),
        input=dict(request.input),
        output=_worker_lease_output(config, lease_token, now, cursor),
        error=None,
        dataset_id=None,
        audit_event_id=None,
        attempts=1,
        created_at=_utc_iso(now),
        started_at=_utc_iso(now),
        completed_at=None,
    )


def _worker_lease_request(config: CdcObjectIndexerWorkerConfig, ctx: RequestContext) -> WorkflowStartRequest:
    return WorkflowStartRequest(
        workflow_name=_WORKFLOW_NAME,
        tenant_id=ctx.tenant_id,
        request_id=ctx.request_id,
        idempotency_key=_worker_lease_key(config),
        input={
            "objectTypeApiName": config.object_type_api_name,
            "sourceDatasetRef": config.source_dataset_ref,
        },
    )


def _worker_lease_output(
    config: CdcObjectIndexerWorkerConfig,
    lease_token: str,
    now: datetime,
    cursor: Mapping[str, object],
) -> dict[str, object]:
    return {
        "workerLease": {
            "ownerId": config.worker_id,
            "leaseToken": lease_token,
            "leaseExpiresAt": _utc_iso(now + timedelta(seconds=config.lease_ttl_seconds)),
            "lastHeartbeatAt": _utc_iso(now),
        },
        "cdcObjectIndexer": _indexer_output(config, cursor),
    }


def _release_worker_lease(
    config: CdcObjectIndexerWorkerConfig,
    runtime: CdcObjectIndexerWorkerRuntime,
    lease: _WorkerLease,
    output: Mapping[str, object],
    status: WorkflowLedgerStatus,
    exc: Exception | None,
) -> None:
    ctx = config.request_context()
    error = runtime_error_payload(exc, ctx, adapter="cdc_object_indexer_worker") if exc is not None else None
    with runtime.foundry.engine.begin() as transaction:
        runtime.foundry.runtime_repository.release_workflow_run_lease(
            transaction=transaction,
            tenant_id=config.tenant_id,
            workflow_run_id=lease.workflow_run_id,
            lease_owner_id=lease.owner_id,
            lease_token=lease.token,
            status=status,
            output=cast(RuntimeRow, dict(output)),
            error=cast(RuntimeRow, error) if error is not None else None,
            completed_at=_utc_iso(_utc_now()),
        )


def _worker_lease_is_current(
    runtime: CdcObjectIndexerWorkerRuntime,
    ctx: RequestContext,
    lease: _WorkerLease,
) -> bool:
    with runtime.foundry.engine.begin() as transaction:
        row = runtime.foundry.runtime_repository.workflow_run_by_id(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            workflow_run_id=lease.workflow_run_id,
        )
    return row is not None and _row_has_worker_lease(row["output"], lease)


def _finish_continuous_result(
    config: CdcObjectIndexerWorkerConfig,
    runtime: CdcObjectIndexerWorkerRuntime,
    lease: _WorkerLease,
    state: _LoopState,
    stop_reason: Literal["empty_polls", "max_batches", "stop_requested"],
) -> ContinuousCdcObjectIndexerResult:
    result = _continuous_result(state, stop_reason, lease)
    status: WorkflowLedgerStatus = "cancelled" if stop_reason == "stop_requested" else "succeeded"
    _release_worker_lease(config, runtime, lease, _continuous_output(config, result, state.cursor or {}), status, None)
    return result


def _continuous_result(
    state: _LoopState,
    stop_reason: Literal["empty_polls", "max_batches", "stop_requested", "lease_lost"],
    lease: _WorkerLease,
) -> ContinuousCdcObjectIndexerResult:
    return ContinuousCdcObjectIndexerResult(
        iterations=state.iterations,
        indexed_batches=state.indexed_batches,
        empty_polls=state.empty_polls,
        events_indexed=state.events_indexed,
        last_version_id=state.last_version_id,
        last_index_run_id=state.last_index_run_id,
        stop_reason=stop_reason,
        workflow_run_id=lease.workflow_run_id,
        lease_owner_id=lease.owner_id,
    )


def _continuous_output(
    config: CdcObjectIndexerWorkerConfig,
    result: ContinuousCdcObjectIndexerResult,
    cursor: Mapping[str, object],
) -> Mapping[str, object]:
    return {
        "stopReason": result.stop_reason,
        "iterations": result.iterations,
        "indexedBatches": result.indexed_batches,
        "emptyPolls": result.empty_polls,
        "eventsIndexed": result.events_indexed,
        "lastVersionId": result.last_version_id,
        "lastIndexRunId": result.last_index_run_id,
        "cdcObjectIndexer": _indexer_output(config, cursor),
    }


def _single_output(
    result: CdcObjectIndexBatchResult | None,
    cursor: Mapping[str, object],
) -> Mapping[str, object]:
    if result is None:
        return {"status": "NO_VERSIONS", "cdcObjectIndexer": {"cursor": dict(cursor)}}
    return {"status": "INDEXED", "batch": _batch_payload(result), "cdcObjectIndexer": {"cursor": dict(cursor)}}


def _indexer_output(config: CdcObjectIndexerWorkerConfig, cursor: Mapping[str, object]) -> dict[str, object]:
    return {
        "objectTypeApiName": config.object_type_api_name,
        "sourceDatasetRef": config.source_dataset_ref,
        "cursor": dict(cursor),
    }


def _cursor_after_result(
    result: CdcObjectIndexBatchResult | None,
    previous: Mapping[str, object],
) -> Mapping[str, object]:
    if result is None:
        return dict(previous)
    return {
        "lastIndexedVersionId": result.source_dataset_version_id,
        "lastIndexedVersionNumber": result.source_dataset_version_number,
        "lastIndexRunId": result.index_run_id,
    }


def _cursor_from_workflow(row: WorkflowRunRow | None) -> Mapping[str, object]:
    if row is None:
        return {}
    output = row.get("output")
    if not isinstance(output, Mapping):
        return {}
    indexer = output.get("cdcObjectIndexer")
    if not isinstance(indexer, Mapping):
        return {}
    cursor = indexer.get("cursor")
    return dict(cursor) if isinstance(cursor, Mapping) else {}


def _row_has_worker_lease(output: Mapping[str, object], lease: _WorkerLease) -> bool:
    raw_lease = output.get("workerLease")
    if not isinstance(raw_lease, Mapping):
        return False
    return raw_lease.get("ownerId") == lease.owner_id and raw_lease.get("leaseToken") == lease.token


def _int_cursor(cursor: Mapping[str, object], key: str) -> int | None:
    value = cursor.get(key)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _max_batches_reached(indexed_batches: int, max_batches: int | None) -> bool:
    return max_batches is not None and indexed_batches >= max_batches


def _batch_payload(result: CdcObjectIndexBatchResult) -> dict[str, object]:
    return {
        "sourceDatasetVersionId": result.source_dataset_version_id,
        "sourceDatasetVersionNumber": result.source_dataset_version_number,
        "eventCount": result.event_count,
        "indexRunId": result.index_run_id,
        "objectsUpserted": result.objects_upserted,
        "objectsDeleted": result.objects_deleted,
        "eventsSkipped": result.events_skipped,
    }


def _batch_result_json(result: CdcObjectIndexBatchResult | None) -> str:
    if result is None:
        return json.dumps({"status": "NO_VERSIONS"}, sort_keys=True)
    payload: dict[str, object] = {"status": "INDEXED"}
    payload.update(_batch_payload(result))
    return json.dumps(payload, sort_keys=True)


def _continuous_result_json(result: ContinuousCdcObjectIndexerResult) -> str:
    payload = {
        "status": "STOPPED",
        "stopReason": result.stop_reason,
        "iterations": result.iterations,
        "indexedBatches": result.indexed_batches,
        "emptyPolls": result.empty_polls,
        "eventsIndexed": result.events_indexed,
        "lastVersionId": result.last_version_id,
        "lastIndexRunId": result.last_index_run_id,
    }
    if result.workflow_run_id is not None:
        payload["workflowRunId"] = result.workflow_run_id
    if result.lease_owner_id is not None:
        payload["leaseOwnerId"] = result.lease_owner_id
    return json.dumps(payload, sort_keys=True)


def _failure_json(exc: Exception, config: CdcObjectIndexerWorkerConfig | None) -> str:
    return json.dumps(_failure_payload(exc, config), sort_keys=True)


def _failure_payload(exc: Exception, config: CdcObjectIndexerWorkerConfig | None) -> Mapping[str, object]:
    if isinstance(exc, ValueError):
        return {"type": "CONFIGURATION_ERROR", "message": str(exc), "details": _failure_trace(config)}
    return runtime_error_payload(exc, _failure_context(config), adapter="cdc_object_indexer_worker")


def _failure_trace(config: CdcObjectIndexerWorkerConfig | None) -> Mapping[str, str]:
    ctx = _failure_context(config)
    if ctx is None:
        return {"adapter": "cdc_object_indexer_worker"}
    return {
        "tenant_id": ctx.tenant_id,
        "actor_user_id": ctx.actor_user_id,
        "request_id": ctx.request_id,
        "correlation_id": ctx.request_id,
        "adapter": "cdc_object_indexer_worker",
    }


def _failure_context(config: CdcObjectIndexerWorkerConfig | None) -> RequestContext | None:
    if config is None:
        return None
    try:
        return config.request_context()
    except ValueError:
        return None


def _config_from_args(args: argparse.Namespace) -> CdcObjectIndexerWorkerConfig:
    return replace(
        config_from_env(),
        object_type_api_name=args.object_type,
        source_dataset_ref=args.source_dataset,
        storage_root=Path(args.storage_root),
        db_url=args.db_url,
        adapter_profile=args.adapter_profile,
        max_rows_per_version=args.max_rows_per_version,
        is_continuous=args.is_continuous,
        continuous_max_batches=args.max_batches,
        continuous_max_empty_polls=args.max_empty_polls,
        worker_id=args.worker_id,
        lease_ttl_seconds=args.lease_ttl_seconds,
    )


def _parser() -> argparse.ArgumentParser:
    defaults = config_from_env()
    parser = argparse.ArgumentParser(description="Index committed CDC dataset versions into ontology objects.")
    parser.add_argument("--object-type", default=defaults.object_type_api_name)
    parser.add_argument("--source-dataset", default=defaults.source_dataset_ref)
    parser.add_argument("--storage-root", default=str(defaults.storage_root))
    parser.add_argument("--db-url", default=defaults.db_url)
    parser.add_argument("--adapter-profile", default=defaults.adapter_profile)
    parser.add_argument("--max-rows-per-version", type=int, default=defaults.max_rows_per_version)
    parser.add_argument("--continuous", action="store_true", default=defaults.is_continuous, dest="is_continuous")
    parser.add_argument("--max-batches", type=int, default=defaults.continuous_max_batches)
    parser.add_argument("--max-empty-polls", type=int, default=defaults.continuous_max_empty_polls)
    parser.add_argument("--worker-id", default=defaults.worker_id)
    parser.add_argument("--lease-ttl-seconds", type=int, default=defaults.lease_ttl_seconds)
    return parser


def _env_bool(values: Mapping[str, str], name: str, is_default_enabled: bool) -> bool:
    raw = values.get(name)
    if raw is None:
        return is_default_enabled
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_optional_int(values: Mapping[str, str], name: str) -> int | None:
    raw = values.get(name)
    if raw is None or raw.strip() == "":
        return None
    return int(raw)


def _required_worker_value(field: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"CDC object indexer worker requires {field}")
    return normalized


def _worker_lease_key(config: CdcObjectIndexerWorkerConfig) -> str:
    return f"cdc-object-indexer:{config.object_type_api_name}:{config.source_dataset_ref}"


def _lease_details(config: CdcObjectIndexerWorkerConfig) -> dict[str, object]:
    return {
        "objectTypeApiName": config.object_type_api_name,
        "sourceDatasetRef": config.source_dataset_ref,
        "workerId": config.worker_id,
    }


def _never_stop() -> bool:
    return False


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


_WORKFLOW_NAME = "ContinuousCdcObjectIndexerWorker"
