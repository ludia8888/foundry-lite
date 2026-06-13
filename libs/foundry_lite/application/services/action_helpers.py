from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.action_types import ActionApplyCommand, ActionApplyResponse
from foundry_lite.application.ports import ActionMutationDefinition, ActionTypeRow, ObjectRecordRow
from foundry_lite.application.ports.action_repository import (
    ActionErrorPayload,
    ActionRunRow,
    ObjectPatch,
    ObjectProperties,
)
from foundry_lite.application.primitives import MOCK_WRITEBACK_CONNECTOR
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ExternalSystemError, ValidationFailed


class SupportsErrorPayload(Protocol):
    def _error_payload(
        self,
        exc: Exception,
        ctx: RequestContext | None = None,
        *,
        run_id: str | None = None,
        correlation_id: str | None = None,
        adapter: str | None = None,
    ) -> ActionErrorPayload: ...


def action_command(
    action_api_name: str,
    object_type: str,
    object_id: str,
    expected_object_version: int,
    params: Mapping[str, object],
    idempotency_key: str,
    simulate_writeback_failure: bool,
) -> ActionApplyCommand:
    if not idempotency_key:
        raise ValidationFailed("idempotency key is required")
    return ActionApplyCommand(
        action_api_name=action_api_name,
        object_type=object_type,
        object_id=object_id,
        expected_object_version=expected_object_version,
        params=dict(params),
        idempotency_key=idempotency_key,
        simulate_writeback_failure=simulate_writeback_failure,
    )


def action_replay_response(existing: ActionRunRow) -> ActionApplyResponse:
    return {
        "actionRunId": existing["id"],
        "status": existing["status"],
        "idempotentReplay": True,
        "target": {
            "objectType": existing["target_object_type_api_name"],
            "objectId": existing["target_object_id"],
        },
    }


def writeback_error_payload(
    runtime_service: SupportsErrorPayload,
    error: ExternalSystemError,
    ctx: RequestContext,
    action_run_id: str,
) -> ActionErrorPayload:
    return runtime_service._error_payload(
        error,
        ctx,
        run_id=action_run_id,
        correlation_id=action_run_id,
        adapter=MOCK_WRITEBACK_CONNECTOR,
    )


def action_success_response(
    action_run_id: str,
    record: ObjectRecordRow,
    edit_id: str,
    patch: ObjectPatch,
) -> ActionApplyResponse:
    return {
        "actionRunId": action_run_id,
        "status": "succeeded",
        "objectEditId": edit_id,
        "target": {"objectType": record["object_type_api_name"], "objectId": record["object_id"]},
        "newObjectVersion": record["object_version"] + 1,
        "patch": patch,
    }


def action_patch(action_type: ActionTypeRow, params: Mapping[str, object]) -> ObjectPatch:
    patch: dict[str, object] = {}
    for mutation in action_type["definition"].get("mutations", ()):
        if mutation["type"] != "setProperty":
            raise ValidationFailed("v1 action supports setProperty only", details=dict(mutation))
        patch[mutation["property"]] = mutation_value(mutation, params)
    return patch


def mutation_value(mutation: ActionMutationDefinition, params: Mapping[str, object]) -> object:
    if "valueFrom" in mutation:
        return resolve_value_from(mutation["valueFrom"], params)
    return mutation.get("value")


def previous_action_values(record: ObjectRecordRow, patch: ObjectPatch) -> ObjectProperties:
    return {key: record["properties"].get(key) for key in patch}


def resolve_value_from(expression: str, params: Mapping[str, object]) -> object:
    if expression.startswith("params."):
        key = expression.split(".", 1)[1]
        return params.get(key)
    raise ValidationFailed("unsupported valueFrom expression", details={"expression": expression})
