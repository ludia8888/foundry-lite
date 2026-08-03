"""Application service helpers for action helpers workflows."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Protocol, cast

from foundry_lite.application.action_types import ActionApplyCommand, ActionApplyResponse, ActionPlanSummary
from foundry_lite.application.ports import (
    ACTION_RUN_CONFLICT,
    ACTION_RUN_FAILED,
    ActionMutationDefinition,
    ActionTypeRow,
    ObjectRecordRow,
    StatusTransition,
    TransactionContext,
)
from foundry_lite.application.ports.action_repository import (
    ActionErrorPayload,
    ActionRunRow,
    ObjectPatch,
    ObjectProperties,
)
from foundry_lite.application.primitives import MOCK_WRITEBACK_CONNECTOR, _json_hash
from foundry_lite.application.services.action_validation import action_cache_refresh_hint, action_edit_summary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    ExternalSystemError,
    InvariantViolation,
    PermissionDenied,
    ValidationFailed,
)

RUNTIME_PROFILE_ENV = "FOUNDRY_LITE_RUNTIME_PROFILE"
PROTECTED_RUNTIME_PROFILES = frozenset({"production", "prod", "staging", "stage"})


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


class SupportsAudit(Protocol):
    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str,
        action: str,
        decision: str = "allow",
        after_ref: Mapping[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> None: ...


class SupportsWriteTrafficGate(Protocol):
    def _require_write_traffic_open(
        self,
        ctx: RequestContext,
        *,
        operation: str,
        resource_type: str,
        resource_id: str,
    ) -> None: ...


def require_action_write_open(
    runtime_service: SupportsWriteTrafficGate,
    ctx: RequestContext,
    operation: str,
    resource_type: str,
    resource_id: str,
) -> None:
    runtime_service._require_write_traffic_open(
        ctx,
        operation=operation,
        resource_type=resource_type,
        resource_id=resource_id,
    )


def action_command(
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
    external_writeback_uri: str | None = None,
) -> ActionApplyCommand:
    if not idempotency_key:
        raise ValidationFailed("idempotency key is required")
    normalized_params = dict(params)
    return ActionApplyCommand(
        action_api_name=action_api_name,
        object_type=object_type,
        object_id=object_id,
        expected_object_version=expected_object_version,
        params=normalized_params,
        idempotency_key=idempotency_key,
        request_fingerprint=action_request_fingerprint(
            action_api_name=action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=normalized_params,
            simulate_writeback_failure=simulate_writeback_failure,
            simulate_writeback_retryable=simulate_writeback_retryable,
            simulate_writeback_outcome_unknown=simulate_writeback_outcome_unknown,
            simulate_writeback_compensation_required=simulate_writeback_compensation_required,
        ),
        simulate_writeback_failure=simulate_writeback_failure,
        simulate_writeback_retryable=simulate_writeback_retryable,
        simulate_writeback_outcome_unknown=simulate_writeback_outcome_unknown,
        simulate_writeback_compensation_required=simulate_writeback_compensation_required,
        external_writeback_uri=external_writeback_uri,
    )


def require_failure_injection_allowed(command: ActionApplyCommand) -> None:
    """Block test/demo failure injection from protected runtime profiles."""
    runtime_profile = _runtime_profile(os.environ)
    if not _uses_failure_injection(command) or runtime_profile not in PROTECTED_RUNTIME_PROFILES:
        return
    raise PermissionDenied(
        "action writeback failure injection is disabled for protected runtime profiles",
        details={
            "runtime_profile": runtime_profile,
            "action_api_name": command.action_api_name,
            "failure_injection": _failure_injection_flags(command),
        },
    )


def failure_injection_audit_ref(command: ActionApplyCommand) -> Mapping[str, object]:
    """Return safe audit evidence for a denied failure-injection request."""
    return {
        "runtime_profile": _runtime_profile(os.environ),
        "action_api_name": command.action_api_name,
        "failure_injection": _failure_injection_flags(command),
    }


def action_request_fingerprint(
    *,
    action_api_name: str,
    object_type: str,
    object_id: str,
    expected_object_version: int,
    params: Mapping[str, object],
    simulate_writeback_failure: bool = False,
    simulate_writeback_retryable: bool = False,
    simulate_writeback_outcome_unknown: bool = False,
    simulate_writeback_compensation_required: bool = False,
) -> str:
    return _json_hash(
        {
            "version": 1,
            "actionApiName": action_api_name,
            "objectType": object_type,
            "objectId": object_id,
            "expectedObjectVersion": expected_object_version,
            "params": dict(params),
            "simulateWritebackCompensationRequired": simulate_writeback_compensation_required,
            "simulateWritebackFailure": simulate_writeback_failure,
            "simulateWritebackOutcomeUnknown": simulate_writeback_outcome_unknown,
            "simulateWritebackRetryable": simulate_writeback_retryable,
        }
    )


def _uses_failure_injection(command: ActionApplyCommand) -> bool:
    return any(_failure_injection_flags(command).values())


def _failure_injection_flags(command: ActionApplyCommand) -> dict[str, bool]:
    return {
        "simulate_writeback_failure": command.simulate_writeback_failure,
        "simulate_writeback_retryable": command.simulate_writeback_retryable,
        "simulate_writeback_outcome_unknown": command.simulate_writeback_outcome_unknown,
        "simulate_writeback_compensation_required": command.simulate_writeback_compensation_required,
    }


def _runtime_profile(environ: Mapping[str, str]) -> str:
    value = environ.get(RUNTIME_PROFILE_ENV, "local")
    return value.strip().casefold().replace("_", "-")


def action_replay_response(existing: ActionRunRow) -> ActionApplyResponse:
    result = dict(existing["result"] or {})
    response: ActionApplyResponse = {
        "actionRunId": existing["id"],
        "status": existing["status"],
        "target": {
            "objectType": existing["target_object_type_api_name"],
            "objectId": existing["target_object_id"],
        },
        "idempotentReplay": True,
    }
    plan = result.get("plan")
    if isinstance(plan, Mapping):
        # A v2 (rulesV2) run replays as its multi-object plan summary; the single-object
        # objectEdit/patch/edits/cacheRefresh fields are v1-only and stay absent.
        response["plan"] = cast(ActionPlanSummary, dict(plan))
        return response
    object_edit_id = result.get("objectEditId")
    if isinstance(object_edit_id, str):
        response["objectEditId"] = object_edit_id
    new_object_version = result.get("newObjectVersion")
    if isinstance(new_object_version, int):
        response["newObjectVersion"] = new_object_version
    patch = result.get("patch")
    if isinstance(patch, Mapping):
        response["patch"] = patch
    _attach_edit_response(response)
    return response


def audit_idempotency_conflict(
    runtime_service: SupportsAudit,
    conn: TransactionContext,
    ctx: RequestContext,
    existing: ActionRunRow,
    request_fingerprint: str,
) -> None:
    runtime_service._audit(
        conn,
        ctx,
        event_type="action.run.idempotency_conflict",
        resource_type="action_run",
        resource_id=existing["id"],
        action="apply",
        decision="deny",
        after_ref={
            "idempotency_key": existing["idempotency_key"],
            "existing_request_fingerprint": existing["request_fingerprint"],
            "request_fingerprint": request_fingerprint,
        },
        correlation_id=existing["id"],
    )


def writeback_error_payload(
    runtime_service: SupportsErrorPayload,
    error: ExternalSystemError,
    ctx: RequestContext,
    action_run_id: str,
    *,
    connector_id: str = MOCK_WRITEBACK_CONNECTOR,
) -> ActionErrorPayload:
    return runtime_service._error_payload(
        error,
        ctx,
        run_id=action_run_id,
        correlation_id=action_run_id,
        adapter=connector_id,
    )


def action_success_response(
    action_run_id: str,
    record: ObjectRecordRow,
    edit_id: str,
    patch: ObjectPatch,
) -> ActionApplyResponse:
    response: ActionApplyResponse = {
        "actionRunId": action_run_id,
        "status": "succeeded",
        "objectEditId": edit_id,
        "target": {"objectType": record["object_type_api_name"], "objectId": record["object_id"]},
        "newObjectVersion": record["object_version"] + 1,
        "patch": patch,
    }
    _attach_edit_response(response)
    return response


def _attach_edit_response(response: ActionApplyResponse) -> None:
    target = response["target"]
    object_type = str(target["objectType"])
    object_id = str(target["objectId"])
    action_run_id = response["actionRunId"]
    response["edits"] = action_edit_summary(
        object_type,
        object_id,
        object_edit_id=response.get("objectEditId"),
        new_object_version=response.get("newObjectVersion"),
        patch=response.get("patch"),
    )
    response["cacheRefresh"] = action_cache_refresh_hint(
        action_run_id=action_run_id,
        object_type=object_type,
        object_id=object_id,
    )


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


def action_failure_transition(error: Exception) -> StatusTransition:
    if isinstance(error, ConflictDetected):
        return ACTION_RUN_CONFLICT
    return ACTION_RUN_FAILED


def require_action_target_api_name(action_type: ActionTypeRow, requested_object_type: str) -> None:
    expected_object_type = str(action_type["target_api_name"])
    if requested_object_type == expected_object_type:
        return
    raise ValidationFailed(
        "action target object type mismatch",
        details={"expectedObjectType": expected_object_type, "requestedObjectType": requested_object_type},
    )


def action_target_record_error(action_type: ActionTypeRow, record: ObjectRecordRow) -> InvariantViolation | None:
    expected_object_type_id = str(action_type["target_object_type_id"])
    if str(record["object_type_id"]) == expected_object_type_id:
        return None
    return InvariantViolation(
        "action target record object type invariant violated",
        details={
            "expectedObjectTypeId": expected_object_type_id,
            "recordObjectTypeId": str(record["object_type_id"]),
        },
    )


def previous_action_values(record: ObjectRecordRow, patch: ObjectPatch) -> ObjectProperties:
    return {key: record["properties"].get(key) for key in patch}


def resolve_value_from(expression: str, params: Mapping[str, object]) -> object:
    if expression.startswith("params."):
        key = expression.split(".", 1)[1]
        return params.get(key)
    raise ValidationFailed("unsupported valueFrom expression", details={"expression": expression})
