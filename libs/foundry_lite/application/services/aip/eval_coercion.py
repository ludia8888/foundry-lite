"""Application service helpers for eval coercion workflows."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.services.aip.eval_types import AiEvalError, EvalJsonObject


def json_object(value: object) -> EvalJsonObject:
    if isinstance(value, Mapping):
        return value
    return {}


def number_field(row: Mapping[str, object], field_name: str) -> float:
    value = row.get(field_name)
    if isinstance(value, int | float):
        return float(value)
    raise AiEvalError("invalid_eval_summary", f"eval summary field {field_name} must be numeric")
