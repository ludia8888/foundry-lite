"""Application service helpers for action validation workflows."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.action_types import (
    ActionCacheRefreshHint,
    ActionEditSummary,
    ActionValidationIssue,
    ActionValidationParameterResult,
    ActionValidationResponse,
    ActionValidationTargetResult,
)
from foundry_lite.application.ports import ActionTypeRow, ObjectRecordRow
from foundry_lite.application.safe_expression import validate_action_request
from foundry_lite.domain.errors import ConflictDetected, FoundryLiteError, NotFound


def action_validation_response(
    action_type: ActionTypeRow,
    record: ObjectRecordRow | None,
    expected_object_version: int,
    params: Mapping[str, object],
) -> ActionValidationResponse:
    target = _target_validation(action_type, record, expected_object_version)
    request_error = _request_validation_error(action_type, record, expected_object_version, params)
    parameter_results = _parameter_results(action_type, params, request_error)
    submission_criteria = _submission_criteria(request_error)
    is_valid = target["result"] == "VALID" and _parameters_valid(parameter_results) and not submission_criteria
    return {
        "actionApiName": str(action_type["api_name"]),
        "result": "VALID" if is_valid else "INVALID",
        "target": target,
        "parameters": parameter_results,
        "submissionCriteria": submission_criteria,
    }


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
) -> Exception | None:
    if record is None or record["object_version"] != expected_object_version:
        return None
    return validate_action_request(action_type, record, params)


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
        payload: ActionValidationIssue = {"code": error.code, "message": message or error.message}
        if error.details:
            payload["details"] = error.details
        return payload
    return {"code": "VALIDATION_FAILED", "message": message or str(error)}


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _string_sequence(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str))
