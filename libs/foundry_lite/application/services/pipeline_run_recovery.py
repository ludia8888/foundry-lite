"""Bounded recovery rules for idempotently replayed Pipeline runs."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from uuid import uuid4

from foundry_lite.application.ports.pipeline_repository import PipelineRepository, PipelineRunRow
from foundry_lite.application.ports.transaction_context import TransactionManager
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation

_EXECUTION_LEASE_DURATION = timedelta(minutes=2)
_HEARTBEAT_INTERVAL_SECONDS = 30.0


@dataclass(frozen=True)
class PipelineExecutionLease:
    token: str
    heartbeat_at: str
    expires_at: str


class PipelineTerminalCommitError(RuntimeError):
    """Preserve atomic failure evidence instead of falling back to a split terminal write."""


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
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    row: PipelineRunRow,
) -> bool:
    token = row["execution_lease_token"]
    if token is None:
        return False
    completed_at = _timestamp_text(datetime.now(UTC))
    error = dict(runtime_service._error_payload(stale_pipeline_run_error(row), ctx, run_id=str(row["id"])))
    timeline: list[dict[str, object]] = [
        *row["timeline"],
        {"event": "pipeline.run.failed", "at": _now(), "error": error},
    ]
    with transaction_manager.begin() as transaction:
        after = repository.expire_run_execution(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            run_id=str(row["id"]),
            execution_lease_token=token,
            expired_at=completed_at,
            timeline=timeline,
            error=error,
            completed_at=completed_at,
        )
        if after is not None:
            runtime_service._audit(
                transaction,
                ctx,
                event_type="pipeline.failed",
                resource_type="pipeline_run",
                resource_id=str(row["id"]),
                action="failed",
                after_ref={"error": error},
            )
    return after is not None


@contextmanager
def pipeline_execution_heartbeat(
    transaction_manager: TransactionManager,
    repository: PipelineRepository,
    ctx: RequestContext,
    row: PipelineRunRow,
) -> Iterator[None]:
    stop = Event()
    failures: list[Exception] = []
    thread = Thread(
        target=_renew_execution_lease,
        args=(transaction_manager, repository, ctx, row, stop, failures),
        name=f"pipeline-lease-{row['id']}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
    if failures:
        raise failures[0]


def _renew_execution_lease(
    transaction_manager: TransactionManager,
    repository: PipelineRepository,
    ctx: RequestContext,
    row: PipelineRunRow,
    stop: Event,
    failures: list[Exception],
) -> None:
    token = row["execution_lease_token"]
    if token is None:
        failures.append(InvariantViolation("executing pipeline run is missing its lease token"))
        return
    while not stop.wait(_HEARTBEAT_INTERVAL_SECONDS):
        lease = new_pipeline_execution_lease()
        try:
            with transaction_manager.begin() as transaction:
                renewed = repository.renew_run_execution_lease(
                    transaction=transaction,
                    tenant_id=ctx.tenant_id,
                    run_id=str(row["id"]),
                    execution_lease_token=token,
                    execution_lease_expires_at=lease.expires_at,
                    execution_heartbeat_at=lease.heartbeat_at,
                )
        except Exception as exc:
            failures.append(exc)
            return
        if renewed is None:
            return


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
