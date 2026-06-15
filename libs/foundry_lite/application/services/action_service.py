from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.action_types import ActionApplyCommand, ActionApplyOutcome, ActionApplyResponse
from foundry_lite.application.ports import ActionTypeRow, ObjectRecordRow, TransactionContext
from foundry_lite.application.ports.action_repository import (
    ActionRunRecord,
    ActionRunRow,
    ActionWritebackPayload,
    ActionWritebackRecord,
    ObjectEditRecord,
    ObjectPatch,
    ObjectProperties,
    ObjectTargetUpdate,
)
from foundry_lite.application.primitives import (
    MOCK_WRITEBACK_CONNECTOR,
    _new_id,
    _now,
)
from foundry_lite.application.safe_expression import validate_action_request
from foundry_lite.application.services.action_helpers import (
    action_command,
    action_patch,
    action_replay_response,
    action_success_response,
    audit_idempotency_conflict,
    previous_action_values,
    writeback_error_payload,
)
from foundry_lite.application.services.action_protocols import (
    ActionObjectIndexer,
    ActionObjectRecordLookup,
    ActionOntologyLookup,
    ActionRuntimeBoundary,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    ExternalSystemError,
    InvariantViolation,
    NotFound,
    PermissionDenied,
)


class ActionService(CoreService):
    required_dependencies = ("engine", "policy", "action_repository")
    required_collaborators = (
        "object_indexing_service",
        "object_records_service",
        "ontology_service",
        "runtime_service",
    )
    object_indexing_service: ActionObjectIndexer
    object_records_service: ActionObjectRecordLookup
    ontology_service: ActionOntologyLookup
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
    ) -> ActionApplyResponse:
        ctx = ctx or RequestContext()
        command = action_command(
            action_api_name,
            object_type,
            object_id,
            expected_object_version,
            params,
            idempotency_key,
            simulate_writeback_failure,
        )
        self._require_action_permission(ctx, command.action_api_name)
        action_run_id = _new_id("action_run")
        outcome = self._run_action_command(ctx, command, action_run_id)
        if outcome.deferred_error is not None:
            raise outcome.deferred_error
        if outcome.response is None:
            raise InvariantViolation("action did not produce a response")
        return outcome.response

    def _run_action_command(
        self,
        ctx: RequestContext,
        command: ActionApplyCommand,
        action_run_id: str,
    ) -> ActionApplyOutcome:
        with self.engine.begin() as conn:
            action_type = self.ontology_service._active_action_type(conn, ctx, command.action_api_name)
            existing = self._existing_action_run(conn, ctx, action_type, command.idempotency_key)
            if existing is not None:
                return self._replay_existing_action_run(conn, ctx, existing, command.request_fingerprint)
            raced_existing = self._insert_action_run(
                conn,
                ctx,
                action_type=action_type,
                action_run_id=action_run_id,
                command=command,
            )
            if raced_existing is not None:
                return self._replay_existing_action_run(conn, ctx, raced_existing, command.request_fingerprint)
            outcome = self._complete_received_action_run(
                conn,
                ctx,
                action_type=action_type,
                action_run_id=action_run_id,
                command=command,
            )
        return outcome

    def _complete_received_action_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        action_type: ActionTypeRow,
        action_run_id: str,
        command: ActionApplyCommand,
    ) -> ActionApplyOutcome:
        record = self.object_records_service._object_record(conn, ctx, command.object_type, command.object_id)
        deferred_error = self._action_request_error(
            action_type, record, command.expected_object_version, command.params
        )
        if deferred_error is not None:
            self._fail_action_run(conn, ctx, action_run_id, deferred_error)
            return ActionApplyOutcome(deferred_error=deferred_error)
        if command.simulate_writeback_failure:
            error = self._fail_before_commit_writeback(conn, ctx, action_run_id, command.idempotency_key)
            return ActionApplyOutcome(deferred_error=error)
        if record is None:
            raise InvariantViolation("action target record disappeared before commit")
        self._record_writeback(
            conn,
            ctx,
            action_run_id,
            status="succeeded",
            idempotency_key=command.idempotency_key,
            response={"status_code": 200},
        )
        response = self._commit_action_mutations(
            conn,
            ctx,
            action_type=action_type,
            action_run_id=action_run_id,
            record=record,
            params=command.params,
            idempotency_key=command.idempotency_key,
        )
        return ActionApplyOutcome(response=response)

    def _require_action_permission(self, ctx: RequestContext, action_api_name: str) -> None:
        permission = f"action:execute:{action_api_name}"
        try:
            self.policy.require(ctx, permission)
        except PermissionDenied:
            with self.engine.begin() as conn:
                self.runtime_service._audit(
                    conn,
                    ctx,
                    event_type="permission.denied",
                    resource_type="action_type",
                    resource_id=action_api_name,
                    action="apply",
                    decision="deny",
                    after_ref={"permission": permission},
                )
            raise

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
                target_object_type_api_name=command.object_type,
                target_object_id=command.object_id,
                expected_object_version=command.expected_object_version,
                parameters=command.params,
                status="received",
                idempotency_key=command.idempotency_key,
                request_fingerprint=command.request_fingerprint,
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
        status = "conflict" if isinstance(error, ConflictDetected) else "failed"
        self.action_repository.update_action_run_terminal(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            action_run_id=action_run_id,
            status=status,
            error=self.runtime_service._error_payload(error, ctx, run_id=action_run_id, correlation_id=action_run_id),
            completed_at=_now(),
        )
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

    def _fail_before_commit_writeback(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        idempotency_key: str,
    ) -> ExternalSystemError:
        error = ExternalSystemError(
            "mock before-commit writeback failed",
            details={"connector": MOCK_WRITEBACK_CONNECTOR, "simulated": True},
        )
        self._record_writeback(
            conn,
            ctx,
            action_run_id,
            status="failed",
            idempotency_key=idempotency_key,
            response={"status_code": 500},
        )
        error_payload = dict(writeback_error_payload(self.runtime_service, error, ctx, action_run_id))
        self.action_repository.update_action_run_terminal(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            action_run_id=action_run_id,
            status="failed",
            error=error_payload,
            completed_at=_now(),
        )
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="action.run.failed",
            resource_type="action_run",
            resource_id=action_run_id,
            action="apply",
            after_ref=error_payload,
            correlation_id=action_run_id,
        )
        return error

    def _record_writeback(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        *,
        status: str,
        idempotency_key: str,
        response: ActionWritebackPayload,
    ) -> None:
        now = _now()
        self.action_repository.insert_action_writeback(
            transaction=conn,
            record=ActionWritebackRecord(
                writeback_id=_new_id("writeback"),
                tenant_id=ctx.tenant_id,
                action_run_id=action_run_id,
                mode="before_commit",
                connector_id=MOCK_WRITEBACK_CONNECTOR,
                request={"connector": MOCK_WRITEBACK_CONNECTOR, "simulated": True, "networkCall": False},
                response={**dict(response), "simulated": True},
                status=status,
                idempotency_key=idempotency_key,
                attempts=1,
                created_at=now,
                completed_at=now,
            ),
        )

    def _commit_action_mutations(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        action_type: ActionTypeRow,
        action_run_id: str,
        record: ObjectRecordRow,
        params: Mapping[str, object],
        idempotency_key: str,
    ) -> ActionApplyResponse:
        patch = dict(action_patch(action_type, params))
        previous_values = dict(previous_action_values(record, patch))
        self._update_action_target(conn, record, patch)
        edit_id = self._insert_object_edit(conn, ctx, action_run_id, record, patch, previous_values, idempotency_key)
        self.action_repository.update_action_run_terminal(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            action_run_id=action_run_id,
            status="succeeded",
            error=None,
            completed_at=_now(),
        )
        self._publish_action_commit_events(conn, ctx, action_run_id, record, edit_id)
        self._audit_action_commit(conn, ctx, action_run_id, record, previous_values, patch, edit_id)
        return action_success_response(action_run_id, record, edit_id, patch)

    def _audit_action_commit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        record: ObjectRecordRow,
        previous_values: ObjectProperties,
        patch: ObjectPatch,
        edit_id: str,
    ) -> None:
        object_type = record["object_type_api_name"]
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="action.run.committed",
            resource_type="action_run",
            resource_id=action_run_id,
            action="apply",
            before_ref=self.policy.mask_sensitive_properties(object_type, dict(previous_values)),
            after_ref={
                "patch": self.policy.mask_sensitive_properties(object_type, dict(patch)),
                "object_edit_id": edit_id,
            },
            correlation_id=action_run_id,
        )

    def _update_action_target(
        self,
        conn: TransactionContext,
        record: ObjectRecordRow,
        patch: ObjectPatch,
    ) -> None:
        edit_properties = dict(record["edit_properties"])
        edit_properties.update(patch)
        current = self.object_indexing_service._merge_properties(
            conn, record["object_type_id"], record["base_properties"], edit_properties
        )
        updated = self.action_repository.update_object_target(
            transaction=conn,
            record=ObjectTargetUpdate(
                object_record_id=record["id"],
                tenant_id=record["tenant_id"],
                expected_object_version=record["object_version"],
                edit_properties=edit_properties,
                properties=current,
                next_object_version=record["object_version"] + 1,
                updated_at=_now(),
            ),
        )
        if not updated:
            raise ConflictDetected("object version conflict during commit")

    def _insert_object_edit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        record: ObjectRecordRow,
        patch: ObjectPatch,
        previous_values: ObjectProperties,
        idempotency_key: str,
    ) -> str:
        edit_id = _new_id("edit")
        self.action_repository.insert_object_edit(
            transaction=conn,
            record=ObjectEditRecord(
                edit_id=edit_id,
                tenant_id=ctx.tenant_id,
                action_run_id=action_run_id,
                object_type_id=record["object_type_id"],
                object_type_api_name=record["object_type_api_name"],
                object_id=record["object_id"],
                edit_type="set_property",
                patch=dict(patch),
                previous_values=dict(previous_values),
                actor_user_id=ctx.actor_user_id,
                idempotency_key=idempotency_key,
                created_at=_now(),
            ),
        )
        return edit_id

    def _publish_action_commit_events(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        record: ObjectRecordRow,
        edit_id: str,
    ) -> None:
        payload = {
            "actionRunId": action_run_id,
            "objectType": record["object_type_api_name"],
            "objectId": record["object_id"],
            "editId": edit_id,
        }
        for event_type in ["action.run.committed", "object.edit.committed", "object.changed"]:
            aggregate_type = "action_run" if event_type == "action.run.committed" else "object"
            aggregate_id = action_run_id if event_type == "action.run.committed" else record["object_id"]
            self.runtime_service._outbox(
                conn,
                ctx,
                event_type,
                aggregate_type,
                aggregate_id,
                payload,
                idempotency_key=f"{event_type}:{action_run_id}",
                correlation_id=action_run_id,
            )
