"""Application service helpers for action validation workflows."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.action_types import (
    ActionCacheRefreshHint,
    ActionCriteriaEvaluation,
    ActionEditSummary,
    ActionValidationIssue,
    ActionValidationParameterResult,
    ActionValidationResponse,
    ActionValidationTargetResult,
)
from foundry_lite.application.ports import ActionTypeRow, ObjectRecordRow
from foundry_lite.application.safe_expression import resolve_action_request_parameters, validate_action_request
from foundry_lite.application.services.runtime_error_payloads import scrub_error_mapping, scrub_error_text
from foundry_lite.domain.action_runtime.action_condition_explanation import explain_action_condition
from foundry_lite.domain.action_runtime.action_conditions import StaticActionConditionContext
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, FoundryLiteError, NotFound


def action_validation_response(
    action_type: ActionTypeRow,
    record: ObjectRecordRow | None,
    expected_object_version: int,
    params: Mapping[str, object],
    ctx: RequestContext | None = None,
    *,
    supplemental_error: Exception | None = None,
    linked_object_properties: Mapping[str, object] | None = None,
) -> ActionValidationResponse:
    target = _target_validation(action_type, record, expected_object_version)
    request_error = _request_validation_error(
        action_type,
        record,
        expected_object_version,
        params,
        ctx,
        linked_object_properties or {},
    )
    request_error = request_error or supplemental_error
    parameter_results = _parameter_results(action_type, params, request_error)
    submission_criteria = _submission_criteria(request_error)
    is_valid = target["result"] == "VALID" and _parameters_valid(parameter_results) and not submission_criteria
    return {
        "actionApiName": str(action_type["api_name"]),
        "result": "VALID" if is_valid else "INVALID",
        "target": target,
        "parameters": parameter_results,
        "submissionCriteria": submission_criteria,
        "submissionCriteriaEvaluation": _criteria_evaluation(
            action_type,
            record,
            expected_object_version,
            params,
            ctx,
            linked_object_properties or {},
        ),
    }


def _criteria_evaluation(
    action_type: ActionTypeRow,
    record: ObjectRecordRow | None,
    expected_object_version: int,
    params: Mapping[str, object],
    ctx: RequestContext | None,
    linked_object_properties: Mapping[str, object],
) -> ActionCriteriaEvaluation | None:
    criteria = _mapping(_mapping(action_type.get("definition")).get("submissionCriteria"))
    if not criteria:
        return None
    if record is None or record["object_version"] != expected_object_version:
        return {"status": "NOT_EVALUATED", "reason": "target_unavailable_or_stale", "tree": None}
    effective_params = _effective_criteria_parameters(action_type, record, params, ctx)
    if effective_params is None:
        return {"status": "NOT_EVALUATED", "reason": "parameters_invalid", "tree": None}
    request_context = ctx or RequestContext()
    condition_context = StaticActionConditionContext(
        parameters=effective_params,
        object_properties=_mapping(record.get("properties")),
        actor_user_id=request_context.actor_user_id,
        actor_groups=request_context.roles,
        actor_attributes=request_context.user_attributes,
        linked_object_properties=linked_object_properties,
        parameter_types=_action_parameter_types(action_type),
    )
    tree = explain_action_condition(criteria, condition_context)
    return {"status": "PASSED" if tree["isSatisfied"] else "FAILED", "reason": None, "tree": tree}


def _effective_criteria_parameters(
    action_type: ActionTypeRow,
    record: ObjectRecordRow,
    params: Mapping[str, object],
    ctx: RequestContext | None,
) -> Mapping[str, object] | None:
    try:
        resolution = resolve_action_request_parameters(action_type, record, params, ctx)
    except FoundryLiteError:
        return None
    return resolution.values if resolution is not None else params


def _action_parameter_types(action_type: ActionTypeRow) -> dict[str, str]:
    definition = _mapping(action_type.get("definition"))
    result: dict[str, str] = {}
    for raw in _object_sequence(definition.get("parameters")):
        parameter = _mapping(raw)
        name = parameter.get("apiName")
        data_type = parameter.get("type")
        if isinstance(name, str) and name and isinstance(data_type, str) and data_type:
            result[name] = data_type
    return result


def action_edit_summary(
    object_type: str,
    object_id: str,
    *,
    object_edit_id: str | None,
    new_object_version: int | None,
    patch: Mapping[str, object] | None,
) -> ActionEditSummary:
    patch_payload = dict(patch or {})
    summary: ActionEditSummary = {
        "objectType": object_type,
        "objectId": object_id,
        "patch": patch_payload,
        "changedProperties": sorted(patch_payload),
    }
    if object_edit_id:
        summary["objectEditId"] = object_edit_id
    if new_object_version is not None:
        summary["newObjectVersion"] = new_object_version
    return summary


def action_cache_refresh_hint(
    *,
    action_run_id: str,
    object_type: str,
    object_id: str,
) -> ActionCacheRefreshHint:
    return {
        "objectKeys": [f"objects:{object_type}:{object_id}"],
        "objectTypeKeys": [f"objects:{object_type}:query"],
        "actionRunKeys": [f"actions:runs:{action_run_id}"],
    }


def _target_validation(
    action_type: ActionTypeRow,
    record: ObjectRecordRow | None,
    expected_object_version: int,
) -> ActionValidationTargetResult:
    object_type = str(action_type["target_api_name"])
    object_id = str(record["object_id"]) if record is not None else ""
    current_version = int(record["object_version"]) if record is not None else None
    issues = _target_issues(record, expected_object_version)
    return {
        "result": "VALID" if not issues else "INVALID",
        "objectType": object_type,
        "objectId": object_id,
        "expectedObjectVersion": expected_object_version,
        "currentObjectVersion": current_version,
        "issues": issues,
    }


def _target_issues(
    record: ObjectRecordRow | None,
    expected_object_version: int,
) -> list[ActionValidationIssue]:
    if record is None:
        return [_issue(NotFound("target object not found"))]
    if record["object_version"] != expected_object_version:
        return [
            _issue(
                ConflictDetected(
                    "object version conflict",
                    details={
                        "currentObjectVersion": record["object_version"],
                        "expectedObjectVersion": expected_object_version,
                    },
                )
            )
        ]
    return []


def _request_validation_error(
    action_type: ActionTypeRow,
    record: ObjectRecordRow | None,
    expected_object_version: int,
    params: Mapping[str, object],
    ctx: RequestContext | None,
    linked_object_properties: Mapping[str, object],
) -> Exception | None:
    if record is None or record["object_version"] != expected_object_version:
        return None
    return validate_action_request(
        action_type,
        record,
        params,
        ctx,
        linked_object_properties=linked_object_properties,
    )


def _parameter_results(
    action_type: ActionTypeRow,
    params: Mapping[str, object],
    error: Exception | None,
) -> dict[str, ActionValidationParameterResult]:
    schema = _mapping(action_type.get("parameter_schema"))
    required = set(_string_sequence(schema.get("required")))
    declared = set(_mapping(schema.get("properties")))
    names = sorted(declared | required | set(params))
    return {name: _parameter_result(name, required, error) for name in names}


def _parameter_result(
    name: str,
    required: set[str],
    error: Exception | None,
) -> ActionValidationParameterResult:
    issues = _parameter_issues(name, error)
    return {"result": "VALID" if not issues else "INVALID", "required": name in required, "issues": issues}


def _parameter_issues(name: str, error: Exception | None) -> list[ActionValidationIssue]:
    if not isinstance(error, FoundryLiteError):
        return []
    details = error.details
    if name in _string_sequence(details.get("missing")):
        return [_issue(error, message=f"missing required parameter {name}")]
    if name in _string_sequence(details.get("unexpected")):
        return [_issue(error, message=f"unexpected parameter {name}")]
    if name in _string_sequence(details.get("invalid")):
        return [_issue(error, message=f"invalid parameter type for {name}")]
    return []


def _submission_criteria(error: Exception | None) -> list[ActionValidationIssue]:
    if error is None or _is_parameter_error(error):
        return []
    return [_issue(error)]


def _is_parameter_error(error: Exception) -> bool:
    if not isinstance(error, FoundryLiteError):
        return False
    return any(key in error.details for key in ("missing", "unexpected", "invalid"))


def _parameters_valid(results: Mapping[str, ActionValidationParameterResult]) -> bool:
    return all(item["result"] == "VALID" for item in results.values())


def _issue(error: Exception, *, message: str | None = None) -> ActionValidationIssue:
    if isinstance(error, FoundryLiteError):
        payload: ActionValidationIssue = {
            "code": error.code,
            "message": scrub_error_text(message or error.message),
        }
        if error.details:
            payload["details"] = scrub_error_mapping(error.details)
        return payload
    return {"code": "VALIDATION_FAILED", "message": scrub_error_text(message or str(error))}


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _object_sequence(value: object) -> tuple[object, ...]:
    return tuple(value) if isinstance(value, list | tuple) else ()


def _string_sequence(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str))
