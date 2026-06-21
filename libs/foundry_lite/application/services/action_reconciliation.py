from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.action_types import ActionApplyResponse, ActionWritebackReconciliationResult
from foundry_lite.application.ports import (
    ACTION_RUN_RECONCILED,
    ActionRepository,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.ports.action_repository import (
    ActionRunRow,
    ActionWritebackReconciliation,
    ActionWritebackRecord,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.action_mutations import ActionMutationUnitOfWork
from foundry_lite.application.services.action_protocols import (
    ActionObjectIndexer,
    ActionObjectRecordLookup,
    ActionOntologyLookup,
    ActionRuntimeBoundary,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, PermissionDenied, ValidationFailed
from foundry_lite.security.policy import PolicyService


@dataclass(frozen=True)
class ActionWritebackReconciliationWorkflow:
    engine: TransactionManager
    policy: PolicyService
    action_repository: ActionRepository
    object_indexing_service: ActionObjectIndexer
    object_records_service: ActionObjectRecordLookup
    ontology_service: ActionOntologyLookup
    runtime_service: ActionRuntimeBoundary

    def reconcile(
        self,
        writeback_id: str,
        *,
        remote_status: str,
        remote_resource_id: str,
        ctx: RequestContext,
    ) -> ActionWritebackReconciliationResult:
        self._require_operations_retry(ctx, writeback_id)
        _validate_remote_success(remote_status, remote_resource_id)
        with self.engine.begin() as conn:
            writeback = self._required_writeback(conn, ctx, writeback_id)
            if writeback.status == "reconciled":
                return _already_reconciled_result(writeback, remote_status, remote_resource_id)
            action_run = self._required_action_run(conn, ctx, writeback.action_run_id)
            self._require_outcome_unknown(writeback, action_run)
            return self._reconcile_outcome_unknown(
                conn,
                ctx,
                writeback=writeback,
                action_run=action_run,
                remote_status=remote_status,
                remote_resource_id=remote_resource_id,
            )

    def _require_operations_retry(self, ctx: RequestContext, writeback_id: str) -> None:
        try:
            self.policy.require(ctx, "operations:retry")
        except PermissionDenied:
            with self.engine.begin() as conn:
                self.runtime_service._audit(
                    conn,
                    ctx,
                    event_type="permission.denied",
                    resource_type="action_writeback",
                    resource_id=writeback_id,
                    action="operations:retry",
                    decision="deny",
                )
            raise

    def _required_writeback(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        writeback_id: str,
    ) -> ActionWritebackRecord:
        writeback = self.action_repository.action_writeback_by_id(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            writeback_id=writeback_id,
        )
        if writeback is None:
            raise NotFound("action writeback not found", details={"writeback_id": writeback_id})
        return writeback

    def _required_action_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
    ) -> ActionRunRow:
        action_run = self.action_repository.action_run_by_id(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            action_run_id=action_run_id,
        )
        if action_run is None:
            raise NotFound("action run not found", details={"action_run_id": action_run_id})
        return action_run

    def _require_outcome_unknown(self, writeback: ActionWritebackRecord, action_run: ActionRunRow) -> None:
        if writeback.status != "outcome_unknown" or action_run["status"] != "outcome_unknown":
            raise ValidationFailed(
                "action writeback is not outcome-unknown",
                details={
                    "writeback_id": writeback.writeback_id,
                    "writeback_status": writeback.status,
                    "action_status": action_run["status"],
                },
            )

    def _reconcile_outcome_unknown(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        writeback: ActionWritebackRecord,
        action_run: ActionRunRow,
        remote_status: str,
        remote_resource_id: str,
    ) -> ActionWritebackReconciliationResult:
        now = _now()
        response = _reconciled_writeback_response(writeback, remote_status, remote_resource_id, now)
        if not self._try_mark_writeback_reconciled(conn, writeback, response, now):
            return _already_reconciled_result(writeback, remote_status, remote_resource_id)
        mutation = self._commit_reconciled_action(conn, ctx, action_run)
        self._audit_action_reconciled(conn, ctx, action_run["id"], writeback, response, mutation)
        return _reconciled_result(action_run["id"], writeback.writeback_id, remote_status, remote_resource_id, mutation)

    def _try_mark_writeback_reconciled(
        self,
        conn: TransactionContext,
        writeback: ActionWritebackRecord,
        response: Mapping[str, object],
        completed_at: str,
    ) -> bool:
        return self.action_repository.reconcile_action_writeback(
            transaction=conn,
            record=ActionWritebackReconciliation(
                writeback_id=writeback.writeback_id,
                tenant_id=writeback.tenant_id,
                action_run_id=writeback.action_run_id,
                response=response,
                completed_at=completed_at,
            ),
        )

    def _commit_reconciled_action(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run: ActionRunRow,
    ) -> ActionApplyResponse:
        action_type = self.ontology_service._active_action_type(conn, ctx, action_run["action_type_api_name"])
        record = self.object_records_service._object_record(
            conn,
            ctx,
            action_run["target_object_type_api_name"],
            action_run["target_object_id"],
        )
        if record is None:
            raise NotFound("target object not found")
        if record["object_version"] != action_run["expected_object_version"]:
            raise ConflictDetected("object version conflict during reconciliation")
        return self._mutation_unit_of_work().commit(
            conn,
            ctx,
            action_type=action_type,
            action_run_id=action_run["id"],
            record=record,
            params=action_run["parameters"],
            idempotency_key=action_run["idempotency_key"],
            transition=ACTION_RUN_RECONCILED,
        )

    def _audit_action_reconciled(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        writeback: ActionWritebackRecord,
        response: Mapping[str, object],
        mutation: ActionApplyResponse,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="action.run.reconciled",
            resource_type="action_run",
            resource_id=action_run_id,
            action="reconcile",
            before_ref={"writebackStatus": "outcome_unknown", "writebackId": writeback.writeback_id},
            after_ref={**dict(response), "objectEditId": mutation.get("objectEditId")},
            correlation_id=action_run_id,
        )

    def _mutation_unit_of_work(self) -> ActionMutationUnitOfWork:
        return ActionMutationUnitOfWork(
            action_repository=self.action_repository,
            object_indexing_service=self.object_indexing_service,
            runtime_service=self.runtime_service,
            policy=self.policy,
        )


def _validate_remote_success(remote_status: str, remote_resource_id: str) -> None:
    if remote_status != "succeeded":
        raise ValidationFailed(
            "only remote success reconciliation is supported",
            details={"remote_status": remote_status},
        )
    if not remote_resource_id:
        raise ValidationFailed("remote resource id is required")


def _reconciled_writeback_response(
    writeback: ActionWritebackRecord,
    remote_status: str,
    remote_resource_id: str,
    reconciled_at: str,
) -> dict[str, object]:
    response = dict(writeback.response or {})
    return {
        **response,
        "status_code": 200,
        "outcome_unknown": False,
        "reconciled": True,
        "remote_resource_id": remote_resource_id,
        "last_observed_status": remote_status,
        "reconciled_at": reconciled_at,
    }


def _reconciled_result(
    action_run_id: str,
    writeback_id: str,
    remote_status: str,
    remote_resource_id: str,
    mutation: ActionApplyResponse,
) -> ActionWritebackReconciliationResult:
    result: ActionWritebackReconciliationResult = {
        "actionRunId": action_run_id,
        "writebackId": writeback_id,
        "status": "reconciled",
        "remoteStatus": remote_status,
        "remoteResourceId": remote_resource_id,
    }
    if "objectEditId" in mutation:
        result["objectEditId"] = mutation["objectEditId"]
    if "newObjectVersion" in mutation:
        result["newObjectVersion"] = mutation["newObjectVersion"]
    return result


def _already_reconciled_result(
    writeback: ActionWritebackRecord,
    remote_status: str,
    remote_resource_id: str,
) -> ActionWritebackReconciliationResult:
    return {
        "actionRunId": writeback.action_run_id,
        "writebackId": writeback.writeback_id,
        "status": "reconciled",
        "remoteStatus": remote_status,
        "remoteResourceId": remote_resource_id,
        "alreadyReconciled": True,
    }
