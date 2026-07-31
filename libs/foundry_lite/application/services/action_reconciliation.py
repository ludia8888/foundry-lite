"""Application service helpers for action reconciliation workflows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.action_types import (
    ActionApplyResponse,
    ActionWritebackQueueResult,
    ActionWritebackReconciliationResult,
    ActionWritebackRecoveryItem,
    ActionWritebackRecoveryResult,
)
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
from foundry_lite.application.ports.external_writeback_adapter import (
    ExternalWritebackAdapter,
    ExternalWriteTarget,
    RemoteOutcomeStatus,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.action_external_pending_recovery import ExternalPendingRecovery
from foundry_lite.application.services.action_mutations import ActionMutationUnitOfWork
from foundry_lite.application.services.action_protocols import (
    ActionObjectIndexer,
    ActionObjectRecordLookup,
    ActionOntologyLookup,
    ActionRuntimeBoundary,
)
from foundry_lite.application.services.action_reconciliation_helpers import (
    UNRESOLVED_WRITEBACK_STATUSES,
    action_run_has_sensitive_parameters,
    already_reconciled_result,
    approval_required_recovery_item,
    external_writeback_uri,
    failed_recovery_item,
    is_resolvable_writeback,
    queue_item,
    queue_statuses,
    reconciled_recovery_item,
    reconciled_result,
    reconciled_writeback_response,
    recovery_result,
    skipped_recovery_item,
    validate_queue_limit,
    validate_remote_success,
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
    external_writeback_adapter: ExternalWritebackAdapter | None = None

    def list_unresolved(
        self,
        *,
        ctx: RequestContext,
        status: str | None = None,
        limit: int = 50,
    ) -> ActionWritebackQueueResult:
        self._require_operations_read(ctx)
        statuses = queue_statuses(status)
        validate_queue_limit(limit)
        with self.engine.begin() as conn:
            rows = self.action_repository.list_action_writebacks(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                statuses=statuses,
                limit=limit,
            )
        sensitive = self.policy.sensitive_column_names(ctx)
        return {"items": [queue_item(row, sensitive) for row in rows]}

    def reconcile(
        self,
        writeback_id: str,
        *,
        remote_status: str | None = None,
        remote_resource_id: str | None = None,
        external_writeback_uri: str | None = None,
        ctx: RequestContext,
    ) -> ActionWritebackReconciliationResult:
        self._require_operations_retry(ctx, writeback_id)
        status, resource_id = self._resolve_remote_outcome(
            remote_status, remote_resource_id, external_writeback_uri, writeback_id, ctx
        )
        validate_remote_success(status, resource_id)
        with self.engine.begin() as conn:
            writeback = self._required_writeback(conn, ctx, writeback_id)
            if writeback.status == "reconciled":
                return already_reconciled_result(writeback, status, resource_id)
            action_run = self._required_action_run(conn, ctx, writeback.action_run_id)
            if not is_resolvable_writeback(writeback, action_run):
                current = self._required_writeback(conn, ctx, writeback_id)
                if current.status == "reconciled":
                    return already_reconciled_result(current, status, resource_id)
            self._require_outcome_unknown(writeback, action_run)
            return self._reconcile_outcome_unknown(
                conn,
                ctx,
                writeback=writeback,
                action_run=action_run,
                remote_status=status,
                remote_resource_id=resource_id,
            )

    def recover_unresolved(
        self,
        *,
        ctx: RequestContext,
        limit: int = 50,
    ) -> ActionWritebackRecoveryResult:
        self._require_operations_retry(ctx, "action_writeback_recovery")
        validate_queue_limit(limit)
        # Snapshot the unresolved writebacks BEFORE resolving external-pending runs, so an
        # external-pending run that this tick moves to outcome_unknown is picked up by the NEXT batch
        # (the worker loops until empty) rather than double-processed within this tick.
        writeback_rows = self._unresolved_rows(ctx, limit)
        pending_items = self._external_pending_recovery().recover(ctx, limit)
        writeback_items = [self._recover_one(ctx, row) for row in writeback_rows]
        result = recovery_result(pending_items + writeback_items)
        self._audit_recovery_tick(ctx, result)
        return result

    def _external_pending_recovery(self) -> ExternalPendingRecovery:
        return ExternalPendingRecovery(
            engine=self.engine,
            policy=self.policy,
            action_repository=self.action_repository,
            object_indexing_service=self.object_indexing_service,
            object_records_service=self.object_records_service,
            ontology_service=self.ontology_service,
            runtime_service=self.runtime_service,
            external_writeback_adapter=self.external_writeback_adapter,
        )

    def approve_recovery(
        self,
        writeback_id: str,
        *,
        approval_id: str,
        reason: str,
        external_writeback_uri: str | None = None,
        ctx: RequestContext,
    ) -> ActionWritebackRecoveryItem:
        self._require_operations_retry(ctx, writeback_id)
        approval_id = _required_approval_text("approval_id", approval_id)
        reason = _required_approval_text("reason", reason)
        writeback, policy_reason = self._approval_required_writeback(ctx, writeback_id)
        self._audit_recovery_approved(ctx, writeback, approval_id, reason, policy_reason)
        item = self._recover_one(
            ctx,
            writeback,
            is_approval_released=True,
            external_writeback_uri_override=external_writeback_uri,
        )
        item["approvalId"] = approval_id
        item["approvalReason"] = policy_reason
        return item

    def _unresolved_rows(self, ctx: RequestContext, limit: int) -> list[ActionWritebackRecord]:
        with self.engine.begin() as conn:
            return self.action_repository.list_action_writebacks(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                statuses=UNRESOLVED_WRITEBACK_STATUSES,
                limit=limit,
            )

    def _recover_one(
        self,
        ctx: RequestContext,
        writeback: ActionWritebackRecord,
        *,
        is_approval_released: bool = False,
        external_writeback_uri_override: str | None = None,
    ) -> ActionWritebackRecoveryItem:
        if writeback.status == "retryable":
            return skipped_recovery_item(writeback, "retryable_external_not_changed")
        approval_reason = None if is_approval_released else self._operator_approval_reason(ctx, writeback)
        if approval_reason is not None:
            return approval_required_recovery_item(writeback, approval_reason)
        uri = external_writeback_uri_override or external_writeback_uri(writeback)
        if uri is None:
            return skipped_recovery_item(writeback, "missing_external_writeback_uri")
        try:
            result = self.reconcile(writeback.writeback_id, external_writeback_uri=uri, ctx=ctx)
        except Exception as exc:
            return failed_recovery_item(writeback, exc)
        return reconciled_recovery_item(writeback, result)

    def _operator_approval_reason(self, ctx: RequestContext, writeback: ActionWritebackRecord) -> str | None:
        sensitive = self.policy.sensitive_column_names(ctx)
        if not sensitive:
            return None
        with self.engine.begin() as conn:
            action_run = self._required_action_run(conn, ctx, writeback.action_run_id)
        if action_run_has_sensitive_parameters(action_run, sensitive):
            return "sensitive_action_parameter"
        return None

    def _approval_required_writeback(
        self,
        ctx: RequestContext,
        writeback_id: str,
    ) -> tuple[ActionWritebackRecord, str]:
        with self.engine.begin() as conn:
            writeback = self._required_writeback(conn, ctx, writeback_id)
            action_run = self._required_action_run(conn, ctx, writeback.action_run_id)
        if not is_resolvable_writeback(writeback, action_run):
            raise ValidationFailed(
                "action writeback recovery is not approval-recoverable",
                details={
                    "writeback_id": writeback_id,
                    "writeback_status": writeback.status,
                    "action_run_status": action_run["status"],
                },
            )
        sensitive = self.policy.sensitive_column_names(ctx)
        policy_reason = None
        if sensitive and action_run_has_sensitive_parameters(action_run, sensitive):
            policy_reason = "sensitive_action_parameter"
        if policy_reason is None:
            raise ValidationFailed(
                "action writeback recovery does not require operator approval",
                details={"writeback_id": writeback_id, "writeback_status": writeback.status},
            )
        return writeback, policy_reason

    def _audit_recovery_approved(
        self,
        ctx: RequestContext,
        writeback: ActionWritebackRecord,
        approval_id: str,
        reason: str,
        policy_reason: str,
    ) -> None:
        with self.engine.begin() as conn:
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="action.writeback.recovery_approved",
                resource_type="action_writeback",
                resource_id=writeback.writeback_id,
                action="approve_recovery",
                after_ref={
                    "approvalId": approval_id,
                    "reason": reason,
                    "policyReason": policy_reason,
                    "actionRunId": writeback.action_run_id,
                    "writebackStatus": writeback.status,
                    "approvedBy": ctx.actor_user_id,
                },
                correlation_id=writeback.action_run_id,
            )

    def _audit_recovery_tick(self, ctx: RequestContext, result: ActionWritebackRecoveryResult) -> None:
        with self.engine.begin() as conn:
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="action.writeback.recovery_tick",
                resource_type="action_writeback_recovery",
                resource_id=ctx.request_id,
                action="recover",
                after_ref=dict(result),
                correlation_id=ctx.request_id,
            )

    def _resolve_remote_outcome(
        self,
        remote_status: str | None,
        remote_resource_id: str | None,
        external_writeback_uri: str | None,
        writeback_id: str,
        ctx: RequestContext,
    ) -> tuple[str, str]:
        """Operator-provided remote_status wins; otherwise HEAD the real external system."""
        if remote_status is not None:
            return remote_status, remote_resource_id or ""
        if external_writeback_uri is None or self.external_writeback_adapter is None:
            raise ValidationFailed(
                "reconciliation requires an operator remote_status or a real external writeback lookup",
                details={"writeback_id": writeback_id},
            )
        return self._lookup_remote_outcome(external_writeback_uri, writeback_id, ctx)

    def _lookup_remote_outcome(
        self,
        external_writeback_uri: str,
        writeback_id: str,
        ctx: RequestContext,
    ) -> tuple[str, str]:
        adapter = self.external_writeback_adapter
        assert adapter is not None  # guaranteed by caller  # nosec B101
        with self.engine.begin() as conn:
            writeback = self._required_writeback(conn, ctx, writeback_id)
        if writeback.status == "retryable":
            raise ValidationFailed(
                "retryable writeback is not resolvable by reconciliation",
                details={"writeback_id": writeback_id, "writeback_status": writeback.status},
            )
        outcome = adapter.remote_lookup(
            ExternalWriteTarget(uri=external_writeback_uri, idempotency_key=writeback.idempotency_key)
        )
        if outcome.status is not RemoteOutcomeStatus.LANDED:
            raise ValidationFailed(
                "real remote lookup did not find the external write landed",
                details={"writeback_id": writeback_id, "remote_status": outcome.status.value},
            )
        return "succeeded", outcome.remote_resource_id or ""

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

    def _require_operations_read(self, ctx: RequestContext) -> None:
        self.policy.require(ctx, "operations:read:summary")

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
        # Both an outcome_unknown writeback (a timeout that may have landed) and a compensation_required
        # writeback (external success + local failure) are unresolved before-commit external side effects
        # that a remote-success reconciliation drives forward to a committed local mutation.
        if not is_resolvable_writeback(writeback, action_run):
            raise ValidationFailed(
                "action writeback is not resolvable by reconciliation",
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
        response = reconciled_writeback_response(writeback, remote_status, remote_resource_id, now)
        if not self._try_mark_writeback_reconciled(conn, writeback, response, now):
            current = self._required_writeback(conn, ctx, writeback.writeback_id)
            return already_reconciled_result(current, remote_status, remote_resource_id)
        mutation = self._commit_reconciled_action(conn, ctx, action_run)
        self._audit_action_reconciled(conn, ctx, action_run["id"], writeback, response, mutation)
        return reconciled_result(action_run["id"], writeback.writeback_id, remote_status, remote_resource_id, mutation)

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
        action_type = self.ontology_service._action_type_by_id(conn, ctx, action_run["action_type_id"])
        record = self.object_records_service._object_record(
            conn,
            ctx,
            action_run["target_object_type_api_name"],
            action_run["target_object_id"],
            object_type_id=action_run["target_object_type_id"],
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
            before_ref={"writebackStatus": writeback.status, "writebackId": writeback.writeback_id},
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


def _required_approval_text(field: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValidationFailed("approval recovery field is required", details={"field": field})
    return normalized
