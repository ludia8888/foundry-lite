"""Application-layer models and helpers for safe expression."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from foundry_lite.domain.errors import ValidationFailed

OBJECT_IN_PATTERN = re.compile(r"^object\.([A-Za-z_][A-Za-z0-9_]*)\s+in\s+\[(.*)]$")
OBJECT_EQ_PATTERN = re.compile(r"^object\.([A-Za-z_][A-Za-z0-9_]*)\s*==\s*'([^']*)'$")


def precondition_expression(precondition: Mapping[str, object]) -> str:
    # Use explicit None checks so an empty-string safeExpression does not
    # silently fall back to ``expression`` or ``cel`` — root-cause hardening
    # surfaced by hypothesis P5 property tests.
    if "safeExpression" in precondition:
        return _string_or_empty(precondition["safeExpression"])
    if "expression" in precondition:
        return _string_or_empty(precondition["expression"])
    return _string_or_empty(precondition.get("cel", ""))


def evaluate_safe_expression(expression: str, properties: Mapping[str, object]) -> bool:
    match = OBJECT_IN_PATTERN.match(expression)
    if match:
        prop = match.group(1)
        raw_values = match.group(2)
        values = [part.strip().strip("'\"") for part in raw_values.split(",")]
        return properties.get(prop) in values
    match = OBJECT_EQ_PATTERN.match(expression)
    if match:
        return properties.get(match.group(1)) == match.group(2)
    raise ValidationFailed("unsupported safe expression", details={"expression": expression})


def validate_action_request(
    action_type: Mapping[str, object],
    record: Mapping[str, object],
    params: Mapping[str, object],
) -> Exception | None:
    schema = _mapping_or_empty(action_type.get("parameter_schema"))
    required = _string_sequence(schema.get("required", ()))
    missing = [name for name in required if name not in params]
    if missing:
        return ValidationFailed("missing required action parameters", details={"missing": missing})
    properties = schema.get("properties")
    if properties is not None:
        # A declared parameter schema rejects unknown parameters and enforces the
        # declared type, so a stray or wrong-typed value never reaches the patch.
        type_error = _validate_parameter_values(_mapping_or_empty(properties), params)
        if type_error is not None:
            return type_error
    definition = _mapping_or_empty(action_type.get("definition"))
    for raw_precondition in _object_sequence(definition.get("preconditions", ())):
        precondition = _mapping_or_empty(raw_precondition)
        expression = precondition_expression(precondition)
        if not evaluate_safe_expression(expression, _mapping_or_empty(record.get("properties"))):
            return ValidationFailed(
                _string_or_empty(precondition.get("message")) or "action precondition failed",
                details={"expression": expression},
            )
    return None


def _validate_parameter_values(properties: Mapping[str, object], params: Mapping[str, object]) -> Exception | None:
    unexpected = sorted(name for name in params if name not in properties)
    if unexpected:
        return ValidationFailed("unexpected action parameters", details={"unexpected": unexpected})
    invalid = sorted(
        name
        for name, value in params.items()
        if not _value_matches_type(value, _string_or_empty(_mapping_or_empty(properties.get(name)).get("type")))
    )
    if invalid:
        return ValidationFailed("invalid action parameter types", details={"invalid": invalid})
    return None


def _value_matches_type(value: object, declared_type: str) -> bool:
    if declared_type == "string":
        return isinstance(value, str)
    if declared_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared_type == "float":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if declared_type == "boolean":
        return isinstance(value, bool)
    # Unknown/unspecified declared types stay permissive (e.g. future date types).
    return True


def _string_or_empty(value: object) -> str:
    return value if isinstance(value, str) else ""


def _mapping_or_empty(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _object_sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, str):
        return value
    return ()


def _string_sequence(value: object) -> tuple[str, ...]:
    return tuple(item for item in _object_sequence(value) if isinstance(item, str))
