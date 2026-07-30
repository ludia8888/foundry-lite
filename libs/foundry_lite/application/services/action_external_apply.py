"""External-writeback apply orchestration (L8: real before-commit external side effect).

An action with a real external writeback cannot hold the DB connection across the vendor round-trip.
This orchestrator splits the apply into three phases around a durable, NON-terminal ``external_pending``
write-ahead marker:

1. **Phase 1 (txn A):** run the received-state preamble, then CAS ``received -> external_pending`` and
   commit — releasing the connection.
2. **Phase 2 (no txn):** ``adapter.write()``.
3. **Phase 3 (txn B, then C on local failure):** re-read the run (replay-if-already-resolved guard) and
   map the receipt — AMBIGUOUS -> ``outcome_unknown``, LANDED + local-ok -> ``succeeded``, LANDED +
   local-fail -> ``compensation_required`` (fresh txn).

A crash between the phase-1 commit and phase 3 leaves a committed ``external_pending`` run for the worker
recovery sweep. The shared received-state helpers (replay, validation, mutation commit) live on the
``ActionApplyService`` and are reused here through the ``service`` reference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from foundry_lite.application.action_types import ActionApplyCommand, ActionApplyOutcome
from foundry_lite.application.ports import (
    ACTION_RUN_EXTERNAL_PENDING,
    ActionRunRow,
    ActionTypeRow,
    ObjectRecordRow,
    TransactionContext,
)
from foundry_lite.application.ports.external_writeback_adapter import RemoteOutcomeStatus
from foundry_lite.application.services.action_helpers import require_action_target_api_name
from foundry_lite.application.services.action_workflow import (
    LocalCommitFailed,
    RealExternalWritebackRunner,
    WriteReceipt,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation, NotFound

if TYPE_CHECKING:
    from foundry_lite.application.services.action_apply_service import ActionApplyService


@dataclass(frozen=True)
class _ExternalPendingPrep:
    """Phase-1 result. ``outcome`` short-circuits when the call was fully handled inside the write-ahead
    transaction (idempotent replay, request/version failure, simulated-flag error, or a concurrent
    conflict). Otherwise the run has been committed into ``external_pending`` and ``action_type`` carries
    forward to the phase-3 resolve."""

    outcome: ActionApplyOutcome | None = None
    action_type: ActionTypeRow | None = None


@dataclass(frozen=True)
class ExternalActionApply:
    """Drives the three-phase real external-writeback apply, delegating shared helpers to the service."""

    service: ActionApplyService

    def run(
        self,
        ctx: RequestContext,
        command: ActionApplyCommand,
        action_run_id: str,
        runner: RealExternalWritebackRunner,
    ) -> ActionApplyOutcome:
        prep = self._prepare(ctx, command, action_run_id)
        if prep.outcome is not None:
            return prep.outcome
        assert prep.action_type is not None  # nosec B101 - set whenever outcome is None
        receipt = runner.write(command)
        return self._resolve(ctx, command, action_run_id, prep.action_type, runner, receipt)

    def _prepare(
        self,
        ctx: RequestContext,
        command: ActionApplyCommand,
        action_run_id: str,
    ) -> _ExternalPendingPrep:
        """Phase 1 (txn A): run the received-state preamble, then CAS ``received -> external_pending``."""
        service = self.service
        action_type_for_failure: ActionTypeRow | None = None
        try:
            with service.engine.begin() as conn:
                action_type = service.ontology_service._active_action_type(conn, ctx, command.action_api_name)
                action_type_for_failure = action_type
                require_action_target_api_name(action_type, command.object_type)
                replay = service._replay_or_none(conn, ctx, action_type, action_run_id, command)
                if replay is not None:
                    return _ExternalPendingPrep(outcome=replay)
                record = service._visible_target_record(conn, ctx, action_type, command)
                deferred_error = service._action_request_error(
                    ctx, action_type, record, command.expected_object_version, command.params
                )
                if deferred_error is not None:
                    service._fail_action_run(conn, ctx, action_run_id, deferred_error)
                    return _ExternalPendingPrep(outcome=ActionApplyOutcome(deferred_error=deferred_error))
                simulated = service.action_writeback_service.writeback_recorder().simulated_before_commit_error(
                    conn, ctx, action_run_id, command
                )
                if simulated is not None:
                    return _ExternalPendingPrep(outcome=ActionApplyOutcome(deferred_error=simulated))
                if record is None:
                    raise InvariantViolation("action target record disappeared before commit")
                self._mark_pending(conn, ctx, action_run_id, command)
                return _ExternalPendingPrep(action_type=action_type)
        except ConflictDetected as exc:
            if action_type_for_failure is None:
                raise
            outcome = service._record_rolled_back_action_conflict(
                ctx, command, action_run_id, action_type_for_failure, exc
            )
            return _ExternalPendingPrep(outcome=outcome)

    def _mark_pending(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        command: ActionApplyCommand,
    ) -> None:
        """Commit the write-ahead marker: ``received -> external_pending`` (no completion timestamp yet)."""
        updated = self.service.action_repository.update_action_run_terminal(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            action_run_id=action_run_id,
            transition=ACTION_RUN_EXTERNAL_PENDING,
            error=None,
            completed_at=None,
        )
        if not updated:
            raise ConflictDetected(
                "action run state changed concurrently before external write", details={"run_id": action_run_id}
            )
        self.service.runtime_service._audit(
            conn,
            ctx,
            event_type="action.run.external_pending",
            resource_type="action_run",
            resource_id=action_run_id,
            action="apply",
            after_ref={"externalWritebackUri": command.external_writeback_uri},
            correlation_id=action_run_id,
        )

    def _resolve(
        self,
        ctx: RequestContext,
        command: ActionApplyCommand,
        action_run_id: str,
        action_type: ActionTypeRow,
        runner: RealExternalWritebackRunner,
        receipt: WriteReceipt,
    ) -> ActionApplyOutcome:
        """Phase 3: map the write receipt onto the committed ``external_pending`` run."""
        if receipt.status is RemoteOutcomeStatus.AMBIGUOUS:
            return self._resolve_ambiguous(ctx, command, action_run_id, runner)
        return self._resolve_landed(ctx, command, action_run_id, action_type, runner, receipt)

    def _resolve_ambiguous(
        self,
        ctx: RequestContext,
        command: ActionApplyCommand,
        action_run_id: str,
        runner: RealExternalWritebackRunner,
    ) -> ActionApplyOutcome:
        """AMBIGUOUS: the write may have landed, so record ``outcome_unknown`` (never a guaranteed failure)."""
        with self.service.engine.begin() as conn:
            replay = self._replay_if_resolved(conn, ctx, action_run_id, command)
            if replay is not None:
                return replay
            error = runner.record_outcome_unknown(conn, ctx, action_run_id, command)
        return ActionApplyOutcome(deferred_error=error)

    def _resolve_landed(
        self,
        ctx: RequestContext,
        command: ActionApplyCommand,
        action_run_id: str,
        action_type: ActionTypeRow,
        runner: RealExternalWritebackRunner,
        receipt: WriteReceipt,
    ) -> ActionApplyOutcome:
        """LANDED: commit the local mutation; any local failure requires compensation (external is live)."""
        try:
            with self.service.engine.begin() as conn:
                run = self._require_pending_run(conn, ctx, action_run_id)
                replay = self._replay_if_resolved_run(conn, ctx, run, command)
                if replay is not None:
                    return replay
                runner.record_succeeded(conn, ctx, action_run_id, command, receipt)
                try:
                    record = self._pending_target(conn, ctx, run)
                    response = self.service._mutation_unit_of_work().commit(
                        conn,
                        ctx,
                        action_type=action_type,
                        action_run_id=action_run_id,
                        record=record,
                        params=command.params,
                        idempotency_key=command.idempotency_key,
                    )
                except Exception as exc:
                    raise LocalCommitFailed(receipt) from exc
            return ActionApplyOutcome(response=response)
        except LocalCommitFailed as exc:
            return self._record_compensation(ctx, command, action_run_id, runner, exc.receipt)

    def _record_compensation(
        self,
        ctx: RequestContext,
        command: ActionApplyCommand,
        action_run_id: str,
        runner: RealExternalWritebackRunner,
        receipt: WriteReceipt,
    ) -> ActionApplyOutcome:
        """LANDED + local failure: record ``compensation_required`` in a fresh transaction."""
        with self.service.engine.begin() as conn:
            run = self._require_pending_run(conn, ctx, action_run_id)
            replay = self._replay_if_resolved_run(conn, ctx, run, command)
            if replay is not None:
                return replay
            error = runner.record_compensation_required(conn, ctx, action_run_id, command, receipt)
        return ActionApplyOutcome(deferred_error=error)

    def _pending_target(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run: ActionRunRow,
    ) -> ObjectRecordRow:
        record = self.service.object_records_service._object_record(
            conn,
            ctx,
            run["target_object_type_api_name"],
            run["target_object_id"],
            object_type_id=run["target_object_type_id"],
        )
        if record is None:
            raise NotFound("target object not found")
        if record["object_version"] != run["expected_object_version"]:
            raise ConflictDetected("object version conflict after external write landed")
        return record

    def _replay_if_resolved(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        command: ActionApplyCommand,
    ) -> ActionApplyOutcome | None:
        run = self._require_pending_run(conn, ctx, action_run_id)
        return self._replay_if_resolved_run(conn, ctx, run, command)

    def _replay_if_resolved_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run: ActionRunRow,
        command: ActionApplyCommand,
    ) -> ActionApplyOutcome | None:
        """If a concurrent duplicate or the recovery sweep already moved the run out of ``external_pending``,
        return that resolved run as an idempotent replay instead of resolving the same write twice."""
        if run["status"] != "external_pending":
            return self.service._replay_existing_action_run(conn, ctx, run, command.request_fingerprint)
        return None

    def _require_pending_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
    ) -> ActionRunRow:
        run = self.service.action_repository.action_run_by_id(
            transaction=conn, tenant_id=ctx.tenant_id, action_run_id=action_run_id
        )
        if run is None:
            raise InvariantViolation(
                "external-pending action run disappeared before resolve", details={"run_id": action_run_id}
            )
        return run
