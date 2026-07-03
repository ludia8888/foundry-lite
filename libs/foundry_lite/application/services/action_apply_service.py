"""Action apply use case service."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.action_types import (
    ActionApplyCommand,
    ActionApplyOutcome,
    ActionApplyResponse,
)
from foundry_lite.application.ports import (
    ActionRunRecord,
    ActionRunRow,
    ActionTypeRow,
    ObjectRecordRow,
    OsdkResourceOperation,
    TransactionContext,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.safe_expression import validate_action_request
from foundry_lite.application.services.action_helpers import (
    action_command,
    action_failure_transition,
    action_replay_response,
    action_target_record_error,
    audit_idempotency_conflict,
    require_action_target_api_name,
    require_action_write_open,
)
from foundry_lite.application.services.action_permission_guards import (
    require_action_permission,
    require_action_target_read,
    require_failure_injection_for_command,
)
from foundry_lite.application.services.action_protocols import ActionOsdkScopeBoundary
from foundry_lite.application.services.action_workflow import (
    ActionMutationUnitOfWork,
    ActionObjectIndexer,
    ActionObjectRecordLookup,
    ActionOntologyLookup,
    ActionRuntimeBoundary,
    LocalCommitFailed,
    WriteReceipt,
)
from foundry_lite.application.services.action_writeback_service import ActionWritebackService
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    InvariantViolation,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)


class ActionApplyService(CoreService):
    """Apply actions with idempotency, optimistic concurrency, audit, and writeback."""

    required_dependencies = ("engine", "policy", "action_repository")
    required_collaborators = (
        "action_writeback_service",
        "object_index_record_mutation_service",
        "object_records_service",
        "ontology_service",
        "osdk_application_service",
        "runtime_service",
    )
    action_writeback_service: ActionWritebackService
    object_index_record_mutation_service: ActionObjectIndexer
    object_records_service: ActionObjectRecordLookup
    ontology_service: ActionOntologyLookup
    osdk_application_service: ActionOsdkScopeBoundary
    runtime_service: ActionRuntimeBoundary

    def apply_action(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
        simulate_writeback_failure: bool = False,
        simulate_writeback_retryable: bool = False,
        simulate_writeback_outcome_unknown: bool = False,
        simulate_writeback_compensation_required: bool = False,
        external_writeback_uri: str | None = None,
    ) -> ActionApplyResponse:
        ctx = ctx or RequestContext()
        if not idempotency_key:
            raise ValidationFailed("idempotency key is required")
        command = self._action_command_from_request(
            action_api_name,
            object_type,
            object_id,
            expected_object_version,
            params,
            idempotency_key,
            simulate_writeback_failure,
            simulate_writeback_retryable,
            simulate_writeback_outcome_unknown,
            simulate_writeback_compensation_required,
            external_writeback_uri,
        )
        self._authorize_action_apply(ctx, command)
        action_run_id = _new_id("action_run")
        outcome = self._run_action_command(ctx, command, action_run_id)
        if outcome.deferred_error is not None:
            raise outcome.deferred_error
        if outcome.response is None:
            raise InvariantViolation("action did not produce a response")
        return outcome.response

    def _action_command_from_request(
        self,
        action_api_name: str,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        simulate_writeback_failure: bool,
        simulate_writeback_retryable: bool,
        simulate_writeback_outcome_unknown: bool,
        simulate_writeback_compensation_required: bool,
        external_writeback_uri: str | None,
    ) -> ActionApplyCommand:
        return action_command(
            action_api_name,
            object_type,
            object_id,
            expected_object_version,
            params,
            idempotency_key,
            simulate_writeback_failure,
            simulate_writeback_retryable,
            simulate_writeback_outcome_unknown,
            simulate_writeback_compensation_required,
            external_writeback_uri,
        )

    def _authorize_action_apply(self, ctx: RequestContext, command: ActionApplyCommand) -> None:
        require_failure_injection_for_command(self.engine, self.runtime_service, ctx, command)
        require_action_permission(self.engine, self.policy, self.runtime_service, ctx, command.action_api_name)
        require_action_target_read(
            self.engine,
            self.policy,
            self.runtime_service,
            ctx,
            command.action_api_name,
            command.object_type,
            command.object_id,
        )
        self._require_action_scope(ctx, command.action_api_name, "execute")
        require_action_write_open(self.runtime_service, ctx, "apply", "action_type", command.action_api_name)

    def _require_action_scope(
        self, ctx: RequestContext, action_api_name: str, operation: OsdkResourceOperation
    ) -> None:
        self.osdk_application_service.require_resource_scope(
            ctx,
            resource_type="action",
            resource_api_name=action_api_name,
            operation=operation,
        )

    def _run_action_command(
        self,
        ctx: RequestContext,
        command: ActionApplyCommand,
        action_run_id: str,
    ) -> ActionApplyOutcome:
        action_type_for_failure: ActionTypeRow | None = None
        try:
            with self.engine.begin() as conn:
                action_type = self.ontology_service._active_action_type(conn, ctx, command.action_api_name)
                action_type_for_failure = action_type
                require_action_target_api_name(action_type, command.object_type)
                replay = self._replay_or_none(conn, ctx, action_type, action_run_id, command)
                if replay is not None:
                    return replay
                record = self.object_records_service._object_record(conn, ctx, command.object_type, command.object_id)
                if record is not None and (error := action_target_record_error(action_type, record)) is not None:
                    raise error
                outcome = self._complete_received_action_run(
                    conn, ctx, action_type=action_type, action_run_id=action_run_id, command=command, record=record
                )
        except LocalCommitFailed as exc:
            if action_type_for_failure is None:
                raise
            return self._record_compensation_required_after_local_failure(
                ctx, command, action_run_id, action_type_for_failure, exc.receipt
            )
        except ConflictDetected as exc:
            if action_type_for_failure is None:
                raise
            return self._record_rolled_back_action_conflict(ctx, command, action_run_id, action_type_for_failure, exc)
        return outcome

    def _replay_or_none(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        action_run_id: str,
        command: ActionApplyCommand,
    ) -> ActionApplyOutcome | None:
        """Replay if this idempotency key already won; None means this call is the winner."""
        current = self._existing_action_run(conn, ctx, action_type, command.idempotency_key)
        if current is not None:
            return self._replay_existing_action_run(conn, ctx, current, command.request_fingerprint)
        existing = self._insert_action_run(
            conn, ctx, action_type=action_type, action_run_id=action_run_id, command=command
        )
        if existing is not None:
            return self._replay_existing_action_run(conn, ctx, existing, command.request_fingerprint)
        return None

    def _record_rolled_back_action_conflict(
        self,
        ctx: RequestContext,
        command: ActionApplyCommand,
        action_run_id: str,
        action_type: ActionTypeRow,
        error: ConflictDetected,
    ) -> ActionApplyOutcome:
        with self.engine.begin() as conn:
            replay = self._replay_or_none(conn, ctx, action_type, action_run_id, command)
            if replay is not None:
                return replay
            self._fail_action_run(conn, ctx, action_run_id, error)
        return ActionApplyOutcome(deferred_error=error)

    def _complete_received_action_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        action_type: ActionTypeRow,
        action_run_id: str,
        command: ActionApplyCommand,
        record: ObjectRecordRow | None,
    ) -> ActionApplyOutcome:
        deferred_error = self._action_request_error(
            action_type, record, command.expected_object_version, command.params
        )
        if deferred_error is not None:
            self._fail_action_run(conn, ctx, action_run_id, deferred_error)
            return ActionApplyOutcome(deferred_error=deferred_error)
        error = self.action_writeback_service.writeback_recorder().simulated_before_commit_error(
            conn, ctx, action_run_id, command
        )
        if error is not None:
            return ActionApplyOutcome(deferred_error=error)
        if record is None:
            raise InvariantViolation("action target record disappeared before commit")
        return self._writeback_and_commit(conn, ctx, action_type, action_run_id, command, record)

    def _writeback_and_commit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        action_run_id: str,
        command: ActionApplyCommand,
        record: ObjectRecordRow,
    ) -> ActionApplyOutcome:
        def commit() -> ActionApplyResponse:
            return self._mutation_unit_of_work().commit(
                conn,
                ctx,
                action_type=action_type,
                action_run_id=action_run_id,
                record=record,
                params=command.params,
                idempotency_key=command.idempotency_key,
            )

        real_runner = self.action_writeback_service.real_writeback_runner(command)
        if real_runner is not None:
            return real_runner.complete_before_commit(conn, ctx, action_run_id, command, commit=commit)
        self.action_writeback_service.writeback_recorder().record(
            conn,
            ctx,
            action_run_id,
            status="succeeded",
            idempotency_key=command.idempotency_key,
            request_hash=command.request_fingerprint,
            response={"status_code": 200},
        )
        return ActionApplyOutcome(response=commit())

    def _record_compensation_required_after_local_failure(
        self,
        ctx: RequestContext,
        command: ActionApplyCommand,
        action_run_id: str,
        action_type: ActionTypeRow,
        receipt: WriteReceipt,
    ) -> ActionApplyOutcome:
        runner = self.action_writeback_service.real_writeback_runner(command)
        if runner is None:
            raise InvariantViolation("compensation requires a real external writeback adapter")
        with self.engine.begin() as conn:
            replay = self._replay_or_none(conn, ctx, action_type, action_run_id, command)
            if replay is not None:
                return replay
            error = runner.record_compensation_required(conn, ctx, action_run_id, command, receipt)
        return ActionApplyOutcome(deferred_error=error)

    def _existing_action_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_type: ActionTypeRow,
        idempotency_key: str,
    ) -> ActionRunRow | None:
        return self.action_repository.action_run_by_idempotency(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            action_type_id=action_type["id"],
            actor_user_id=ctx.actor_user_id,
            idempotency_key=idempotency_key,
        )

    def _insert_action_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        action_type: ActionTypeRow,
        action_run_id: str,
        command: ActionApplyCommand,
    ) -> ActionRunRow | None:
        return self.action_repository.insert_action_run_or_get_existing(
            transaction=conn,
            record=ActionRunRecord(
                action_run_id=action_run_id,
                tenant_id=ctx.tenant_id,
                action_type_id=action_type["id"],
                action_type_api_name=command.action_api_name,
                actor_user_id=ctx.actor_user_id,
                target_object_type_id=action_type["target_object_type_id"],
                target_object_type_api_name=action_type["target_api_name"],
                target_object_id=command.object_id,
                expected_object_version=command.expected_object_version,
                parameters=command.params,
                status="received",
                idempotency_key=command.idempotency_key,
                request_fingerprint=command.request_fingerprint,
                result=None,
                error=None,
                created_at=_now(),
                completed_at=None,
            ),
        )

    def _replay_existing_action_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        existing: ActionRunRow,
        request_fingerprint: str,
    ) -> ActionApplyOutcome:
        if existing["request_fingerprint"] == request_fingerprint:
            return ActionApplyOutcome(response=action_replay_response(existing))
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
        return ActionApplyOutcome(deferred_error=error)

    def _action_request_error(
        self,
        action_type: ActionTypeRow,
        record: ObjectRecordRow | None,
        expected_object_version: int,
        params: Mapping[str, object],
    ) -> Exception | None:
        if record is None:
            return NotFound("target object not found")
        if record["object_version"] != expected_object_version:
            return ConflictDetected(
                "object version conflict",
                details={
                    "currentObjectVersion": record["object_version"],
                    "expectedObjectVersion": expected_object_version,
                },
            )
        return validate_action_request(action_type, record, params)

    def _fail_action_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        error: Exception,
    ) -> None:
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
        return ActionMutationUnitOfWork(
            action_repository=self.action_repository,
            object_indexing_service=self.object_index_record_mutation_service,
            runtime_service=self.runtime_service,
            policy=self.policy,
        )
