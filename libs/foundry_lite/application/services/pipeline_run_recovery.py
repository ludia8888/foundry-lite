"""Bounded recovery rules for idempotently replayed Pipeline runs."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, Lock, Thread
from uuid import uuid4

from foundry_lite.application.ports.dataset_transaction_repository import (
    DatasetTransactionRepository,
    PipelineDatasetCommitRow,
)
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineExecutionRepository,
    PipelineRunArtifactRow,
)
from foundry_lite.application.ports.pipeline_repository import PipelineRepository, PipelineRunRow
from foundry_lite.application.ports.transaction_context import TransactionContext, TransactionManager
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.application.state_transitions import (
    PIPELINE_RUN_FAILED,
    PIPELINE_RUN_PARTIAL,
    StatusTransition,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation
from foundry_lite.security.tenant_context import tenant_context

_EXECUTION_LEASE_DURATION = timedelta(minutes=2)
_HEARTBEAT_INTERVAL_SECONDS = 30.0


@dataclass(frozen=True)
class PipelineExecutionLease:
    token: str
    heartbeat_at: str
    expires_at: str


@dataclass(frozen=True)
class PipelineExpiredExecutionState:
    transition: StatusTransition
    event: str
    output_dataset_ref: str | None
    output_version_id: str | None
    outputs: tuple[dict[str, object], ...]
    error: dict[str, object]


@dataclass(frozen=True)
class RecoveredPipelineOutput:
    node_id: str
    artifact_kind: str
    plane: str
    status: str
    is_serving: bool
    ref: Mapping[str, object]
    evidence_id: str


class PipelineTerminalCommitError(RuntimeError):
    """Preserve atomic failure evidence instead of falling back to a split terminal write."""


class PipelineExecutionLeaseLost(InvariantViolation):
    """Fail closed when an executor can no longer prove ownership of a run."""


class PipelineExecutionLeaseGuard:
    """Renew and fence a run lease at every durable Pipeline write boundary."""

    def __init__(
        self,
        transaction_manager: TransactionManager,
        repository: PipelineRepository,
        ctx: RequestContext,
        row: PipelineRunRow,
    ) -> None:
        token = row["execution_lease_token"]
        if token is None:
            raise PipelineExecutionLeaseLost("executing pipeline run is missing its lease token")
        self._transaction_manager = transaction_manager
        self._repository = repository
        self._ctx = ctx
        self._run_id = str(row["id"])
        self._token = token
        self._failure: PipelineExecutionLeaseLost | None = None
        self._lock = Lock()

    def require_active(self, transaction: TransactionContext | None = None) -> None:
        self.raise_if_failed()
        try:
            if transaction is None:
                with tenant_context(self._ctx.tenant_id), self._transaction_manager.begin() as owned_transaction:
                    self._renew(owned_transaction)
            else:
                self._renew(transaction)
        except PipelineExecutionLeaseLost as exc:
            self.record_failure(exc)
            raise
        except Exception as exc:
            failure = self._lost_error()
            self.record_failure(failure)
            raise failure from exc

    def raise_if_failed(self) -> None:
        with self._lock:
            failure = self._failure
        if failure is not None:
            raise failure

    def record_failure(self, failure: PipelineExecutionLeaseLost) -> None:
        with self._lock:
            if self._failure is None:
                self._failure = failure

    def _renew(self, transaction: TransactionContext) -> None:
        lease = new_pipeline_execution_lease()
        renewed = self._repository.renew_run_execution_lease(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            run_id=self._run_id,
            execution_lease_token=self._token,
            execution_lease_expires_at=lease.expires_at,
            execution_heartbeat_at=lease.heartbeat_at,
        )
        if renewed is None:
            raise self._lost_error()

    def _lost_error(self) -> PipelineExecutionLeaseLost:
        return PipelineExecutionLeaseLost(
            "pipeline execution lease was lost before commit",
            details={"run_id": self._run_id},
        )


def replayed_pipeline_run_action(row: Mapping[str, object]) -> str:
    if row.get("status") == "running":
        return "execute"
    return "fail_stale" if is_stale_pipeline_execution(row) else "read"


def new_pipeline_execution_lease(*, now: datetime | None = None) -> PipelineExecutionLease:
    heartbeat = (now or datetime.now(UTC)).astimezone(UTC)
    return PipelineExecutionLease(
        token=uuid4().hex,
        heartbeat_at=_timestamp_text(heartbeat),
        expires_at=_timestamp_text(heartbeat + _EXECUTION_LEASE_DURATION),
    )


def stale_pipeline_run_error(row: Mapping[str, object]) -> InvariantViolation:
    return InvariantViolation(
        "expired pipeline execution lease was recovered as terminal failure",
        details={
            "run_id": row.get("id"),
            "execution_lease_expires_at": row.get("execution_lease_expires_at"),
        },
    )


def is_stale_pipeline_execution(row: Mapping[str, object], *, now: datetime | None = None) -> bool:
    if row.get("status") != "executing":
        return False
    token = row.get("execution_lease_token")
    expires_at = row.get("execution_lease_expires_at")
    if not isinstance(token, str) or not token or not isinstance(expires_at, str) or not expires_at:
        return False
    return _timestamp(expires_at) <= (now or datetime.now(UTC))


def expire_stale_pipeline_run(
    transaction_manager: TransactionManager,
    repository: PipelineRepository,
    execution_repository: PipelineExecutionRepository,
    dataset_transaction_repository: DatasetTransactionRepository,
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    row: PipelineRunRow,
) -> bool:
    token = row["execution_lease_token"]
    if token is None:
        return False
    completed_at = _timestamp_text(datetime.now(UTC))
    with transaction_manager.begin() as transaction:
        state = _expired_execution_state(
            transaction,
            execution_repository,
            dataset_transaction_repository,
            runtime_service,
            ctx,
            row,
        )
        timeline = _expired_timeline(row, state)
        after = repository.expire_run_execution(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            run_id=str(row["id"]),
            execution_lease_token=token,
            expired_at=completed_at,
            transition=state.transition,
            output_dataset_ref=state.output_dataset_ref,
            output_version_id=state.output_version_id,
            outputs=list(state.outputs),
            timeline=timeline,
            error=state.error,
            completed_at=completed_at,
        )
        if after is not None:
            _audit_expired_execution(runtime_service, transaction, ctx, row, state)
    return after is not None


def _expired_timeline(
    row: PipelineRunRow,
    state: PipelineExpiredExecutionState,
) -> list[dict[str, object]]:
    return [*row["timeline"], {"event": state.event, "at": _now(), "error": state.error}]


def _expired_execution_state(
    transaction: TransactionContext,
    repository: PipelineExecutionRepository,
    dataset_transaction_repository: DatasetTransactionRepository,
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    row: PipelineRunRow,
) -> PipelineExpiredExecutionState:
    outputs_found = _committed_outputs(transaction, repository, dataset_transaction_repository, ctx, row)
    if not outputs_found:
        error = dict(runtime_service._error_payload(stale_pipeline_run_error(row), ctx, run_id=str(row["id"])))
        return PipelineExpiredExecutionState(PIPELINE_RUN_FAILED, "pipeline.run.failed", None, None, (), error)
    outputs = tuple(_reconciled_output(output) for output in outputs_found)
    dataset_ref, version_id = _single_serving_dataset_fields(outputs_found)
    error = dict(runtime_service._error_payload(_reconciliation_error(row, outputs_found), ctx, run_id=str(row["id"])))
    return PipelineExpiredExecutionState(
        PIPELINE_RUN_PARTIAL,
        "pipeline.run.reconciliation_required",
        dataset_ref,
        version_id,
        outputs,
        error,
    )


def _committed_outputs(
    transaction: TransactionContext,
    repository: PipelineExecutionRepository,
    dataset_transaction_repository: DatasetTransactionRepository,
    ctx: RequestContext,
    row: PipelineRunRow,
) -> list[RecoveredPipelineOutput]:
    node_rows = repository.node_runs_for_run(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        run_id=str(row["id"]),
    )
    output_node_ids = {item["node_id"] for item in node_rows if item["descriptor_id"].startswith("output.")}
    artifacts = [
        _artifact_output(item)
        for item in repository.artifacts_for_run(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            run_id=str(row["id"]),
        )
        if item["node_id"] in output_node_ids and item["status"] == "COMMITTED"
    ]
    transactions = dataset_transaction_repository.committed_pipeline_output_transactions(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        pipeline_run_id=str(row["id"]),
    )
    recovered = _missing_dataset_outputs(transactions, output_node_ids, {_output_key(item) for item in artifacts})
    return [*artifacts, *recovered]


def _missing_dataset_outputs(
    rows: list[PipelineDatasetCommitRow],
    output_node_ids: set[str],
    seen: set[tuple[str, str, str | None]],
) -> list[RecoveredPipelineOutput]:
    recovered: list[RecoveredPipelineOutput] = []
    for row in rows:
        output = _dataset_commit_output(row, output_node_ids)
        if output is not None and _output_key(output) not in seen:
            recovered.append(output)
            seen.add(_output_key(output))
    return recovered


def _artifact_output(artifact: PipelineRunArtifactRow) -> RecoveredPipelineOutput:
    return RecoveredPipelineOutput(
        node_id=artifact["node_id"],
        artifact_kind=artifact["artifact_kind"],
        plane=artifact["plane"],
        status=artifact["status"],
        is_serving=artifact["is_serving"],
        ref=artifact["artifact_ref"],
        evidence_id=artifact["id"],
    )


def _dataset_commit_output(
    row: PipelineDatasetCommitRow,
    output_node_ids: set[str],
) -> RecoveredPipelineOutput | None:
    node_id = _optional_text(row["metadata"].get("pipelineNodeId"))
    if node_id is None or node_id not in output_node_ids:
        return None
    return RecoveredPipelineOutput(
        node_id=node_id,
        artifact_kind="dataset_version",
        plane="dataset",
        status="COMMITTED",
        is_serving=True,
        ref={
            "datasetRef": row["dataset_ref"],
            "versionId": row["version_id"],
            "transactionId": row["transaction_id"],
        },
        evidence_id=row["transaction_id"],
    )


def _output_key(output: RecoveredPipelineOutput) -> tuple[str, str, str | None]:
    return output.node_id, output.artifact_kind, _optional_text(output.ref.get("versionId"))


def _reconciled_output(artifact: RecoveredPipelineOutput) -> dict[str, object]:
    return {
        "nodeId": artifact.node_id,
        "artifactKind": artifact.artifact_kind,
        "plane": artifact.plane,
        "status": artifact.status,
        "commitKind": "SERVING_ASSET" if artifact.is_serving else "GOVERNED_CANDIDATE",
        "isServing": artifact.is_serving,
        "ref": dict(artifact.ref),
    }


def _single_serving_dataset_fields(
    artifacts: list[RecoveredPipelineOutput],
) -> tuple[str | None, str | None]:
    matches = [
        artifact for artifact in artifacts if artifact.artifact_kind == "dataset_version" and artifact.is_serving
    ]
    if len(matches) != 1:
        return None, None
    ref = matches[0].ref
    dataset_ref = ref.get("datasetRef")
    version_id = ref.get("versionId")
    return _optional_text(dataset_ref), _optional_text(version_id)


def _reconciliation_error(
    row: PipelineRunRow,
    artifacts: list[RecoveredPipelineOutput],
) -> InvariantViolation:
    return InvariantViolation(
        "pipeline outputs committed but terminal evidence requires reconciliation",
        details={
            "run_id": row["id"],
            "commit_evidence_ids": [artifact.evidence_id for artifact in artifacts],
            "execution_lease_expires_at": row["execution_lease_expires_at"],
        },
    )


def _audit_expired_execution(
    runtime_service: RuntimeEvidenceBoundary,
    transaction: TransactionContext,
    ctx: RequestContext,
    row: PipelineRunRow,
    state: PipelineExpiredExecutionState,
) -> None:
    is_reconciliation = state.transition.to_status == "partial"
    action = "reconciliation_required" if is_reconciliation else "failed"
    runtime_service._audit(
        transaction,
        ctx,
        event_type=f"pipeline.{action}",
        resource_type="pipeline_run",
        resource_id=str(row["id"]),
        action=action,
        after_ref={"outputs": list(state.outputs), "error": state.error},
    )


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


@contextmanager
def pipeline_execution_heartbeat(
    transaction_manager: TransactionManager,
    repository: PipelineRepository,
    ctx: RequestContext,
    row: PipelineRunRow,
) -> Iterator[PipelineExecutionLeaseGuard]:
    stop = Event()
    guard = PipelineExecutionLeaseGuard(transaction_manager, repository, ctx, row)
    thread = Thread(
        target=_renew_execution_lease,
        args=(guard, stop),
        name=f"pipeline-lease-{row['id']}",
        daemon=True,
    )
    thread.start()
    try:
        yield guard
    finally:
        stop.set()
        thread.join()
    guard.raise_if_failed()


def _renew_execution_lease(
    guard: PipelineExecutionLeaseGuard,
    stop: Event,
) -> None:
    while not stop.wait(_HEARTBEAT_INTERVAL_SECONDS):
        try:
            guard.require_active()
        except PipelineExecutionLeaseLost:
            return


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
