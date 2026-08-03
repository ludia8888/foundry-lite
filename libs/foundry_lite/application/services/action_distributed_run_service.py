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
    InvariantViolation,
    MetadataRepository,
    OntologyLookupService,
    RequestContext,
    TransactionContext,
    action_contract_fingerprint,
)
from foundry_lite.application.services.action_distributed_run_evidence import (
    ACTION_RUN_TERMINAL_STATUSES,
    action_error_kind,
    action_function_output,
    action_has_function,
    action_retry_at,
    append_action_attempt_event,
    append_action_run_event,
    is_action_error_retryable,
)
from foundry_lite.application.services.action_distributed_run_support import (
    action_attempt_claim,
    action_function_request,
    action_worker_context,
    action_worker_lease,
    require_stored_plan_hash,
    stored_action_contract,
    stored_edit_plan,
    utc_now,
)
from foundry_lite.application.services.action_edit_plan_committer import ActionEditPlanCommitter, plan_summary
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
    ACTION_RUN_ASYNC_RUNNING,
    ACTION_RUN_ASYNC_SUCCEEDED,
    ACTION_RUN_COMMIT_PENDING,
)
from foundry_lite.security.tenant_context import tenant_context


class ActionStepRetryableFailure(ActionRunRetryableFailure):
    """Signal Temporal that a safely retryable Action step needs redelivery."""


class ActionStepLeaseLost(ActionRunRetryableFailure):
    """Stop a stale worker before it can write a result."""


class ActionDistributedRunService(CoreService):
    required_dependencies = (
        "engine",
        "policy",
        "action_repository",
        "action_execution_repository",
        "action_function_executor",
        "metadata_repository",
    )
    required_collaborators = (
        "object_index_record_mutation_service",
        "object_records_service",
        "ontology_lookup_service",
        "osdk_application_service",
        "runtime_service",
    )
    action_repository: ActionRepository
    action_execution_repository: ActionExecutionRepository
    action_function_executor: ActionFunctionExecutor
    metadata_repository: MetadataRepository
    object_index_record_mutation_service: ActionObjectIndexer
    object_records_service: ActionObjectRecordLookup
    ontology_lookup_service: OntologyLookupService
    osdk_application_service: ActionOsdkScopeBoundary
    runtime_service: ActionRuntimeBoundary

    def drive(self, request: ActionRunDispatchRequest, *, worker_id: str) -> dict[str, object]:
        row = self._run(request.tenant_id, request.run_id)
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
                plan, function_result = self._execute_plan(ctx, row)
            finally:
                heartbeat.stop()
            if heartbeat.is_lost:
                raise ActionStepLeaseLost("Action step heartbeat lost its fencing lease")
            return self._commit(ctx, row, attempt, plan, function_result)
        except ActionStepLeaseLost:
            raise
        except Exception as exc:
            self._record_failure(ctx, row, attempt, exc)
            raise

    def _claim(self, ctx: RequestContext, row: ActionAsyncRunRow, worker_id: str) -> ActionStepAttemptRow | None:
        lease = action_worker_lease(worker_id)
        step_key = "function" if action_has_function(row) else "commit"
        with self.engine.begin() as transaction:
            current = self._required_run(transaction, ctx, row["id"])
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
            self._append_attempt_event(transaction, ctx, row, attempt, "action.step.running", {})
            return attempt

    def _execute_plan(
        self, ctx: RequestContext, row: ActionAsyncRunRow
    ) -> tuple[EditPlan, ActionFunctionExecutionResult | None]:
        require_stored_plan_hash(row)
        contract = stored_action_contract(row)
        if contract.function is None:
            return stored_edit_plan(row), None
        result = self.action_function_executor.execute(action_function_request(row, ctx))
        plan = result.edit_batch.to_edit_plan(operation_prefix=f"{row['id']}:function")
        return plan, result

    def _commit(
        self,
        ctx: RequestContext,
        row: ActionAsyncRunRow,
        attempt: ActionStepAttemptRow,
        plan: EditPlan,
        function_result: ActionFunctionExecutionResult | None,
    ) -> dict[str, object]:
        changed_at = utc_now()
        with self.engine.begin() as transaction:
            current = self._required_run(transaction, ctx, row["id"])
            owner = self._require_owner(transaction, ctx, attempt, changed_at)
            if current["status"] == "cancelling":
                return self._cancel_claimed(transaction, ctx, current, owner, changed_at)
            contract = self._revalidate(transaction, ctx, current, plan)
            self._mark_commit_pending(transaction, ctx, current, changed_at)
            result = self._committer().commit_plan(
                transaction, ctx, action_run_id=row["id"], plan=plan, transition=ACTION_RUN_ASYNC_SUCCEEDED
            )
            output: dict[str, object] = {
                "plan": dict(plan_summary(result)),
                "function": action_function_output(function_result),
                "externalExecutionId": function_result.external_execution_id if function_result else None,
                "definitionFingerprint": action_contract_fingerprint(contract),
            }
            completed = self._complete_attempt(transaction, ctx, owner, "succeeded", output, None, None, changed_at)
            self._append_attempt_event(transaction, ctx, current, completed, "action.step.succeeded", output)
            self._append_attempt_event(transaction, ctx, current, completed, "action.run.succeeded", output)
        return {"actionRunId": row["id"], "status": "succeeded", "result": output}

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
        authorize_action_edit_plan(transaction, ctx, self.policy, self.ontology_lookup_service, contract, plan)
        return contract

    def _mark_commit_pending(
        self, transaction: TransactionContext, ctx: RequestContext, row: ActionAsyncRunRow, changed_at: str
    ) -> None:
        updated = self.action_execution_repository.transition_run(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            run_id=row["id"],
            transition=ACTION_RUN_COMMIT_PENDING,
            changed_at=changed_at,
        )
        if updated is None:
            raise ConflictDetected("Action run changed before commit")

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
        owner = self._require_owner(transaction, ctx, attempt, changed_at)
        current = self._required_run(transaction, ctx, row["id"])
        if current["status"] == "cancelling":
            self._cancel_claimed(transaction, ctx, current, owner, changed_at)
            return
        error = dict(self.runtime_service._error_payload(exc, ctx, run_id=row["id"]))
        completed = self._complete_attempt(
            transaction, ctx, owner, "failed", {}, error, action_error_kind(exc), changed_at, retry_at=retry_at
        )
        event = "action.step.retry_wait" if retry_at else "action.step.failed"
        self._append_attempt_event(transaction, ctx, current, completed, event, {"retryAt": retry_at})
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
        transition = ACTION_RUN_ASYNC_CONFLICT if isinstance(exc, ConflictDetected) else ACTION_RUN_ASYNC_FAILED
        self.action_execution_repository.transition_run(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            run_id=row["id"],
            transition=transition,
            changed_at=changed_at,
            error=error,
        )
        self._append_attempt_event(transaction, ctx, row, attempt, f"action.run.{transition.to_status}", error)
        self.runtime_service._audit(
            transaction,
            ctx,
            event_type=f"action.run.{transition.to_status}",
            resource_type="action_run",
            resource_id=row["id"],
            action="execute",
            decision="deny",
            after_ref=error,
            correlation_id=row["id"],
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
        current = self._run(ctx.tenant_id, row["id"])
        if current["status"] == "cancelling":
            return self._finalize_cancellation(ctx, current, worker_id)
        if current["status"] in ACTION_RUN_TERMINAL_STATUSES:
            return {"actionRunId": current["id"], "status": current["status"]}
        raise ActionStepLeaseLost("Action step is owned by another live worker")

    def _finalize_cancellation(self, ctx: RequestContext, row: ActionAsyncRunRow, worker_id: str) -> dict[str, object]:
        changed_at = utc_now()
        with self.engine.begin() as transaction:
            current = self._required_run(transaction, ctx, row["id"])
            if current["status"] in ACTION_RUN_TERMINAL_STATUSES:
                return {"actionRunId": row["id"], "status": current["status"]}
            is_blocked, attempt = self._claim_cancellation(transaction, current, worker_id)
            if is_blocked:
                return {"actionRunId": row["id"], "status": "cancelling"}
            completed = None
            if attempt is not None:
                completed = self._complete_attempt(
                    transaction, ctx, attempt, "cancelled", {}, None, "cancellation", changed_at
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
        completed = self._complete_attempt(transaction, ctx, attempt, "cancelled", {}, None, "cancellation", changed_at)
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

    def _require_owner(
        self, transaction: TransactionContext, ctx: RequestContext, attempt: ActionStepAttemptRow, owned_at: str
    ) -> ActionStepAttemptRow:
        owner = self.action_execution_repository.lock_attempt_owner(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            attempt_id=attempt["id"],
            worker_id=attempt["worker_id"],
            lease_token=attempt["lease_token"],
            fencing_token=attempt["fencing_token"],
            owned_at=owned_at,
        )
        if owner is None:
            raise ActionStepLeaseLost("Action step fencing token is stale")
        return owner

    def _complete_attempt(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        attempt: ActionStepAttemptRow,
        status: str,
        output: dict[str, object],
        error: dict[str, object] | None,
        error_kind: str | None,
        changed_at: str,
        *,
        retry_at: str | None = None,
    ) -> ActionStepAttemptRow:
        completed = self.action_execution_repository.complete_attempt(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            attempt_id=attempt["id"],
            worker_id=attempt["worker_id"],
            lease_token=attempt["lease_token"],
            fencing_token=attempt["fencing_token"],
            status=status,
            output_manifest=output,
            error=error,
            error_kind=error_kind,
            completed_at=changed_at,
            retry_at=retry_at,
            external_execution_id=str(output.get("externalExecutionId") or "") or None,
        )
        if completed is None:
            raise ActionStepLeaseLost("Action step terminal write was fenced")
        return completed

    def _required_run(self, transaction: TransactionContext, ctx: RequestContext, run_id: str) -> ActionAsyncRunRow:
        row = self.action_execution_repository.run_by_id(
            transaction=transaction, tenant_id=ctx.tenant_id, run_id=run_id
        )
        if row is None:
            raise InvariantViolation("Action run disappeared")
        return row

    def _run(self, tenant_id: str, run_id: str) -> ActionAsyncRunRow:
        with self.engine.begin() as transaction:
            row = self.action_execution_repository.run_by_id(
                transaction=transaction, tenant_id=tenant_id, run_id=run_id
            )
        if row is None:
            raise InvariantViolation("Action run not found")
        return row

    def _committer(self) -> ActionEditPlanCommitter:
        return ActionEditPlanCommitter(
            action_repository=self.action_repository,
            object_indexer=self.object_index_record_mutation_service,
            object_lookup=self.object_records_service,
            ontology_lookup=self.ontology_lookup_service,
            link_type_lookup=self.ontology_lookup_service,
            runtime=self.runtime_service,
        )

    def _append_attempt_event(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        row: ActionAsyncRunRow,
        attempt: ActionStepAttemptRow,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        append_action_attempt_event(
            self.action_execution_repository, transaction, ctx, row, attempt, event_type, payload
        )
