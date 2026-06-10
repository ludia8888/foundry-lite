from __future__ import annotations

from typing import Any

from foundry_lite.application.ports.action_repository import (
    ActionRunRecord,
    ActionWritebackRecord,
    ObjectEditRecord,
    ObjectTargetUpdate,
)
from foundry_lite.application.primitives import (
    MOCK_WRITEBACK_CONNECTOR,
    _new_id,
    _now,
)
from foundry_lite.application.safe_expression import (
    evaluate_safe_expression,
    precondition_expression,
    validate_action_request,
)
from foundry_lite.application.services.base import CoreServiceMixin
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    ExternalSystemError,
    InvariantViolation,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)


class ActionServiceMixin(CoreServiceMixin):
    def apply_action(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: dict[str, Any],
        idempotency_key: str,
        ctx: RequestContext | None = None,
        simulate_writeback_failure: bool = False,
    ) -> dict[str, Any]:
        ctx = ctx or RequestContext()
        if not idempotency_key:
            raise ValidationFailed("idempotency key is required")
        self._require_action_permission(ctx, action_api_name)
        action_run_id = _new_id("action_run")
        deferred_error: Exception | None = None
        response: dict[str, Any] | None = None

        with self.engine.begin() as conn:
            action_type = self._active_action_type(conn, ctx, action_api_name)
            existing = self._existing_action_run(conn, ctx, action_type, idempotency_key)
            if existing is not None:
                return self._action_replay_response(existing)

            self._insert_action_run(
                conn,
                ctx,
                action_type=action_type,
                action_api_name=action_api_name,
                action_run_id=action_run_id,
                object_type=object_type,
                object_id=object_id,
                expected_object_version=expected_object_version,
                params=params,
                idempotency_key=idempotency_key,
            )
            record = self._object_record(conn, ctx, object_type, object_id)
            deferred_error = self._action_request_error(action_type, record, expected_object_version, params)

            if deferred_error is not None:
                self._fail_action_run(conn, ctx, action_run_id, deferred_error)
            elif simulate_writeback_failure:
                deferred_error = self._fail_before_commit_writeback(conn, ctx, action_run_id, idempotency_key)
            else:
                if record is None:
                    raise InvariantViolation("action target record disappeared before commit")
                self._record_writeback(
                    conn,
                    ctx,
                    action_run_id,
                    status="succeeded",
                    idempotency_key=idempotency_key,
                    response={"status_code": 200},
                )
                response = self._commit_action_mutations(
                    conn,
                    ctx,
                    action_type=action_type,
                    action_run_id=action_run_id,
                    record=record,
                    params=params,
                    idempotency_key=idempotency_key,
                )
        if deferred_error is not None:
            raise deferred_error
        if response is None:
            raise InvariantViolation("action did not produce a response")
        return response

    def _require_action_permission(self, ctx: RequestContext, action_api_name: str) -> None:
        permission = f"action:execute:{action_api_name}"
        try:
            self.policy.require(ctx, permission)
        except PermissionDenied:
            with self.engine.begin() as conn:
                self._audit(
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
        conn: Any,
        ctx: RequestContext,
        action_type: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        return self.action_repository.action_run_by_idempotency(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            action_type_id=action_type["id"],
            actor_user_id=ctx.actor_user_id,
            idempotency_key=idempotency_key,
        )

    def _action_replay_response(self, existing: dict[str, Any]) -> dict[str, Any]:
        return {
            "actionRunId": existing["id"],
            "status": existing["status"],
            "idempotentReplay": True,
            "target": {
                "objectType": existing["target_object_type_api_name"],
                "objectId": existing["target_object_id"],
            },
        }

    def _insert_action_run(
        self,
        conn: Any,
        ctx: RequestContext,
        *,
        action_type: dict[str, Any],
        action_api_name: str,
        action_run_id: str,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: dict[str, Any],
        idempotency_key: str,
    ) -> None:
        self.action_repository.insert_action_run(
            transaction=conn,
            record=ActionRunRecord(
                action_run_id=action_run_id,
                tenant_id=ctx.tenant_id,
                action_type_id=action_type["id"],
                action_type_api_name=action_api_name,
                actor_user_id=ctx.actor_user_id,
                target_object_type_id=action_type["target_object_type_id"],
                target_object_type_api_name=object_type,
                target_object_id=object_id,
                expected_object_version=expected_object_version,
                parameters=params,
                status="received",
                idempotency_key=idempotency_key,
                error=None,
                created_at=_now(),
                completed_at=None,
            ),
        )

    def _action_request_error(
        self,
        action_type: dict[str, Any],
        record: dict[str, Any] | None,
        expected_object_version: int,
        params: dict[str, Any],
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
        return self._validate_action_request(action_type, record, params)

    def _fail_action_run(
        self,
        conn: Any,
        ctx: RequestContext,
        action_run_id: str,
        error: Exception,
    ) -> None:
        status = "conflict" if isinstance(error, ConflictDetected) else "failed"
        self.action_repository.update_action_run_terminal(
            transaction=conn,
            action_run_id=action_run_id,
            status=status,
            error=self._error_payload(error),
            completed_at=_now(),
        )
        self._audit(
            conn,
            ctx,
            event_type="action.run.failed",
            resource_type="action_run",
            resource_id=action_run_id,
            action="apply",
            decision="deny" if isinstance(error, PermissionDenied) else "allow",
            after_ref=self._error_payload(error),
            correlation_id=action_run_id,
        )

    def _fail_before_commit_writeback(
        self,
        conn: Any,
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
        self.action_repository.update_action_run_terminal(
            transaction=conn,
            action_run_id=action_run_id,
            status="failed",
            error=self._error_payload(error),
            completed_at=_now(),
        )
        return error

    def _validate_action_request(
        self,
        action_type: dict[str, Any],
        record: dict[str, Any],
        params: dict[str, Any],
    ) -> Exception | None:
        return validate_action_request(action_type, record, params)

    def _precondition_expression(self, precondition: dict[str, Any]) -> str:
        return precondition_expression(precondition)

    def _evaluate_precondition(self, expression: str, properties: dict[str, Any]) -> bool:
        return evaluate_safe_expression(expression, properties)

    def _record_writeback(
        self,
        conn: Any,
        ctx: RequestContext,
        action_run_id: str,
        *,
        status: str,
        idempotency_key: str,
        response: dict[str, Any],
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
                response={**response, "simulated": True},
                status=status,
                idempotency_key=idempotency_key,
                attempts=1,
                created_at=now,
                completed_at=now,
            ),
        )

    def _commit_action_mutations(
        self,
        conn: Any,
        ctx: RequestContext,
        *,
        action_type: dict[str, Any],
        action_run_id: str,
        record: dict[str, Any],
        params: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        patch = self._action_patch(action_type, params)
        previous_values = self._previous_action_values(record, patch)
        self._update_action_target(conn, record, patch)
        edit_id = self._insert_object_edit(conn, ctx, action_run_id, record, patch, previous_values, idempotency_key)
        self.action_repository.update_action_run_terminal(
            transaction=conn,
            action_run_id=action_run_id,
            status="succeeded",
            error=None,
            completed_at=_now(),
        )
        self._publish_action_commit_events(conn, ctx, action_run_id, record, edit_id)
        self._audit(
            conn,
            ctx,
            event_type="action.run.committed",
            resource_type="action_run",
            resource_id=action_run_id,
            action="apply",
            before_ref=previous_values,
            after_ref={"patch": patch, "object_edit_id": edit_id},
            correlation_id=action_run_id,
        )
        return {
            "actionRunId": action_run_id,
            "status": "succeeded",
            "objectEditId": edit_id,
            "target": {"objectType": record["object_type_api_name"], "objectId": record["object_id"]},
            "newObjectVersion": record["object_version"] + 1,
            "patch": patch,
        }

    def _action_patch(self, action_type: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        patch: dict[str, Any] = {}
        for mutation in action_type["definition"].get("mutations", []):
            if mutation["type"] != "setProperty":
                raise ValidationFailed("v1 action supports setProperty only", details=mutation)
            patch[mutation["property"]] = self._mutation_value(mutation, params)
        return patch

    def _mutation_value(self, mutation: dict[str, Any], params: dict[str, Any]) -> Any:
        if "valueFrom" in mutation:
            return self._resolve_value_from(mutation["valueFrom"], params)
        return mutation.get("value")

    def _previous_action_values(self, record: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        return {key: record["properties"].get(key) for key in patch}

    def _update_action_target(
        self,
        conn: Any,
        record: dict[str, Any],
        patch: dict[str, Any],
    ) -> None:
        edit_properties = dict(record["edit_properties"])
        edit_properties.update(patch)
        current = self._merge_properties(conn, record["object_type_id"], record["base_properties"], edit_properties)
        updated = self.action_repository.update_object_target(
            transaction=conn,
            record=ObjectTargetUpdate(
                object_record_id=record["id"],
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
        conn: Any,
        ctx: RequestContext,
        action_run_id: str,
        record: dict[str, Any],
        patch: dict[str, Any],
        previous_values: dict[str, Any],
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
                patch=patch,
                previous_values=previous_values,
                actor_user_id=ctx.actor_user_id,
                idempotency_key=idempotency_key,
                created_at=_now(),
            ),
        )
        return edit_id

    def _publish_action_commit_events(
        self,
        conn: Any,
        ctx: RequestContext,
        action_run_id: str,
        record: dict[str, Any],
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
            self._outbox(
                conn,
                ctx,
                event_type,
                aggregate_type,
                aggregate_id,
                payload,
                idempotency_key=f"{event_type}:{action_run_id}",
                correlation_id=action_run_id,
            )

    def _resolve_value_from(self, expression: str, params: dict[str, Any]) -> Any:
        if expression.startswith("params."):
            key = expression.split(".", 1)[1]
            return params.get(key)
        raise ValidationFailed("unsupported valueFrom expression", details={"expression": expression})
