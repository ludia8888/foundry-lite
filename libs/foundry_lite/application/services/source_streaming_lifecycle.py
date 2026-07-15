"""Durable control-plane lifecycle for long-running streaming Source syncs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from foundry_lite.application.ports import (
    RuntimeRepository,
    SourceManagementRepository,
    TransactionContext,
    WorkflowRunRow,
    WorkflowStartRequest,
)
from foundry_lite.application.ports.transaction_context import WORKFLOW_RUN_CANCELLED
from foundry_lite.application.services.source_management_helpers import SourceRuntimeBoundary, now
from foundry_lite.application.services.source_streaming_health import streaming_health_view
from foundry_lite.application.services.workflow_orchestration_helpers import (
    require_same_workflow_request,
    workflow_record,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed

SOURCE_STREAMING_WORKFLOW_NAME = "SourceStreamingSyncWorkflow"
SOURCE_STREAMING_WORKFLOW_VERSION = "source_streaming_sync.workflow/v1"
SOURCE_STREAMING_WORKFLOW_PROFILE = "source-streaming-worker"
_ACTIVE_STATUSES = frozenset({"requested", "starting", "running", "start_unknown"})
_WORKER_ASSIGNMENT_GRACE = timedelta(seconds=30)


@dataclass(frozen=True)
class _StreamingStatusSnapshot:
    workflow_run_id: str | None
    workflow_name: str
    status: str
    output: Mapping[str, object]
    lease: Mapping[str, object]
    telemetry: Mapping[str, object]
    error: Mapping[str, object] | None
    started_at: str | None
    completed_at: str | None
    created_at: str | None
    operation_path: str | None


@dataclass(frozen=True)
class SourceStreamingLifecycleDependencies:
    runtime_repository: RuntimeRepository
    source_management_repository: SourceManagementRepository
    runtime_service: SourceRuntimeBoundary


def start_streaming_sync(
    dependencies: SourceStreamingLifecycleDependencies,
    conn: TransactionContext,
    ctx: RequestContext,
    sync: Mapping[str, object],
    idempotency_key: str,
) -> dict[str, object]:
    _require_streaming_kafka_sync(sync)
    _require_dataset_branch_available(dependencies, conn, ctx, sync)
    active = _active_workflow(dependencies, conn, ctx, sync)
    if active is not None:
        return streaming_sync_status_view(sync, active)
    request = _streaming_request(ctx, sync, idempotency_key)
    record = workflow_record(ctx, request, SOURCE_STREAMING_WORKFLOW_PROFILE, None)
    existing = dependencies.runtime_repository.insert_workflow_run_or_get_existing(transaction=conn, record=record)
    row = existing or _workflow_row(dependencies, conn, ctx, record.workflow_run_id)
    require_same_workflow_request(row, request)
    if existing is None:
        _link_sync_workflow(dependencies, conn, ctx, sync, row)
        _audit_start(dependencies, conn, ctx, sync, row)
    return streaming_sync_status_view(sync, row)


def stop_streaming_sync(
    dependencies: SourceStreamingLifecycleDependencies,
    conn: TransactionContext,
    ctx: RequestContext,
    sync: Mapping[str, object],
) -> dict[str, object]:
    _require_streaming_kafka_sync(sync)
    row = _linked_workflow(dependencies, conn, ctx, sync)
    if row is None:
        return streaming_sync_status_view(sync, None)
    if row["status"] not in _ACTIVE_STATUSES:
        return streaming_sync_status_view(sync, row)
    updated = dependencies.runtime_repository.update_workflow_run_status(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        workflow_run_id=row["id"],
        transition=WORKFLOW_RUN_CANCELLED,
        output=_stop_requested_output(row),
        error=None,
        started_at=None,
        completed_at=now(),
    )
    terminal = updated or _workflow_row(dependencies, conn, ctx, row["id"])
    _audit_stop(dependencies, conn, ctx, sync, terminal)
    return streaming_sync_status_view(sync, terminal)


def streaming_sync_status(
    dependencies: SourceStreamingLifecycleDependencies,
    conn: TransactionContext,
    ctx: RequestContext,
    sync: Mapping[str, object],
) -> dict[str, object]:
    _require_streaming_kafka_sync(sync)
    return streaming_sync_status_view(sync, _linked_workflow(dependencies, conn, ctx, sync))


def streaming_sync_status_view(
    sync: Mapping[str, object],
    row: WorkflowRunRow | None,
    *,
    observed_at: datetime | None = None,
) -> dict[str, object]:
    observed = observed_at or datetime.now(UTC)
    snapshot = _streaming_status_snapshot(row)
    is_worker_stale = _is_worker_stale(snapshot.status, snapshot.lease, observed, snapshot.created_at)
    health = streaming_health_view(
        sync,
        snapshot.telemetry,
        is_worker_stale=is_worker_stale,
        is_active=snapshot.status in _ACTIVE_STATUSES,
        observed_at=observed,
    )
    lifecycle_state = _health_lifecycle(_lifecycle_state(snapshot.status, snapshot.output, is_worker_stale), health)
    return {
        "syncName": sync["sync_name"],
        "sourceName": sync["source_name"],
        "workflowRunId": snapshot.workflow_run_id,
        "workflowName": snapshot.workflow_name,
        "status": snapshot.status,
        "desiredState": snapshot.output.get("desiredState", _desired_state(snapshot.status)),
        "lifecycleState": lifecycle_state,
        "isWorkerStale": is_worker_stale,
        "workerLease": snapshot.lease,
        "telemetry": snapshot.telemetry,
        "health": health,
        "error": snapshot.error,
        "startedAt": snapshot.started_at,
        "completedAt": snapshot.completed_at,
        "operationPath": snapshot.operation_path,
    }


def _streaming_status_snapshot(row: WorkflowRunRow | None) -> _StreamingStatusSnapshot:
    if row is None:
        return _StreamingStatusSnapshot(
            workflow_run_id=None,
            workflow_name=SOURCE_STREAMING_WORKFLOW_NAME,
            status="idle",
            output={},
            lease={},
            telemetry={},
            error=None,
            started_at=None,
            completed_at=None,
            created_at=None,
            operation_path=None,
        )
    output = dict(row["output"])
    error = dict(row["error"]) if row["error"] is not None else None
    return _StreamingStatusSnapshot(
        workflow_run_id=row["id"],
        workflow_name=row["workflow_name"],
        status=str(row["status"]),
        output=output,
        lease=_mapping(output.get("workerLease")),
        telemetry=_mapping(output.get("telemetry")),
        error=error,
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        created_at=row["created_at"],
        operation_path=f"/api/operations/runs/workflow/{row['id']}",
    )


def _streaming_request(ctx: RequestContext, sync: Mapping[str, object], idempotency_key: str) -> WorkflowStartRequest:
    return WorkflowStartRequest(
        workflow_name=SOURCE_STREAMING_WORKFLOW_NAME,
        tenant_id=ctx.tenant_id,
        request_id=ctx.request_id,
        idempotency_key=idempotency_key,
        input={
            "workflowKind": "source_streaming_sync",
            "workflowDefinitionVersion": SOURCE_STREAMING_WORKFLOW_VERSION,
            "syncName": sync["sync_name"],
            "sourceName": sync["source_name"],
            "configFingerprint": sync["config_fingerprint"],
            "targetDatasetRef": sync.get("target_dataset_ref"),
        },
    )


def _active_workflow(
    dependencies: SourceStreamingLifecycleDependencies,
    conn: TransactionContext,
    ctx: RequestContext,
    sync: Mapping[str, object],
) -> WorkflowRunRow | None:
    row = _linked_workflow(dependencies, conn, ctx, sync)
    return row if row is not None and row["status"] in _ACTIVE_STATUSES else None


def _require_dataset_branch_available(
    dependencies: SourceStreamingLifecycleDependencies,
    conn: TransactionContext,
    ctx: RequestContext,
    sync: Mapping[str, object],
) -> None:
    target = sync.get("target_dataset_ref")
    if not isinstance(target, str) or not target:
        return
    for candidate in dependencies.source_management_repository.list_syncs(tenant_id=ctx.tenant_id):
        if candidate["sync_name"] == sync["sync_name"] or candidate.get("target_dataset_ref") != target:
            continue
        row = _linked_workflow(dependencies, conn, ctx, candidate)
        if row is not None and row["status"] in _ACTIVE_STATUSES:
            raise ConflictDetected(
                "another streaming sync is already active on the target dataset branch",
                details={
                    "targetDatasetRef": target,
                    "activeSyncName": candidate["sync_name"],
                    "requestedSyncName": sync["sync_name"],
                    "workflowRunId": row["id"],
                },
            )


def _linked_workflow(
    dependencies: SourceStreamingLifecycleDependencies,
    conn: TransactionContext,
    ctx: RequestContext,
    sync: Mapping[str, object],
) -> WorkflowRunRow | None:
    workflow_run_id = sync.get("last_workflow_run_id")
    if not isinstance(workflow_run_id, str) or not workflow_run_id:
        return None
    return dependencies.runtime_repository.workflow_run_by_id(
        transaction=conn, tenant_id=ctx.tenant_id, workflow_run_id=workflow_run_id
    )


def _workflow_row(
    dependencies: SourceStreamingLifecycleDependencies,
    conn: TransactionContext,
    ctx: RequestContext,
    workflow_run_id: str,
) -> WorkflowRunRow:
    row = dependencies.runtime_repository.workflow_run_by_id(
        transaction=conn, tenant_id=ctx.tenant_id, workflow_run_id=workflow_run_id
    )
    if row is None:
        raise NotFound("source streaming workflow not found", details={"workflowRunId": workflow_run_id})
    return row


def _link_sync_workflow(
    dependencies: SourceStreamingLifecycleDependencies,
    conn: TransactionContext,
    ctx: RequestContext,
    sync: Mapping[str, object],
    row: WorkflowRunRow,
) -> None:
    updated = dependencies.source_management_repository.update_sync_streaming_workflow(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        sync_name=str(sync["sync_name"]),
        workflow_run_id=row["id"],
        updated_at=now(),
    )
    if updated is None:
        raise NotFound("source sync not found", details={"syncName": sync["sync_name"]})


def _audit_start(
    dependencies: SourceStreamingLifecycleDependencies,
    conn: TransactionContext,
    ctx: RequestContext,
    sync: Mapping[str, object],
    row: WorkflowRunRow,
) -> None:
    dependencies.runtime_service._audit(
        conn,
        ctx,
        event_type="source.streaming_sync.start_requested",
        resource_type="source_sync",
        resource_id=str(sync["sync_name"]),
        action="source_manage",
        after_ref=streaming_sync_status_view(sync, row),
        correlation_id=row["id"],
    )


def _audit_stop(
    dependencies: SourceStreamingLifecycleDependencies,
    conn: TransactionContext,
    ctx: RequestContext,
    sync: Mapping[str, object],
    row: WorkflowRunRow,
) -> None:
    dependencies.runtime_service._audit(
        conn,
        ctx,
        event_type="source.streaming_sync.stop_requested",
        resource_type="source_sync",
        resource_id=str(sync["sync_name"]),
        action="source_manage",
        after_ref=streaming_sync_status_view(sync, row),
        correlation_id=row["id"],
    )


def _stop_requested_output(row: WorkflowRunRow) -> dict[str, object]:
    output = dict(row["output"])
    output.update({"desiredState": "STOPPED", "lifecycleState": "STOP_REQUESTED", "stopRequestedAt": now()})
    return output


def _require_streaming_kafka_sync(sync: Mapping[str, object]) -> None:
    if sync.get("source_type") == "kafka" and sync.get("capability") == "streaming":
        return
    raise ValidationFailed(
        "continuous lifecycle is only available for Kafka streaming syncs",
        details={"syncName": sync.get("sync_name"), "sourceType": sync.get("source_type")},
    )


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _desired_state(status: str) -> str:
    return "RUNNING" if status in _ACTIVE_STATUSES else "STOPPED"


def _lifecycle_state(status: str, output: Mapping[str, object], is_worker_stale: bool) -> str:
    if status == "idle":
        return "IDLE"
    if status == "cancelled":
        return str(output.get("lifecycleState", "STOPPED"))
    if status == "failed":
        return "FAILED"
    if is_worker_stale:
        return "UNHEALTHY"
    return str(output.get("lifecycleState", "STARTING"))


def _health_lifecycle(lifecycle_state: str, health: Mapping[str, object]) -> str:
    health_status = health.get("status")
    if lifecycle_state != "RUNNING":
        return lifecycle_state
    if health_status == "UNHEALTHY":
        return "UNHEALTHY"
    if health_status == "DEGRADED":
        return "DEGRADED"
    return lifecycle_state


def _is_worker_stale(
    status: str,
    lease: Mapping[str, object],
    observed_at: datetime,
    created_at: str | None,
) -> bool:
    if status not in _ACTIVE_STATUSES:
        return False
    if not lease:
        return status == "running" or _assignment_grace_expired(created_at, observed_at)
    expires_at = lease.get("leaseExpiresAt")
    if not isinstance(expires_at, str):
        return status == "running"
    try:
        return datetime.fromisoformat(expires_at.replace("Z", "+00:00")) < observed_at
    except ValueError:
        return True


def _assignment_grace_expired(created_at: str | None, observed_at: datetime) -> bool:
    if not created_at:
        return False
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return created + _WORKER_ASSIGNMENT_GRACE < observed_at
