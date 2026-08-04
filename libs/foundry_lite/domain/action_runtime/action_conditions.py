"""Typed, eval-free condition AST for Action Contract v3.

Conditions are persisted as JSON objects and interpreted through this closed
evaluator.  No expression strings, interpolation, or ``eval`` are accepted.
The same AST is used by parameter overrides and submission criteria.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from foundry_lite.domain.errors import ValidationFailed

COMPARISON_OPERATORS = frozenset(
    {"eq", "neq", "in", "notIn", "lt", "lte", "gt", "gte", "contains", "startsWith", "exists"}
)


class ActionConditionContext(Protocol):
    """Values that a condition may read while remaining I/O free."""

    def parameter(self, name: str) -> object: ...

    def object_property(self, name: str) -> object: ...

    def current_user(self, attribute: str | None) -> object: ...


@dataclass(frozen=True, slots=True)
class StaticActionConditionContext:
    """Concrete context used by request validation and form previews."""

    parameters: Mapping[str, object]
    object_properties: Mapping[str, object]
    actor_user_id: str
    actor_groups: tuple[str, ...]

    def parameter(self, name: str) -> object:
        return self.parameters.get(name)

    def object_property(self, name: str) -> object:
        return self.object_properties.get(name)

    def current_user(self, attribute: str | None) -> object:
        if attribute in (None, "id"):
            return self.actor_user_id
        if attribute in {"group", "groups", "roles"}:
            return self.actor_groups
        raise ValidationFailed("unsupported current-user attribute", details={"attribute": attribute})


def evaluate_action_condition(condition: Mapping[str, object], context: ActionConditionContext) -> bool:
    """Evaluate one closed condition node, failing on unknown shapes."""
    if "all" in condition:
        return all(evaluate_action_condition(item, context) for item in _condition_list(condition["all"], "all"))
    if "any" in condition:
        return any(evaluate_action_condition(item, context) for item in _condition_list(condition["any"], "any"))
    if "not" in condition:
        return not evaluate_action_condition(_condition(condition["not"], "not"), context)
    return _evaluate_comparison(condition, context)


def validate_action_condition(condition: Mapping[str, object]) -> None:
    """Validate every node without needing runtime values."""
    if "all" in condition:
        _validate_children(condition["all"], "all")
        return
    if "any" in condition:
        _validate_children(condition["any"], "any")
        return
    if "not" in condition:
        validate_action_condition(_condition(condition["not"], "not"))
        return
    _validate_comparison(condition)


def referenced_condition_parameters(condition: Mapping[str, object]) -> frozenset[str]:
    """Return parameter names read anywhere in a condition tree."""
    if "all" in condition:
        return _children_parameter_refs(condition["all"], "all")
    if "any" in condition:
        return _children_parameter_refs(condition["any"], "any")
    if "not" in condition:
        return referenced_condition_parameters(_condition(condition["not"], "not"))
    refs = {_parameter_ref(condition.get("left")), _parameter_ref(condition.get("right"))}
    return frozenset(ref for ref in refs if ref is not None)


def _evaluate_comparison(condition: Mapping[str, object], context: ActionConditionContext) -> bool:
    operator = _operator(condition)
    left = _condition_value(condition.get("left"), context)
    if operator == "exists":
        return left is not None
    right = _condition_value(condition.get("right"), context)
    return _compare(operator, left, right)


def _compare(operator: str, left: object, right: object) -> bool:
    if operator == "eq":
        return left == right
    if operator == "neq":
        return left != right
    if operator in {"in", "notIn"}:
        result = _contains(right, left)
        return not result if operator == "notIn" else result
    if operator in {"lt", "lte", "gt", "gte"}:
        return _ordered_compare(operator, left, right)
    if operator == "contains":
        return _contains(left, right)
    if operator == "startsWith":
        return isinstance(left, str) and isinstance(right, str) and left.startswith(right)
    raise ValidationFailed("unsupported action condition operator", details={"operator": operator})


def _ordered_compare(operator: str, left: object, right: object) -> bool:
    try:
        if operator == "lt":
            return bool(left < right)  # type: ignore[operator]
        if operator == "lte":
            return bool(left <= right)  # type: ignore[operator]
        if operator == "gt":
            return bool(left > right)  # type: ignore[operator]
        return bool(left >= right)  # type: ignore[operator]
    except TypeError as exc:
        raise ValidationFailed(
            "action condition values are not comparable",
            details={"operator": operator, "leftType": type(left).__name__, "rightType": type(right).__name__},
        ) from exc


def _contains(container: object, member: object) -> bool:
    if isinstance(container, str):
        return isinstance(member, str) and member in container
    if isinstance(container, Sequence):
        return member in container
    if isinstance(container, Mapping):
        return member in container
    return False


def _condition_value(raw: object, context: ActionConditionContext) -> object:
    value = _mapping(raw, "condition value")
    kind = value.get("kind")
    if kind == "literal":
        return value.get("value")
    if kind == "parameter":
        return context.parameter(_required_text(value, "parameter"))
    if kind == "objectProperty":
        return context.object_property(_required_text(value, "property"))
    if kind == "currentUser":
        attribute = value.get("attribute")
        return context.current_user(attribute if isinstance(attribute, str) else None)
    raise ValidationFailed("unsupported action condition value", details={"kind": kind})


def _validate_comparison(condition: Mapping[str, object]) -> None:
    operator = _operator(condition)
    _validate_condition_value(condition.get("left"))
    if operator != "exists":
        _validate_condition_value(condition.get("right"))


def _validate_condition_value(raw: object) -> None:
    value = _mapping(raw, "condition value")
    kind = value.get("kind")
    if kind == "literal":
        return
    if kind == "parameter":
        _required_text(value, "parameter")
        return
    if kind == "objectProperty":
        _required_text(value, "property")
        return
    if kind == "currentUser":
        return
    raise ValidationFailed("unsupported action condition value", details={"kind": kind})


def _validate_children(raw: object, field: str) -> None:
    children = _condition_list(raw, field)
    if not children:
        raise ValidationFailed("action condition group cannot be empty", details={"field": field})
    for child in children:
        validate_action_condition(child)


def _children_parameter_refs(raw: object, field: str) -> frozenset[str]:
    refs: set[str] = set()
    for child in _condition_list(raw, field):
        refs.update(referenced_condition_parameters(child))
    return frozenset(refs)


def _operator(condition: Mapping[str, object]) -> str:
    operator = condition.get("op")
    if not isinstance(operator, str) or operator not in COMPARISON_OPERATORS:
        raise ValidationFailed("unsupported action condition operator", details={"operator": operator})
    return operator


def _parameter_ref(raw: object) -> str | None:
    if not isinstance(raw, Mapping):
        return None
    payload = cast(Mapping[str, object], raw)
    if payload.get("kind") != "parameter":
        return None
    value = payload.get("parameter")
    return value if isinstance(value, str) and value else None


def _condition(raw: object, field: str) -> Mapping[str, object]:
    return _mapping(raw, f"condition {field}")


def _condition_list(raw: object, field: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise ValidationFailed("action condition group must be a list", details={"field": field})
    values = cast(Sequence[object], raw)
    return tuple(_mapping(item, f"condition {field}") for item in values)


def _mapping(raw: object, field: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise ValidationFailed(f"{field} must be an object")
    return cast(Mapping[str, object], raw)


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationFailed("action condition field is required", details={"field": key})
    return value
