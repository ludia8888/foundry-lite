"""Recovery sweep for stranded ``external_pending`` action runs.

An action with a real external side effect commits a NON-terminal ``external_pending`` write-ahead marker
before the external write (so the DB connection is released for the network call). If the process crashes
between that commit and the phase-3 resolve, the run is left committed as ``external_pending`` with no
terminal writeback. This sweep HEADs the external system via the idempotent ``remote_lookup`` and drives
each stranded run to a terminal (or deferred) state:

- ``LANDED`` -> apply the local mutation and succeed;
- ``ABSENT`` -> fail (the external write never landed);
- ``AMBIGUOUS`` -> record ``outcome_unknown`` and hand it to the existing writeback sweep;
- a transient/unreachable lookup (raises) -> leave it ``external_pending``, recoverable on a later tick.
"""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.action_types import ActionWritebackRecoveryItem
from foundry_lite.application.ports import (
    ACTION_RUN_FAILED,
    ACTION_RUN_SUCCEEDED,
    ObjectRecordRow,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.ports.action_repository import ActionRepository, ActionRunRow
from foundry_lite.application.ports.external_writeback_adapter import (
    ExternalWritebackAdapter,
    ExternalWriteTarget,
    RemoteOutcome,
    RemoteOutcomeStatus,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.action_mutations import ActionMutationUnitOfWork
from foundry_lite.application.services.action_protocols import (
    ActionObjectIndexer,
    ActionObjectRecordLookup,
    ActionOntologyLookup,
    ActionRuntimeBoundary,
)
from foundry_lite.application.services.action_reconciliation_helpers import (
    external_pending_failed_item,
    external_pending_reconciled_item,
    external_pending_skipped_item,
)
from foundry_lite.application.services.action_writebacks import ActionWritebackRecorder
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound
from foundry_lite.security.policy import PolicyService


@dataclass(frozen=True)
class ExternalPendingRecovery:
    """Resolves stranded ``external_pending`` runs against the real external system by HEAD lookup."""

    engine: TransactionManager
    policy: PolicyService
    action_repository: ActionRepository
    object_indexing_service: ActionObjectIndexer
    object_records_service: ActionObjectRecordLookup
    ontology_service: ActionOntologyLookup
    runtime_service: ActionRuntimeBoundary
    external_writeback_adapter: ExternalWritebackAdapter | None = None

    def recover(self, ctx: RequestContext, limit: int) -> list[ActionWritebackRecoveryItem]:
        with self.engine.begin() as conn:
            runs = self.action_repository.list_action_runs_by_status(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                statuses=("external_pending",),
                limit=limit,
            )
        return [self._recover_one(ctx, run) for run in runs]

    def _recover_one(self, ctx: RequestContext, run: ActionRunRow) -> ActionWritebackRecoveryItem:
        adapter = self.external_writeback_adapter
        uri = run["external_writeback_uri"]
        if adapter is None:
            return external_pending_skipped_item(run, "missing_external_writeback_adapter")
        if not uri:
            return external_pending_skipped_item(run, "missing_external_writeback_uri")
        try:
            outcome = adapter.remote_lookup(ExternalWriteTarget(uri=uri, idempotency_key=run["idempotency_key"]))
            with self.engine.begin() as conn:
                current = self._required_action_run(conn, ctx, run["id"])
                if current["status"] != "external_pending":
                    return external_pending_skipped_item(run, "already_resolved")
                return self._apply_outcome(conn, ctx, current, outcome)
        except Exception as exc:  # noqa: BLE001 - a failed lookup/commit leaves the run recoverable next tick
            return external_pending_failed_item(run, exc)

    def _apply_outcome(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run: ActionRunRow,
        outcome: RemoteOutcome,
    ) -> ActionWritebackRecoveryItem:
        if outcome.status is RemoteOutcomeStatus.LANDED:
            return self._recover_landed(conn, ctx, run, outcome)
        if outcome.status is RemoteOutcomeStatus.ABSENT:
            return self._recover_absent(conn, ctx, run)
        return self._recover_ambiguous(conn, ctx, run)

    def _recover_landed(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run: ActionRunRow,
        outcome: RemoteOutcome,
    ) -> ActionWritebackRecoveryItem:
        resource_id = outcome.remote_resource_id or ""
        writeback_id = self._recorder().record(
            conn,
            ctx,
            run["id"],
            status="succeeded",
            idempotency_key=run["idempotency_key"],
            request_hash=run["request_fingerprint"],
            response={"status_code": 200, "remote_resource_id": resource_id},
            external_writeback_uri=run["external_writeback_uri"],
        )
        action_type = self.ontology_service._action_type_by_id(conn, ctx, run["action_type_id"])
        record = self._required_target_record(conn, ctx, run)
        self._mutation_unit_of_work().commit(
            conn,
            ctx,
            action_type=action_type,
            action_run_id=run["id"],
            record=record,
            params=run["parameters"],
            idempotency_key=run["idempotency_key"],
            transition=ACTION_RUN_SUCCEEDED,
        )
        self._audit(conn, ctx, run, "succeeded", writeback_id)
        return external_pending_reconciled_item(run, "succeeded", resource_id)

    def _recover_absent(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run: ActionRunRow,
    ) -> ActionWritebackRecoveryItem:
        writeback_id = self._recorder().record(
            conn,
            ctx,
            run["id"],
            status="failed",
            idempotency_key=run["idempotency_key"],
            request_hash=run["request_fingerprint"],
            response={"status_code": 404, "remote_status": "absent"},
            external_writeback_uri=run["external_writeback_uri"],
        )
        updated = self.action_repository.update_action_run_terminal(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            action_run_id=run["id"],
            transition=ACTION_RUN_FAILED,
            error={"code": "external_write_absent", "message": "external write did not land"},
            completed_at=_now(),
        )
        if not updated:
            raise ConflictDetected("action run state changed concurrently during recovery")
        self._audit(conn, ctx, run, "failed", writeback_id)
        return external_pending_reconciled_item(run, "absent", "")

    def _recover_ambiguous(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run: ActionRunRow,
    ) -> ActionWritebackRecoveryItem:
        # A still-ambiguous HEAD leaves the run in outcome_unknown for the writeback sweep to finish
        # (external_writeback_uri travels on the recorded writeback request), never a guaranteed failure.
        self._recorder().outcome_unknown_before_commit(
            conn,
            ctx,
            run["id"],
            run["idempotency_key"],
            run["request_fingerprint"],
            external_writeback_uri=run["external_writeback_uri"],
        )
        return external_pending_skipped_item(run, "still_outcome_unknown")

    def _required_target_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run: ActionRunRow,
    ) -> ObjectRecordRow:
        record = self.object_records_service._object_record(
            conn,
            ctx,
            run["target_object_type_api_name"],
            run["target_object_id"],
            object_type_id=run["target_object_type_id"],
        )
        if record is None:
            raise NotFound("target object not found")
        if record["object_version"] != run["expected_object_version"]:
            raise ConflictDetected("object version conflict during external-pending recovery")
        return record

    def _required_action_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
    ) -> ActionRunRow:
        run = self.action_repository.action_run_by_id(
            transaction=conn, tenant_id=ctx.tenant_id, action_run_id=action_run_id
        )
        if run is None:
            raise NotFound("action run not found", details={"action_run_id": action_run_id})
        return run

    def _recorder(self) -> ActionWritebackRecorder:
        adapter = self.external_writeback_adapter
        assert adapter is not None  # nosec B101 - guarded by _recover_one
        return ActionWritebackRecorder(
            action_repository=self.action_repository,
            runtime_service=self.runtime_service,
            connector_id=adapter.profile_name,
            is_simulated=False,
        )

    def _mutation_unit_of_work(self) -> ActionMutationUnitOfWork:
        return ActionMutationUnitOfWork(
            action_repository=self.action_repository,
            object_indexing_service=self.object_indexing_service,
            runtime_service=self.runtime_service,
            policy=self.policy,
        )

    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run: ActionRunRow,
        resolution: str,
        writeback_id: str,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="action.run.external_pending_recovered",
            resource_type="action_run",
            resource_id=run["id"],
            action="recover",
            after_ref={"resolution": resolution, "writebackId": writeback_id},
            correlation_id=run["id"],
        )
