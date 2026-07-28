"""Renewable lease and caller-context recovery for durable Pipeline previews."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, Lock, Thread
from uuid import uuid4

from foundry_lite.application.ports.metadata_repository import MetadataRepository
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineExecutionRepository,
    PipelinePreviewRunRow,
)
from foundry_lite.application.ports.transaction_context import TransactionContext, TransactionManager
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation
from foundry_lite.security.tenant_context import tenant_context

_PREVIEW_LEASE_DURATION = timedelta(minutes=2)
_PREVIEW_HEARTBEAT_INTERVAL_SECONDS = 30.0


@dataclass(frozen=True)
class PipelinePreviewExecutionLease:
    """A renewable ownership token and its UTC heartbeat/expiry timestamps."""

    token: str
    heartbeat_at: str
    expires_at: str


class PipelinePreviewExecutionLeaseLost(InvariantViolation):
    """Fail closed when a preview executor can no longer prove ownership."""


class PipelinePreviewRecoveryCursor:
    """Rotate the first tenant across bounded recovery passes."""

    def __init__(self) -> None:
        self._next_tenant_id: str | None = None
        self._lock = Lock()

    def ordered_tenant_ids(self, tenant_ids: Sequence[str]) -> tuple[str, ...]:
        tenants = tuple(dict.fromkeys(tenant_ids))
        if not tenants:
            return ()
        with self._lock:
            start = tenants.index(self._next_tenant_id) if self._next_tenant_id in tenants else 0
            ordered = tenants[start:] + tenants[:start]
            self._next_tenant_id = ordered[1 % len(ordered)]
        return ordered


class PipelinePreviewExecutionLeaseGuard:
    """Renew a preview lease and remember heartbeat failure across threads."""

    def __init__(
        self,
        transaction_manager: TransactionManager,
        repository: PipelineExecutionRepository,
        ctx: RequestContext,
        row: PipelinePreviewRunRow,
    ) -> None:
        token = row["execution_lease_token"]
        if token is None:
            raise PipelinePreviewExecutionLeaseLost("executing pipeline preview is missing its lease token")
        self._transaction_manager = transaction_manager
        self._repository = repository
        self._ctx = ctx
        self._preview_run_id = str(row["id"])
        self._token = token
        self._failure: PipelinePreviewExecutionLeaseLost | None = None
        self._lock = Lock()

    @property
    def token(self) -> str:
        """Return the immutable token fencing this executor."""
        return self._token

    def require_active(self, transaction: TransactionContext | None = None) -> None:
        """Renew the lease or fail closed when ownership cannot be proven."""
        self.raise_if_failed()
        try:
            if transaction is None:
                with tenant_context(self._ctx.tenant_id), self._transaction_manager.begin() as owned_transaction:
                    self._renew(owned_transaction)
            else:
                self._renew(transaction)
        except PipelinePreviewExecutionLeaseLost as exc:
            self.record_failure(exc)
            raise
        except Exception as exc:
            failure = self._lost_error()
            self.record_failure(failure)
            raise failure from exc

    def raise_if_failed(self) -> None:
        """Propagate a heartbeat-thread failure on the caller thread."""
        with self._lock:
            failure = self._failure
        if failure is not None:
            raise failure

    def record_failure(self, failure: PipelinePreviewExecutionLeaseLost) -> None:
        """Keep the first ownership failure for deterministic propagation."""
        with self._lock:
            if self._failure is None:
                self._failure = failure

    def _renew(self, transaction: TransactionContext) -> None:
        lease = new_pipeline_preview_execution_lease()
        renewed = self._repository.renew_preview_execution_lease(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            preview_run_id=self._preview_run_id,
            execution_lease_token=self._token,
            execution_lease_expires_at=lease.expires_at,
            execution_heartbeat_at=lease.heartbeat_at,
        )
        if renewed is None:
            raise self._lost_error()

    def _lost_error(self) -> PipelinePreviewExecutionLeaseLost:
        return PipelinePreviewExecutionLeaseLost(
            "pipeline preview execution lease was lost before terminal evidence",
            details={"preview_run_id": self._preview_run_id},
        )


def new_pipeline_preview_execution_lease(
    *,
    now: datetime | None = None,
) -> PipelinePreviewExecutionLease:
    """Create a fresh two-minute preview execution lease."""
    heartbeat = (now or datetime.now(UTC)).astimezone(UTC)
    return PipelinePreviewExecutionLease(
        token=uuid4().hex,
        heartbeat_at=_timestamp_text(heartbeat),
        expires_at=_timestamp_text(heartbeat + _PREVIEW_LEASE_DURATION),
    )


def pipeline_preview_utc_now() -> str:
    """Return a fixed-width UTC timestamp safe for persisted lexical comparison."""
    return _timestamp_text(datetime.now(UTC))


def recoverable_pipeline_previews(
    transaction_manager: TransactionManager,
    repository: PipelineExecutionRepository,
    metadata_repository: MetadataRepository,
    recovery_cursor: PipelinePreviewRecoveryCursor,
    *,
    as_of: str,
    limit: int,
) -> list[PipelinePreviewRunRow]:
    """Scan a rotating, fairly shared tenant batch inside each RLS context."""
    remaining = max(1, min(limit, 100))
    rows: list[PipelinePreviewRunRow] = []
    tenant_ids = recovery_cursor.ordered_tenant_ids(metadata_repository.list_tenant_ids())
    for index, tenant_id in enumerate(tenant_ids):
        tenant_limit = max(1, remaining // (len(tenant_ids) - index))
        with tenant_context(tenant_id), transaction_manager.begin() as transaction:
            tenant_rows = repository.recoverable_previews(
                transaction=transaction,
                as_of=as_of,
                limit=tenant_limit,
            )
        rows.extend(tenant_rows)
        remaining -= len(tenant_rows)
        if remaining == 0:
            break
    return rows


def pipeline_preview_lease_claim_values() -> dict[str, str]:
    """Build repository values for the initial queued-to-running claim."""
    lease = new_pipeline_preview_execution_lease()
    return {
        "started_at": lease.heartbeat_at,
        "execution_lease_token": lease.token,
        "execution_lease_expires_at": lease.expires_at,
        "execution_heartbeat_at": lease.heartbeat_at,
    }


def pipeline_preview_lease_reclaim_values() -> dict[str, str]:
    """Build repository values for reclaiming an expired running preview."""
    lease = new_pipeline_preview_execution_lease()
    return {
        "reclaim_before": lease.heartbeat_at,
        "execution_lease_token": lease.token,
        "execution_lease_expires_at": lease.expires_at,
        "execution_heartbeat_at": lease.heartbeat_at,
    }


def pipeline_preview_execution_context(ctx: RequestContext) -> dict[str, object]:
    """Persist the caller fields needed to authorize restart recovery."""
    return {
        "actorUserId": ctx.actor_user_id,
        "roles": list(ctx.roles),
        "applicationId": ctx.application_id,
        "clientId": ctx.client_id,
        "tokenScopes": list(ctx.token_scopes),
    }


def recovered_pipeline_preview_context(row: PipelinePreviewRunRow) -> RequestContext:
    """Rebuild caller authorization, with a least-privileged legacy fallback."""
    payload = row["execution_context"]
    roles = _string_tuple(payload.get("roles"))
    return RequestContext(
        tenant_id=str(row["tenant_id"]),
        actor_user_id=_string_or(payload.get("actorUserId"), str(row["created_by"])),
        request_id=f"req-pipeline-preview-recovery:{row['id']}",
        roles=roles or ("data_engineer",),
        application_id=_optional_string(payload.get("applicationId")),
        client_id=_optional_string(payload.get("clientId")),
        token_scopes=_string_tuple(payload.get("tokenScopes")),
    )


@contextmanager
def pipeline_preview_execution_heartbeat(
    transaction_manager: TransactionManager,
    repository: PipelineExecutionRepository,
    ctx: RequestContext,
    row: PipelinePreviewRunRow,
) -> Iterator[PipelinePreviewExecutionLeaseGuard]:
    """Run a background heartbeat and surface ownership loss to the executor."""
    stop = Event()
    guard = PipelinePreviewExecutionLeaseGuard(transaction_manager, repository, ctx, row)
    thread = Thread(
        target=_renew_preview_execution_lease,
        args=(guard, stop),
        name=f"pipeline-preview-lease-{row['id']}",
        daemon=True,
    )
    thread.start()
    try:
        yield guard
    finally:
        stop.set()
        thread.join()
    guard.raise_if_failed()


def _renew_preview_execution_lease(
    guard: PipelinePreviewExecutionLeaseGuard,
    stop: Event,
) -> None:
    while not stop.wait(_PREVIEW_HEARTBEAT_INTERVAL_SECONDS):
        try:
            guard.require_active()
        except PipelinePreviewExecutionLeaseLost:
            return


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _string_or(value: object, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
