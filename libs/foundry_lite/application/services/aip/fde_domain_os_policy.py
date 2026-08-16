"""Validation and executable compilation for bounded Domain OS policies."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.services.aip.fde_tool_result import FdePlatformToolError
from foundry_lite.domain.action_runtime.action_conditions import (
    COMPARISON_OPERATORS,
    validate_action_condition,
)
from foundry_lite.domain.error_redaction import scrub_error_text
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.scalar_values import matches_scalar_type

JsonObject = Mapping[str, object]
_MAX_ACTIONS = 20
_MAX_FIELDS = 23
_MAX_POLICIES = 20
_ENFORCEMENT = frozenset({"blocking", "warning", "manual_review"})
_COLLECTION_ONLY_OPERATORS = frozenset({"containsAny", "eachIs", "eachIsNot"})
_OPERATORS = COMPARISON_OPERATORS - _COLLECTION_ONLY_OPERATORS
_TEXT_OPERATORS = frozenset({"contains", "startsWith", "matches"})


def compile_domain_policies(
    brief: JsonObject,
    primary_record: JsonObject | None,
    actions: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Validate policy references and mark actions that need human confirmation."""

    values = _mapping_items(brief.get("policies") or [], "domainBrief.policies", _MAX_POLICIES)
    fields = _record_fields(primary_record)
    action_names = {str(action["apiName"]) for action in actions}
    policies = [_policy(value, fields, action_names) for value in values]
    manual_actions = {
        action_name
        for policy in policies
        if policy["enforcement"] == "manual_review"
        for action_name in _text_items(policy.get("appliesToActions"), "policy.appliesToActions", _MAX_ACTIONS)
    }
    for action in actions:
        if action["apiName"] in manual_actions:
            action["requiresApproval"] = True
    return policies


def policy_preconditions(action: JsonObject, policies: list[dict[str, object]]) -> list[dict[str, object]]:
    """Compile only explicit blocking conditions into typed Action preconditions."""

    result: list[dict[str, object]] = []
    action_name = str(action["apiName"])
    for policy in policies:
        applies_to = _text_items(policy.get("appliesToActions"), "policy.appliesToActions", _MAX_ACTIONS)
        if policy.get("automationStatus") != "executable_precondition" or action_name not in applies_to:
            continue
        result.extend(_condition_precondition(condition, policy) for condition in _conditions(policy))
    return result


def _policy(value: JsonObject, fields: Mapping[str, str], action_names: set[str]) -> dict[str, object]:
    enforcement = str(value.get("enforcement") or "blocking")
    if enforcement not in _ENFORCEMENT:
        raise FdePlatformToolError("schema_invalid", f"지원하지 않는 규칙 적용 방식입니다: {enforcement}")
    applies_to = _text_items(value.get("appliesToActions") or [], "policy.appliesToActions", _MAX_ACTIONS)
    unknown_actions = sorted(set(applies_to) - action_names)
    if unknown_actions:
        raise FdePlatformToolError(
            "schema_invalid", f"규칙이 없는 업무 버튼을 참조합니다: {', '.join(unknown_actions)}"
        )
    condition_rows = _mapping_items(value.get("conditions") or [], "policy.conditions", 12)
    conditions = [_policy_condition(item, fields) for item in condition_rows]
    return {
        "name": _required_text(value, "name", 160),
        "statement": _required_text(value, "statement", 1_000),
        "enforcement": enforcement,
        "evidence": _optional_text(value, "evidence", 500),
        "appliesToActions": applies_to,
        "conditions": conditions,
        "automationStatus": _automation_status(enforcement, applies_to, conditions),
    }


def _policy_condition(value: JsonObject, fields: Mapping[str, str]) -> dict[str, object]:
    property_name = _required_text(value, "propertyApiName", 64)
    if property_name not in fields:
        raise FdePlatformToolError("schema_invalid", f"규칙이 없는 업무 정보를 참조합니다: {property_name}")
    operator = _required_text(value, "operator", 16)
    if operator not in _OPERATORS:
        raise FdePlatformToolError("schema_invalid", f"지원하지 않는 규칙 비교 방식입니다: {operator}")
    _validate_value_presence(value, operator)
    condition_value = value.get("value")
    _validate_condition_value(fields[property_name], operator, condition_value)
    _validate_runtime_condition(property_name, operator, condition_value)
    return {"propertyApiName": property_name, "operator": operator, "value": condition_value}


def _validate_value_presence(value: JsonObject, operator: str) -> None:
    has_value = "value" in value
    if operator == "exists" and has_value:
        raise FdePlatformToolError("schema_invalid", "exists 규칙에는 value 항목을 입력하지 않습니다.")
    if operator != "exists" and not has_value:
        raise FdePlatformToolError("schema_invalid", f"{operator} 규칙에는 비교할 값이 필요합니다.")


def _validate_condition_value(field_type: str, operator: str, value: object) -> None:
    if operator == "exists":
        return
    values = _condition_values(operator, value)
    _validate_condition_operator(field_type, operator)
    if not all(_matches_field_type(item, field_type) for item in values):
        raise FdePlatformToolError("schema_invalid", f"규칙 값이 {field_type} 업무 정보 형식과 맞지 않습니다.")


def _condition_values(operator: str, value: object) -> Sequence[object]:
    if operator in {"in", "notIn"}:
        if not isinstance(value, list) or not value or len(value) > 50:
            raise FdePlatformToolError("schema_invalid", f"{operator} 규칙 값은 비어 있지 않은 목록이어야 합니다.")
        return value
    if isinstance(value, list):
        raise FdePlatformToolError("schema_invalid", f"{operator} 규칙 값은 목록일 수 없습니다.")
    return (value,)


def _validate_condition_operator(field_type: str, operator: str) -> None:
    if operator in {"lt", "lte", "gt", "gte"} and field_type not in {"integer", "float", "date", "timestamp"}:
        raise FdePlatformToolError("schema_invalid", f"{field_type} 업무 정보에는 크기 비교 규칙을 사용할 수 없습니다.")
    if operator in _TEXT_OPERATORS and field_type != "string":
        raise FdePlatformToolError(
            "schema_invalid", f"{field_type} 업무 정보에는 텍스트 비교 규칙을 사용할 수 없습니다."
        )


def _validate_runtime_condition(property_name: str, operator: str, value: object) -> None:
    condition: dict[str, object] = {
        "op": operator,
        "left": {"kind": "objectProperty", "property": property_name},
    }
    if operator != "exists":
        condition["right"] = {"kind": "literal", "value": value}
    try:
        validate_action_condition(condition)
    except ValidationFailed as exc:
        raise FdePlatformToolError(
            "schema_invalid", f"실행할 수 없는 규칙입니다: {scrub_error_text(str(exc))}"
        ) from exc


def _matches_field_type(value: object, field_type: str) -> bool:
    return matches_scalar_type(field_type, value)


def _record_fields(record: JsonObject | None) -> dict[str, str]:
    if record is None:
        return {}
    return {
        str(field["apiName"]): str(field["type"])
        for field in _mapping_items(record.get("fields"), "record.fields", _MAX_FIELDS)
    }


def _automation_status(enforcement: str, actions: list[str], conditions: list[dict[str, object]]) -> str:
    if enforcement == "blocking" and actions and conditions:
        return "executable_precondition"
    if enforcement == "manual_review" and actions:
        return "human_confirmation"
    return "documented_for_review"


def _conditions(policy: JsonObject) -> list[dict[str, object]]:
    return _mapping_items(policy.get("conditions"), "policy.conditions", 12)


def _condition_precondition(condition: JsonObject, policy: JsonObject) -> dict[str, object]:
    result: dict[str, object] = {
        "op": condition["operator"],
        "left": {"kind": "objectProperty", "property": condition["propertyApiName"]},
        "message": policy["statement"],
        "policyName": policy["name"],
    }
    if condition["operator"] != "exists":
        result["right"] = {"kind": "literal", "value": condition["value"]}
    return result


def _mapping_items(value: object, field: str, limit: int) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise FdePlatformToolError("schema_invalid", f"{field} must be a list")
    if len(value) > limit or not all(isinstance(item, Mapping) for item in value):
        raise FdePlatformToolError("schema_invalid", f"{field} exceeds its bounded object-list contract")
    return [{str(name): item for name, item in row.items()} for row in value if isinstance(row, Mapping)]


def _text_items(value: object, field: str, limit: int) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes) or len(value) > limit:
        raise FdePlatformToolError("schema_invalid", f"{field} must be a bounded text list")
    items = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if len(items) != len(value):
        raise FdePlatformToolError("schema_invalid", f"{field} must contain non-empty text values")
    return items


def _required_text(value: JsonObject, field: str, limit: int) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip() or len(item) > limit:
        raise FdePlatformToolError("schema_invalid", f"{field} must be non-empty text up to {limit} characters")
    return item.strip()


def _optional_text(value: JsonObject, field: str, limit: int) -> str:
    item = value.get(field)
    if item is None:
        return ""
    if not isinstance(item, str) or len(item) > limit:
        raise FdePlatformToolError("schema_invalid", f"{field} must be text up to {limit} characters")
    return item.strip()


__all__ = ["compile_domain_policies", "policy_preconditions"]
