"""Worker-side execution of one fenced Action function/commit step."""

from __future__ import annotations

from foundry_lite.application.services.action_attempt_heartbeat import ActionAttemptHeartbeat
from foundry_lite.application.services.action_distributed_contracts import (
    ActionAsyncRunRow,
    ActionDefinitionV3,
    ActionExecutionRepository,
    ActionFunctionExecutionResult,
    ActionFunctionExecutor,
    ActionObjectIndexer,
    ActionObjectRecordLookup,
    ActionOsdkScopeBoundary,
    ActionRepository,
    ActionRunDispatchRequest,
    ActionRunRetryableFailure,
    ActionRuntimeBoundary,
    ActionStepAttemptRow,
    ConflictDetected,
    EditPlan,
    MetadataRepository,
    OntologyLookupService,
    RequestContext,
    TransactionContext,
    action_contract_fingerprint,
)
from foundry_lite.application.services.action_distributed_effects import (
    ActionBeforeEffectOutcomeUnknown,
    ActionEffectDeliveryService,
    ConnectorRegistryRepository,
    authorize_action_effects,
)
from foundry_lite.application.services.action_distributed_run_evidence import (
    ACTION_RUN_TERMINAL_STATUSES,
    action_error_kind,
    action_has_function,
    action_retry_at,
    append_action_attempt_event,
    append_action_run_event,
    is_action_error_retryable,
)
from foundry_lite.application.services.action_distributed_run_support import (
    ActionStepLeaseLost,
    action_attempt_claim,
    action_function_request,
    action_plan_committer,
    action_success_output,
    action_worker_context,
    action_worker_lease,
    append_worker_attempt_event,
    complete_action_attempt,
    load_action_run,
    mark_action_commit_pending,
    persist_terminal_action_failure,
    require_action_attempt_owner,
    require_stored_plan_hash,
    required_action_run,
    stored_action_contract,
    stored_edit_plan,
    utc_now,
)
from foundry_lite.application.services.action_edit_plan_results import ActionEditPlanResult, plan_summary
from foundry_lite.application.services.action_permission_guards import (
    require_action_permission,
    require_action_target_read,
)
from foundry_lite.application.services.action_plan_authorization import authorize_action_edit_plan
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.state_transitions import (
    ACTION_RUN_ASYNC_CANCELLED,
    ACTION_RUN_ASYNC_CONFLICT,
    ACTION_RUN_ASYNC_FAILED,
    ACTION_RUN_ASYNC_OUTCOME_UNKNOWN,
    ACTION_RUN_ASYNC_RUNNING,
    ACTION_RUN_ASYNC_SUCCEEDED,
)
from foundry_lite.security.tenant_context import tenant_context


class ActionStepRetryableFailure(ActionRunRetryableFailure):
    """Signal Temporal that a safely retryable Action step needs redelivery."""


class ActionDistributedRunService(CoreService):
    required_dependencies = (
        "engine",
        "policy",
        "action_repository",
        "action_execution_repository",
        "action_function_executor",
        "connector_registry_repository",
        "metadata_repository",
    )
    required_collaborators = (
        "object_index_record_mutation_service",
        "object_records_service",
        "action_effect_delivery_service",
        "ontology_lookup_service",
        "osdk_application_service",
        "runtime_service",
    )
    action_repository: ActionRepository
    action_execution_repository: ActionExecutionRepository
    action_function_executor: ActionFunctionExecutor
    connector_registry_repository: ConnectorRegistryRepository
    metadata_repository: MetadataRepository
    object_index_record_mutation_service: ActionObjectIndexer
    object_records_service: ActionObjectRecordLookup
    ontology_lookup_service: OntologyLookupService
    osdk_application_service: ActionOsdkScopeBoundary
    runtime_service: ActionRuntimeBoundary
    action_effect_delivery_service: ActionEffectDeliveryService

    def drive(self, request: ActionRunDispatchRequest, *, worker_id: str) -> dict[str, object]:
        row = load_action_run(self.engine, self.action_execution_repository, request.tenant_id, request.run_id)
        if row["status"] in ACTION_RUN_TERMINAL_STATUSES:
            return {"actionRunId": row["id"], "status": row["status"]}
        ctx = action_worker_context(row)
        if row["status"] == "cancelling":
            return self._finalize_cancellation(ctx, row, worker_id)
        attempt = self._claim(ctx, row, worker_id)
        if attempt is None:
            return self._resolve_unclaimed(ctx, row, worker_id)
        try:
            heartbeat = ActionAttemptHeartbeat(self.engine, self.action_execution_repository, attempt)
            heartbeat.start()
            try:
                before_effect = self.action_effect_delivery_service.execute_before(ctx, row, attempt)
                plan, function_result = self._execute_plan(ctx, row, before_effect)
            finally:
                heartbeat.stop()
            if heartbeat.is_lost:
                raise ActionStepLeaseLost("Action step heartbeat lost its fencing lease")
            return self._commit(ctx, row, attempt, plan, function_result, before_effect)
        except ActionStepLeaseLost:
            raise
        except Exception as exc:
            self._record_failure(ctx, row, attempt, exc)
            raise

    def _claim(self, ctx: RequestContext, row: ActionAsyncRunRow, worker_id: str) -> ActionStepAttemptRow | None:
        lease = action_worker_lease(worker_id)
        step_key = "function" if action_has_function(row) else "commit"
        with self.engine.begin() as transaction:
            current = required_action_run(self.action_execution_repository, transaction, ctx, row["id"])
            if current["status"] == "cancelling":
                return None
            attempt = self.action_execution_repository.claim_step(
                transaction=transaction, claim=action_attempt_claim(current, step_key, lease)
            )
            if attempt is None:
                return None
            self.action_execution_repository.transition_run(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                run_id=row["id"],
                transition=ACTION_RUN_ASYNC_RUNNING,
                changed_at=lease.heartbeat_at,
            )
            append_worker_attempt_event(
                self.action_execution_repository, transaction, ctx, row, attempt, "action.step.running", {}
            )
            return attempt

    def _execute_plan(
        self,
        ctx: RequestContext,
        row: ActionAsyncRunRow,
        before_effect: dict[str, object] | None,
    ) -> tuple[EditPlan, ActionFunctionExecutionResult | None]:
        require_stored_plan_hash(row)
        contract = stored_action_contract(row)
        if contract.function is None:
            return stored_edit_plan(row), None
        result = self.action_function_executor.execute(action_function_request(row, ctx, before_effect))
        plan = result.edit_batch.to_edit_plan(operation_prefix=f"{row['id']}:function")
        return plan, result

    def _commit(
        self,
        ctx: RequestContext,
        row: ActionAsyncRunRow,
        attempt: ActionStepAttemptRow,
        plan: EditPlan,
        function_result: ActionFunctionExecutionResult | None,
        before_effect: dict[str, object] | None,
    ) -> dict[str, object]:
        changed_at = utc_now()
        repository = self.action_execution_repository
        with self.engine.begin() as transaction:
            current = required_action_run(repository, transaction, ctx, row["id"])
            owner = require_action_attempt_owner(repository, transaction, ctx, attempt, changed_at)
            if current["status"] == "cancelling":
                return self._cancel_claimed(transaction, ctx, current, owner, changed_at)
            contract = self._revalidate(transaction, ctx, current, plan)
            mark_action_commit_pending(repository, transaction, ctx, current, changed_at)
            result = self._commit_edit_plan(transaction, ctx, row, plan, contract)
            committed_plan = dict(plan_summary(result))
            effect_receipt_ids = self.action_effect_delivery_service.enqueue_after(
                transaction, ctx, current, committed_plan
            )
            output = action_success_output(contract, function_result, before_effect, committed_plan, effect_receipt_ids)
            completed = complete_action_attempt(
                repository, transaction, ctx, owner, "succeeded", output, None, None, changed_at
            )
            append_worker_attempt_event(
                repository, transaction, ctx, current, completed, "action.step.succeeded", output
            )
            append_worker_attempt_event(
                repository, transaction, ctx, current, completed, "action.run.succeeded", output
            )
        return {"actionRunId": row["id"], "status": "succeeded", "result": output}

    def _commit_edit_plan(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        row: ActionAsyncRunRow,
        plan: EditPlan,
        contract: ActionDefinitionV3,
    ) -> ActionEditPlanResult:
        committer = action_plan_committer(
            self.action_repository,
            self.object_index_record_mutation_service,
            self.object_records_service,
            self.ontology_lookup_service,
            self.runtime_service,
        )
        return committer.commit_plan(
            transaction,
            ctx,
            action_run_id=row["id"],
            plan=plan,
            contract=contract,
            transition=ACTION_RUN_ASYNC_SUCCEEDED,
        )

    def _revalidate(
        self, transaction: TransactionContext, ctx: RequestContext, row: ActionAsyncRunRow, plan: EditPlan
    ) -> ActionDefinitionV3:
        contract = stored_action_contract(row)
        active = self.ontology_lookup_service._active_action_type(transaction, ctx, row["action_type_api_name"])
        if active["id"] != row["action_type_id"]:
            raise ConflictDetected("Action definition changed after planning")
        if action_contract_fingerprint(contract) != row["definition_version"]:
            raise ConflictDetected("Action definition fingerprint changed after planning")
        require_action_permission(
            self.engine, self.policy, self.runtime_service, ctx, contract.api_name, action="execute"
        )
        require_action_target_read(
            self.engine,
            self.policy,
            self.runtime_service,
            ctx,
            contract.api_name,
            row["target_object_type_api_name"],
            row["target_object_id"],
            action="execute",
        )
        self.osdk_application_service.require_resource_scope(
            ctx, resource_type="action", resource_api_name=contract.api_name, operation="execute"
        )
        authorize_action_effects(
            transaction,
            ctx,
            self.policy,
            self.osdk_application_service,
            self.connector_registry_repository,
            contract,
        )
        authorize_action_edit_plan(transaction, ctx, self.policy, self.ontology_lookup_service, contract, plan)
        return contract

    def _record_failure(
        self, ctx: RequestContext, row: ActionAsyncRunRow, attempt: ActionStepAttemptRow, exc: Exception
    ) -> None:
        changed_at = utc_now()
        is_retryable = is_action_error_retryable(exc) and int(attempt["attempt_number"]) < 3
        retry_at = action_retry_at(changed_at, int(attempt["attempt_number"])) if is_retryable else None
        with self.engine.begin() as transaction:
            self._persist_failure(transaction, ctx, row, attempt, exc, changed_at, retry_at)
        if is_retryable:
            raise ActionStepRetryableFailure(str(exc)) from exc

    def _persist_failure(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        row: ActionAsyncRunRow,
        attempt: ActionStepAttemptRow,
        exc: Exception,
        changed_at: str,
        retry_at: str | None,
    ) -> None:
        owner = require_action_attempt_owner(self.action_execution_repository, transaction, ctx, attempt, changed_at)
        current = required_action_run(self.action_execution_repository, transaction, ctx, row["id"])
        if current["status"] == "cancelling":
            self._cancel_claimed(transaction, ctx, current, owner, changed_at)
            return
        error = dict(self.runtime_service._error_payload(exc, ctx, run_id=row["id"]))
        completed = complete_action_attempt(
            self.action_execution_repository,
            transaction,
            ctx,
            owner,
            "failed",
            {},
            error,
            action_error_kind(exc),
            changed_at,
            retry_at,
        )
        event = "action.step.retry_wait" if retry_at else "action.step.failed"
        append_worker_attempt_event(
            self.action_execution_repository, transaction, ctx, current, completed, event, {"retryAt": retry_at}
        )
        if retry_at is None:
            self._persist_terminal_failure(transaction, ctx, current, completed, exc, error, changed_at)

    def _persist_terminal_failure(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        row: ActionAsyncRunRow,
        attempt: ActionStepAttemptRow,
        exc: Exception,
        error: dict[str, object],
        changed_at: str,
    ) -> None:
        if isinstance(exc, ConflictDetected):
            transition = ACTION_RUN_ASYNC_CONFLICT
        elif isinstance(exc, ActionBeforeEffectOutcomeUnknown):
            transition = ACTION_RUN_ASYNC_OUTCOME_UNKNOWN
        else:
            transition = ACTION_RUN_ASYNC_FAILED
        persist_terminal_action_failure(
            self.action_execution_repository,
            self.runtime_service,
            transaction,
            ctx,
            row,
            attempt,
            transition,
            error,
            changed_at,
        )

    def recover_all_cancellations(self, *, worker_id: str, limit: int = 100) -> dict[str, object]:
        cancelled = 0
        for tenant_id in self.metadata_repository.list_tenant_ids():
            with tenant_context(tenant_id):
                cancelled += self._recover_tenant_cancellations(tenant_id, worker_id, limit)
        return {"cancelled": cancelled}

    def _recover_tenant_cancellations(self, tenant_id: str, worker_id: str, limit: int) -> int:
        with self.engine.begin() as transaction:
            rows = self.action_execution_repository.cancelling_runs(
                transaction=transaction, tenant_id=tenant_id, limit=max(1, min(limit, 500))
            )
        outcomes = [self._finalize_cancellation(action_worker_context(row), row, worker_id) for row in rows]
        return sum(1 for outcome in outcomes if outcome["status"] == "cancelled")

    def _resolve_unclaimed(self, ctx: RequestContext, row: ActionAsyncRunRow, worker_id: str) -> dict[str, object]:
        current = load_action_run(self.engine, self.action_execution_repository, ctx.tenant_id, row["id"])
        if current["status"] == "cancelling":
            return self._finalize_cancellation(ctx, current, worker_id)
        if current["status"] in ACTION_RUN_TERMINAL_STATUSES:
            return {"actionRunId": current["id"], "status": current["status"]}
        raise ActionStepLeaseLost("Action step is owned by another live worker")

    def _finalize_cancellation(self, ctx: RequestContext, row: ActionAsyncRunRow, worker_id: str) -> dict[str, object]:
        changed_at = utc_now()
        with self.engine.begin() as transaction:
            current = required_action_run(self.action_execution_repository, transaction, ctx, row["id"])
            if current["status"] in ACTION_RUN_TERMINAL_STATUSES:
                return {"actionRunId": row["id"], "status": current["status"]}
            is_blocked, attempt = self._claim_cancellation(transaction, current, worker_id)
            if is_blocked:
                return {"actionRunId": row["id"], "status": "cancelling"}
            completed = None
            if attempt is not None:
                completed = complete_action_attempt(
                    self.action_execution_repository,
                    transaction,
                    ctx,
                    attempt,
                    "cancelled",
                    {},
                    None,
                    "cancellation",
                    changed_at,
                )
            updated = self.action_execution_repository.transition_run(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                run_id=row["id"],
                transition=ACTION_RUN_ASYNC_CANCELLED,
                changed_at=changed_at,
            )
            if updated is not None:
                self._record_cancelled_evidence(transaction, ctx, current, completed)
        return {"actionRunId": row["id"], "status": "cancelled"}

    def _claim_cancellation(
        self, transaction: TransactionContext, row: ActionAsyncRunRow, worker_id: str
    ) -> tuple[bool, ActionStepAttemptRow | None]:
        steps = self.action_execution_repository.steps_for_run(
            transaction=transaction, tenant_id=row["tenant_id"], run_id=row["id"]
        )
        if not any(step["status"] == "running" for step in steps):
            return False, None
        lease = action_worker_lease(worker_id)
        step_key = "function" if action_has_function(row) else "commit"
        attempt = self.action_execution_repository.claim_step(
            transaction=transaction,
            claim=action_attempt_claim(row, step_key, lease, is_cancellation=True),
        )
        return attempt is None, attempt

    def _cancel_claimed(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        row: ActionAsyncRunRow,
        attempt: ActionStepAttemptRow,
        changed_at: str,
    ) -> dict[str, object]:
        completed = complete_action_attempt(
            self.action_execution_repository,
            transaction,
            ctx,
            attempt,
            "cancelled",
            {},
            None,
            "cancellation",
            changed_at,
        )
        updated = self.action_execution_repository.transition_run(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            run_id=row["id"],
            transition=ACTION_RUN_ASYNC_CANCELLED,
            changed_at=changed_at,
        )
        if updated is None:
            raise ConflictDetected("Action cancellation lost the terminal state race")
        self._record_cancelled_evidence(transaction, ctx, row, completed)
        return {"actionRunId": row["id"], "status": "cancelled"}

    def _record_cancelled_evidence(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        row: ActionAsyncRunRow,
        attempt: ActionStepAttemptRow | None,
    ) -> None:
        if attempt is None:
            append_action_run_event(
                self.action_execution_repository, transaction, ctx, row["id"], "action.run.cancelled", {}
            )
        else:
            append_action_attempt_event(
                self.action_execution_repository, transaction, ctx, row, attempt, "action.run.cancelled", {}
            )
        self.runtime_service._audit(
            transaction,
            ctx,
            event_type="action.run.cancelled",
            resource_type="action_run",
            resource_id=row["id"],
            action="cancel",
            decision="allow",
            after_ref={"cancelReason": row["cancel_reason"]},
            correlation_id=row["id"],
        )
