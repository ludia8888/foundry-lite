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
)
from foundry_lite.application.ports.media_repository import (
    MediaRepository,
)
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineExecutionRepository,
)
from foundry_lite.application.ports.pipeline_repository import PipelineRepository, PipelineRunRow
from foundry_lite.application.ports.transaction_context import TransactionContext, TransactionManager
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.pipeline_run_output_recovery import (
    RecoveredPipelineOutput,
    committed_pipeline_outputs,
    reconciled_pipeline_output,
    single_serving_dataset_fields,
)
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
    if is_stale_pipeline_execution(row):
        return "fail_stale"
    if row.get("status") == "running":
        return "execute"
    return "read"


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
    if row.get("status") not in {"running", "executing"}:
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
    media_repository: MediaRepository,
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    row: PipelineRunRow,
) -> bool:
    token = row["execution_lease_token"]
    if token is None:
        return False
    with transaction_manager.begin() as transaction:
        state = _expired_execution_state(
            transaction,
            execution_repository,
            dataset_transaction_repository,
            media_repository,
            runtime_service,
            ctx,
            row,
        )
        return _persist_expired_pipeline_state(
            transaction,
            repository,
            runtime_service,
            ctx,
            row,
            token,
            state,
        )


def _persist_expired_pipeline_state(
    transaction: TransactionContext,
    repository: PipelineRepository,
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    row: PipelineRunRow,
    token: str,
    state: PipelineExpiredExecutionState,
) -> bool:
    completed_at = _timestamp_text(datetime.now(UTC))
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
        timeline=_expired_timeline(row, state),
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
    media_repository: MediaRepository,
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    row: PipelineRunRow,
) -> PipelineExpiredExecutionState:
    outputs_found = committed_pipeline_outputs(
        transaction,
        repository,
        dataset_transaction_repository,
        media_repository,
        ctx,
        str(row["id"]),
    )
    if not outputs_found:
        error = dict(runtime_service._error_payload(stale_pipeline_run_error(row), ctx, run_id=str(row["id"])))
        return PipelineExpiredExecutionState(PIPELINE_RUN_FAILED, "pipeline.run.failed", None, None, (), error)
    outputs = tuple(reconciled_pipeline_output(output) for output in outputs_found)
    dataset_ref, version_id = single_serving_dataset_fields(outputs_found)
    error = dict(runtime_service._error_payload(_reconciliation_error(row, outputs_found), ctx, run_id=str(row["id"])))
    return PipelineExpiredExecutionState(
        PIPELINE_RUN_PARTIAL,
        "pipeline.run.reconciliation_required",
        dataset_ref,
        version_id,
        outputs,
        error,
    )


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
