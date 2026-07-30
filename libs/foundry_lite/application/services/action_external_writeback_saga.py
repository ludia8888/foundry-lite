"""Transaction-boundary saga for a real before-commit external writeback (L8).

A real external side effect must not hold a pooled DB connection across its remote reach-out -- doing
so keeps a connection busy for the whole vendor round-trip and exhausts the pool under fleet load. So
this saga drives the action through three phases with the connection released across the write:

1. commit the ``received`` run (and all pre-write validation) in its own short transaction;
2. perform the external write with **no** DB connection held;
3. record the receipt together with the CAS-guarded local mutation in a fresh transaction.

Every saga invariant matches the local flow -- idempotent replay (a replay short-circuits in phase 1,
before any write), outcome_unknown (an AMBIGUOUS receipt is never a guaranteed failure), the
compensation path (an external write that LANDED before a failed local commit is recorded, never
silently rolled back), and the atomic object-edit + run + audit + outbox commit -- because the saga
reuses the apply service's replay / validation / mutation helpers through :class:`ActionApplyInternals`.
Only the transaction boundaries around the external write differ.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.action_types import ActionApplyCommand, ActionApplyOutcome, ActionApplyResponse
from foundry_lite.application.ports import (
    ActionTypeRow,
    ObjectRecordRow,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.services.action_workflow import (
    LocalCommitFailed,
    RealExternalWritebackRunner,
    WriteReceipt,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation


@dataclass(frozen=True)
class ResolvedActionTarget:
    """Outcome of the shared ``received`` preamble: a replay to short-circuit on, or a target to act on.

    ``action_type`` is always set (it is resolved first). ``outcome`` short-circuits the flow (a
    replay); otherwise ``record`` is the row-policy-filtered target (``None`` becomes NotFound later).
    """

    action_type: ActionTypeRow
    outcome: ActionApplyOutcome | None = None
    record: ObjectRecordRow | None = None


@dataclass(frozen=True)
class _PreparedExternalWrite:
    """Result of phase 1: either an ``outcome`` that short-circuits before any external reach-out, or
    the durably-committed ``received`` run's ``action_type`` + ``record`` to carry into the write."""

    outcome: ActionApplyOutcome | None = None
    action_type: ActionTypeRow | None = None
    record: ObjectRecordRow | None = None


class ActionApplyInternals(Protocol):
    """The apply-service helpers the saga reuses so replay / validation / commit stay shared logic."""

    engine: TransactionManager

    def _resolve_received_target(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        command: ActionApplyCommand,
        action_run_id: str,
    ) -> ResolvedActionTarget: ...

    def _pre_writeback_error(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        action_type: ActionTypeRow,
        action_run_id: str,
        command: ActionApplyCommand,
        record: ObjectRecordRow | None,
    ) -> ActionApplyOutcome | None: ...

    def _record_rolled_back_action_conflict(
        self,
        ctx: RequestContext,
        command: ActionApplyCommand,
        action_run_id: str,
        action_type: ActionTypeRow,
        error: ConflictDetected,
    ) -> ActionApplyOutcome: ...

    def _fail_action_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        error: Exception,
    ) -> None: ...

    def _commit_local_mutation(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        action_type: ActionTypeRow,
        action_run_id: str,
        command: ActionApplyCommand,
        record: ObjectRecordRow,
    ) -> ActionApplyResponse: ...


@dataclass(frozen=True)
class ExternalWritebackSaga:
    """Drives a real external writeback across three transaction phases (see the module docstring)."""

    service: ActionApplyInternals

    def run(
        self,
        ctx: RequestContext,
        command: ActionApplyCommand,
        action_run_id: str,
        real_runner: RealExternalWritebackRunner,
    ) -> ActionApplyOutcome:
        # Phase 1: commit the 'received' run (and all pre-write validation) in a short transaction. A
        # replay / deferred error / simulated outcome short-circuits here, before any external reach-out,
        # so a replay never issues a blind new external write.
        prepared = self._prepare_received_run(ctx, command, action_run_id)
        if prepared.outcome is not None:
            return prepared.outcome
        if prepared.action_type is None or prepared.record is None:
            raise InvariantViolation("prepared external writeback is missing its target")
        # Phase 2: the external reach-out runs with NO DB connection held; the 'received' run is durable.
        try:
            receipt = real_runner.write_external(command)
        except Exception as exc:
            # A raised write means the side effect definitively did not land (the adapter maps
            # timeouts/connection errors to an AMBIGUOUS receipt instead), so no external success is
            # lost. Mark the durable 'received' run failed rather than leaving it dangling, then surface
            # the original error to the caller.
            self._fail_received_run(ctx, action_run_id, exc)
            raise
        # Phase 3: record the receipt together with the CAS-guarded local mutation in a fresh transaction.
        return self._record_receipt(
            ctx,
            command,
            action_run_id,
            action_type=prepared.action_type,
            record=prepared.record,
            real_runner=real_runner,
            receipt=receipt,
        )

    def _prepare_received_run(
        self,
        ctx: RequestContext,
        command: ActionApplyCommand,
        action_run_id: str,
    ) -> _PreparedExternalWrite:
        resolved: ResolvedActionTarget | None = None
        try:
            with self.service.engine.begin() as conn:
                resolved = self.service._resolve_received_target(conn, ctx, command, action_run_id)
                if resolved.outcome is not None:
                    return _PreparedExternalWrite(outcome=resolved.outcome)
                outcome = self.service._pre_writeback_error(
                    conn,
                    ctx,
                    action_type=resolved.action_type,
                    action_run_id=action_run_id,
                    command=command,
                    record=resolved.record,
                )
                if outcome is not None:
                    return _PreparedExternalWrite(outcome=outcome)
                if resolved.record is None:
                    raise InvariantViolation("action target record disappeared before commit")
                return _PreparedExternalWrite(action_type=resolved.action_type, record=resolved.record)
        except ConflictDetected as exc:
            if resolved is None:
                raise
            # A conflict here rolled back the 'received' insert, so re-insert (or replay the concurrent
            # winner) and record the conflict exactly like the local flow does.
            return _PreparedExternalWrite(
                outcome=self.service._record_rolled_back_action_conflict(
                    ctx, command, action_run_id, resolved.action_type, exc
                )
            )

    def _record_receipt(
        self,
        ctx: RequestContext,
        command: ActionApplyCommand,
        action_run_id: str,
        *,
        action_type: ActionTypeRow,
        record: ObjectRecordRow,
        real_runner: RealExternalWritebackRunner,
        receipt: WriteReceipt,
    ) -> ActionApplyOutcome:
        try:
            with self.service.engine.begin() as conn:

                def commit() -> ActionApplyResponse:
                    return self.service._commit_local_mutation(
                        conn,
                        ctx,
                        action_type=action_type,
                        action_run_id=action_run_id,
                        command=command,
                        record=record,
                    )

                return real_runner.record_receipt_before_commit(
                    conn, ctx, action_run_id, command, receipt, commit=commit
                )
        except LocalCommitFailed as exc:
            # The external write LANDED but the local commit failed: the receipt transaction rolled
            # back, so the run is still 'received'. Record compensation against that durable run in a
            # fresh transaction (never a silent local-only rollback of a live external side effect).
            return self._compensation_required(ctx, command, action_run_id, real_runner, exc.receipt)

    def _fail_received_run(self, ctx: RequestContext, action_run_id: str, error: Exception) -> None:
        with self.service.engine.begin() as conn:
            self.service._fail_action_run(conn, ctx, action_run_id, error)

    def _compensation_required(
        self,
        ctx: RequestContext,
        command: ActionApplyCommand,
        action_run_id: str,
        real_runner: RealExternalWritebackRunner,
        receipt: WriteReceipt,
    ) -> ActionApplyOutcome:
        # The 'received' run is already durable (committed in phase 1), so this records compensation
        # directly against it rather than re-inserting: the CAS received -> compensation_required guards
        # concurrency, and a duplicate request replays the run instead of driving it, so there is no
        # competing writer to lose to here.
        with self.service.engine.begin() as conn:
            error = real_runner.record_compensation_required(conn, ctx, action_run_id, command, receipt)
        return ActionApplyOutcome(deferred_error=error)
