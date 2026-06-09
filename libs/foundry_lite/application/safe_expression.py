from __future__ import annotations

import re
from typing import Any

from foundry_lite.domain.errors import ValidationFailed

OBJECT_IN_PATTERN = re.compile(r"^object\.([A-Za-z_][A-Za-z0-9_]*)\s+in\s+\[(.*)]$")
OBJECT_EQ_PATTERN = re.compile(r"^object\.([A-Za-z_][A-Za-z0-9_]*)\s*==\s*'([^']*)'$")


def precondition_expression(precondition: dict[str, Any]) -> str:
    return precondition.get("safeExpression") or precondition.get("expression") or precondition.get("cel", "")


def evaluate_safe_expression(expression: str, properties: dict[str, Any]) -> bool:
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
    action_type: dict[str, Any],
    record: dict[str, Any],
    params: dict[str, Any],
) -> Exception | None:
    schema = action_type["parameter_schema"]
    missing = [name for name in schema["required"] if name not in params]
    if missing:
        return ValidationFailed("missing required action parameters", details={"missing": missing})
    for precondition in action_type["definition"].get("preconditions", []):
        expression = precondition_expression(precondition)
        if not evaluate_safe_expression(expression, record["properties"]):
            return ValidationFailed(
                precondition.get("message", "action precondition failed"),
                details={"expression": expression},
            )
    return None
