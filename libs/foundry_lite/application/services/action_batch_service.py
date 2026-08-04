"""Batch action apply use case service.

One action invocation edits MANY objects of the action's target type
all-or-nothing: every target is validated and mutated inside one database
transaction, so a single stale version or failed precondition rejects the
whole batch and leaves every object untouched. The batch is recorded as ONE
``action_runs`` row (sentinel target id, targets captured in the request
fingerprint, result, and audit evidence) so the existing idempotency, CAS
status, and operations surfaces keep working without a schema migration.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.action_types import (
    ActionBatchApplyCommand,
    ActionBatchApplyOutcome,
    ActionBatchApplyResponse,
    ActionBatchTargetResult,
)
from foundry_lite.application.ports import (
    ACTION_RUN_SUCCEEDED,
    ActionRunRecord,
    ActionRunRow,
    ActionTypeRow,
    ObjectRecordRow,
    TransactionContext,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.action_batch_helpers import (
    BATCH_EXPECTED_OBJECT_VERSION,
    BATCH_TARGET_OBJECT_ID,
    TargetFailure,
    batch_failure_error,
    batch_replay_response,
    batch_request_fingerprint,
    batch_success_response,
    batch_targets_from_request,
    publish_batch_commit_events,
    require_batch_supported_action,
    target_failure,
    target_request_error,
)
from foundry_lite.application.services.action_helpers import (
    action_failure_transition,
    action_patch,
    action_target_record_error,
    audit_idempotency_conflict,
    previous_action_values,
    require_action_target_api_name,
    require_action_write_open,
)
from foundry_lite.application.services.action_mutations import ActionMutationUnitOfWork
from foundry_lite.application.services.action_permission_guards import (
    require_action_permission,
    require_action_target_read,
    segment_mutation_denied_error,
)
from foundry_lite.application.services.action_protocols import (
    ActionObjectIndexer,
    ActionObjectRecordLookup,
    ActionOntologyLookup,
    ActionOsdkScopeBoundary,
    ActionRuntimeBoundary,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.object_store.row_policies import visible_record
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    InvariantViolation,
    PermissionDenied,
    ValidationFailed,
)


class ActionBatchApplyService(CoreService):
    """Apply one action to many targets atomically with idempotency and audit."""

    required_dependencies = ("engine", "policy", "action_repository")
    required_collaborators = (
        "object_index_record_mutation_service",
        "object_records_service",
        "ontology_service",
        "osdk_application_service",
        "runtime_service",
    )
    object_index_record_mutation_service: ActionObjectIndexer
    object_records_service: ActionObjectRecordLookup
    ontology_service: ActionOntologyLookup
    osdk_application_service: ActionOsdkScopeBoundary
    runtime_service: ActionRuntimeBoundary

    def apply_action_batch(
        self,
        action_api_name: str,
        *,
        object_type: str,
        targets: Sequence[Mapping[str, object]],
        params: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> ActionBatchApplyResponse:
        ctx = ctx or RequestContext()
        if not idempotency_key:
            raise ValidationFailed("idempotency key is required")
        parsed_targets = batch_targets_from_request(targets)
        command = ActionBatchApplyCommand(
            action_api_name=action_api_name,
            object_type=object_type,
            targets=parsed_targets,
            params=dict(params),
            idempotency_key=idempotency_key,
            request_fingerprint=batch_request_fingerprint(
                action_api_name=action_api_name,
                object_type=object_type,
                targets=parsed_targets,
                params=params,
            ),
        )
        self._authorize_batch_apply(ctx, command)
        outcome = self._run_batch_command(ctx, command, _new_id("action_run"))
        if outcome.deferred_error is not None:
            raise outcome.deferred_error
        if outcome.response is None:
            raise InvariantViolation("batch action did not produce a response")
        return outcome.response

    def _authorize_batch_apply(self, ctx: RequestContext, command: ActionBatchApplyCommand) -> None:
        """Execute permission and OSDK scope once; object:read per target (Palantir semantics)."""
        require_action_permission(self.engine, self.policy, self.runtime_service, ctx, command.action_api_name)
        for target in command.targets:
            require_action_target_read(
                self.engine,
                self.policy,
                self.runtime_service,
                ctx,
                command.action_api_name,
                command.object_type,
                target.object_id,
            )
        self.osdk_application_service.require_resource_scope(
            ctx,
            resource_type="action",
            resource_api_name=command.action_api_name,
            operation="execute",
        )
        require_action_write_open(self.runtime_service, ctx, "apply", "action_type", command.action_api_name)

    def _run_batch_command(
        self,
        ctx: RequestContext,
        command: ActionBatchApplyCommand,
        action_run_id: str,
    ) -> ActionBatchApplyOutcome:
        action_type_for_failure: ActionTypeRow | None = None
        try:
            with self.engine.begin() as conn:
                action_type = self.ontology_service._active_action_type(conn, ctx, command.action_api_name)
                action_type_for_failure = action_type
                require_action_target_api_name(action_type, command.object_type)
                require_batch_supported_action(action_type)
                replay = self._replay_or_none(conn, ctx, action_type, action_run_id, command)
                if replay is not None:
                    return replay
                outcome = self._complete_received_batch_run(conn, ctx, action_type, action_run_id, command)
        except ConflictDetected as exc:
            # A commit-time CAS loss rolled the whole transaction back (run row
            # included); record the terminal conflict in a fresh transaction so
            # the failure stays auditable, mirroring single apply.
            if action_type_for_failure is None:
                raise
            return self._record_rolled_back_conflict(ctx, command, action_run_id, action_type_for_failure, exc)
        return outcome

    def _replay_or_none(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        action_run_id: str,
        command: ActionBatchApplyCommand,
    ) -> ActionBatchApplyOutcome | None:
        """Replay if this idempotency key already won; None means this call is the winner."""
        existing = self.action_repository.action_run_by_idempotency(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            action_type_id=action_type["id"],
            actor_user_id=ctx.actor_user_id,
            idempotency_key=command.idempotency_key,
        )
        if existing is not None:
            return self._replay_existing_batch_run(conn, ctx, existing, command.request_fingerprint)
        raced = self.action_repository.insert_action_run_or_get_existing(
            transaction=conn,
            record=self._batch_run_record(conn, ctx, action_type, action_run_id, command),
        )
        if raced is not None:
            return self._replay_existing_batch_run(conn, ctx, raced, command.request_fingerprint)
        return None

    def _batch_run_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        action_run_id: str,
        command: ActionBatchApplyCommand,
    ) -> ActionRunRecord:
        concrete_target = self.ontology_service._active_object_type(conn, ctx, command.object_type)
        return ActionRunRecord(
            action_run_id=action_run_id,
            tenant_id=ctx.tenant_id,
            action_type_id=action_type["id"],
            action_type_api_name=command.action_api_name,
            actor_user_id=ctx.actor_user_id,
            target_object_type_id=concrete_target["id"],
            target_object_type_api_name=command.object_type,
            target_object_id=BATCH_TARGET_OBJECT_ID,
            expected_object_version=BATCH_EXPECTED_OBJECT_VERSION,
            parameters=command.params,
            status="received",
            idempotency_key=command.idempotency_key,
            request_fingerprint=command.request_fingerprint,
            result=None,
            error=None,
            created_at=_now(),
            completed_at=None,
        )

    def _replay_existing_batch_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        existing: ActionRunRow,
        request_fingerprint: str,
    ) -> ActionBatchApplyOutcome:
        if existing["request_fingerprint"] == request_fingerprint:
            return ActionBatchApplyOutcome(response=batch_replay_response(existing))
        error = ConflictDetected(
            "idempotency key conflict",
            details={
                "action_run_id": existing["id"],
                "idempotency_key": existing["idempotency_key"],
                "existing_request_fingerprint": existing["request_fingerprint"],
                "request_fingerprint": request_fingerprint,
            },
        )
        audit_idempotency_conflict(self.runtime_service, conn, ctx, existing, request_fingerprint)
        return ActionBatchApplyOutcome(deferred_error=error)

    def _complete_received_batch_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        action_run_id: str,
        command: ActionBatchApplyCommand,
    ) -> ActionBatchApplyOutcome:
        # One shared patch mutates every target, so a datasource segment the
        # caller cannot view rejects the whole batch before any target work.
        if (segment_error := segment_mutation_denied_error(self.policy, ctx, action_type)) is not None:
            self._fail_batch_run(conn, ctx, action_run_id, segment_error)
            return ActionBatchApplyOutcome(deferred_error=segment_error)
        records, failures = self._validated_target_records(conn, ctx, action_type, command)
        if failures:
            error = batch_failure_error(failures, len(command.targets))
            self._fail_batch_run(conn, ctx, action_run_id, error)
            return ActionBatchApplyOutcome(deferred_error=error)
        response = self._commit_batch(conn, ctx, action_type, action_run_id, command, records)
        return ActionBatchApplyOutcome(response=response)

    def _validated_target_records(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        command: ActionBatchApplyCommand,
    ) -> tuple[list[ObjectRecordRow], list[TargetFailure]]:
        """Validate every target before mutating any: all-or-nothing needs the full failure set."""
        records: list[ObjectRecordRow] = []
        failures: list[TargetFailure] = []
        # Hidden targets fail per-target as NotFound (record=None path), so one
        # row-policy-hidden id rejects the batch exactly like a missing id.
        target_type = self.ontology_service._active_object_type(conn, ctx, command.object_type)
        for target in command.targets:
            record = self.object_records_service._object_record(conn, ctx, command.object_type, target.object_id)
            record = visible_record(record, target_type, ctx.roles)
            if record is not None and (invariant := action_target_record_error(action_type, record)) is not None:
                raise invariant
            error = target_request_error(action_type, record, target.expected_object_version, command.params, ctx)
            if error is not None:
                failures.append(target_failure(target.object_id, error))
            elif record is not None:
                records.append(record)
        return records, failures

    def _commit_batch(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        action_run_id: str,
        command: ActionBatchApplyCommand,
        records: list[ObjectRecordRow],
    ) -> ActionBatchApplyResponse:
        """Mutate every target through the single-apply unit-of-work primitives, then seal the run.

        Index-visible object rows are updated synchronously in this transaction
        (read-your-writes), edits carry per-target idempotency keys derived
        from the batch key, and outbox/audit rows commit atomically with them.
        """
        patch = dict(action_patch(action_type, command.params))
        results: list[ActionBatchTargetResult] = []
        previous_by_object: dict[str, dict[str, object]] = {}
        for record in records:
            result, previous_values = self._mutate_target(conn, ctx, action_run_id, command, record, patch)
            previous_by_object[record["object_id"]] = previous_values
            results.append(result)
        response = batch_success_response(action_run_id, command.object_type, results)
        self._finish_batch_run(conn, ctx, action_run_id, response)
        publish_batch_commit_events(self.runtime_service, conn, ctx, action_run_id, command.object_type, results)
        self._audit_batch_commit(conn, ctx, action_run_id, command.object_type, results, previous_by_object, patch)
        return response

    def _mutate_target(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        command: ActionBatchApplyCommand,
        record: ObjectRecordRow,
        patch: dict[str, object],
    ) -> tuple[ActionBatchTargetResult, dict[str, object]]:
        unit_of_work = self._mutation_unit_of_work()
        previous_values = dict(previous_action_values(record, patch))
        unit_of_work._update_action_target(conn, record, patch)
        edit_id = unit_of_work._insert_object_edit(
            conn,
            ctx,
            action_run_id,
            record,
            patch,
            previous_values,
            f"{command.idempotency_key}:{record['object_id']}",
        )
        result: ActionBatchTargetResult = {
            "objectId": record["object_id"],
            "objectEditId": edit_id,
            "newObjectVersion": record["object_version"] + 1,
            "patch": patch,
        }
        return result, previous_values

    def _finish_batch_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        response: ActionBatchApplyResponse,
    ) -> None:
        updated = self.action_repository.update_action_run_terminal(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            action_run_id=action_run_id,
            transition=ACTION_RUN_SUCCEEDED,
            error=None,
            completed_at=_now(),
            result=response,
        )
        if not updated:
            raise ConflictDetected("action run terminal state changed concurrently")

    def _audit_batch_commit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        object_type: str,
        results: list[ActionBatchTargetResult],
        previous_by_object: Mapping[str, Mapping[str, object]],
        patch: Mapping[str, object],
    ) -> None:
        """One audit event for the whole batch, sensitive values masked like single apply."""
        masked_previous = {
            object_id: self.policy.mask_sensitive_properties(ctx, object_type, dict(values))
            for object_id, values in previous_by_object.items()
        }
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="action.run.committed",
            resource_type="action_run",
            resource_id=action_run_id,
            action="apply",
            before_ref={"targets": masked_previous},
            after_ref={
                "patch": self.policy.mask_sensitive_properties(ctx, object_type, dict(patch)),
                "object_edit_ids": {item["objectId"]: item["objectEditId"] for item in results},
                "target_count": len(results),
            },
            correlation_id=action_run_id,
        )

    def _record_rolled_back_conflict(
        self,
        ctx: RequestContext,
        command: ActionBatchApplyCommand,
        action_run_id: str,
        action_type: ActionTypeRow,
        error: ConflictDetected,
    ) -> ActionBatchApplyOutcome:
        with self.engine.begin() as conn:
            replay = self._replay_or_none(conn, ctx, action_type, action_run_id, command)
            if replay is not None:
                return replay
            self._fail_batch_run(conn, ctx, action_run_id, error)
        return ActionBatchApplyOutcome(deferred_error=error)

    def _fail_batch_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        error: Exception,
    ) -> None:
        """Persist and audit the rejected batch so the failure leaves operator evidence."""
        transition = action_failure_transition(error)
        updated = self.action_repository.update_action_run_terminal(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            action_run_id=action_run_id,
            transition=transition,
            error=self.runtime_service._error_payload(error, ctx, run_id=action_run_id, correlation_id=action_run_id),
            completed_at=_now(),
        )
        if not updated:
            raise ConflictDetected("action run terminal state changed concurrently", details={"run_id": action_run_id})
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="action.run.failed",
            resource_type="action_run",
            resource_id=action_run_id,
            action="apply",
            decision="deny" if isinstance(error, PermissionDenied) else "allow",
            after_ref=self.runtime_service._error_payload(
                error, ctx, run_id=action_run_id, correlation_id=action_run_id
            ),
            correlation_id=action_run_id,
        )

    def _mutation_unit_of_work(self) -> ActionMutationUnitOfWork:
        """Same mutation primitives as single apply: CAS target update + edit insert."""
        return ActionMutationUnitOfWork(
            action_repository=self.action_repository,
            object_indexing_service=self.object_index_record_mutation_service,
            runtime_service=self.runtime_service,
            policy=self.policy,
        )
