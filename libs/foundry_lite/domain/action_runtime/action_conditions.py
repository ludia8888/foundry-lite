"""Typed, eval-free condition AST for Action Contract v3.

Conditions are persisted as JSON objects and interpreted through this closed
evaluator.  No expression strings, interpolation, or ``eval`` are accepted.
The same AST is used by parameter overrides and submission criteria.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Protocol, cast

from foundry_lite.domain.errors import ValidationFailed

# Mirrors Palantir's submission-criteria operator set. Their single-value operators are
# is / is not / matches (regex) / is less than / is greater than or equals, and their
# multi-value operators are includes / includes any / is included in / each is / each is not.
# `matches`, `eachIs`, and `eachIsNot` exist here for that parity; `startsWith` predates it.
COMPARISON_OPERATORS = frozenset(
    {
        "eq",
        "neq",
        "in",
        "notIn",
        "lt",
        "lte",
        "gt",
        "gte",
        "contains",
        "containsAny",
        "startsWith",
        "matches",
        "eachIs",
        "eachIsNot",
        "exists",
    }
)


class ActionConditionContext(Protocol):
    """Values that a condition may read while remaining I/O free."""

    def parameter(self, name: str) -> object: ...

    def object_property(self, name: str) -> object: ...

    def current_user(self, attribute: str | None) -> object: ...

    def linked_object_property(
        self, link_type: str, direction: str, property_name: str, aggregation: str
    ) -> object: ...


@dataclass(frozen=True, slots=True, order=True)
class LinkedObjectPropertyReference:
    """One permission-scoped linked-object value source used by criteria."""

    link_type: str
    direction: str
    property_name: str
    aggregation: str

    @property
    def key(self) -> str:
        return linked_object_property_key(self.link_type, self.direction, self.property_name, self.aggregation)


@dataclass(frozen=True, slots=True)
class StaticActionConditionContext:
    """Concrete context used by request validation and form previews."""

    parameters: Mapping[str, object]
    object_properties: Mapping[str, object]
    actor_user_id: str
    actor_groups: tuple[str, ...]
    actor_attributes: Mapping[str, object] = field(default_factory=dict[str, object])
    linked_object_properties: Mapping[str, object] = field(default_factory=dict[str, object])

    def parameter(self, name: str) -> object:
        return self.parameters.get(name)

    def object_property(self, name: str) -> object:
        return self.object_properties.get(name)

    def current_user(self, attribute: str | None) -> object:
        if attribute is None or attribute == "id":
            return self.actor_user_id
        if attribute in {"group", "groups", "roles"}:
            return self.actor_groups
        return self.actor_attributes.get(attribute)

    def linked_object_property(self, link_type: str, direction: str, property_name: str, aggregation: str) -> object:
        key = linked_object_property_key(link_type, direction, property_name, aggregation)
        if key not in self.linked_object_properties:
            raise ValidationFailed(
                "linked-object condition value was not resolved",
                details={"linkType": link_type, "direction": direction, "property": property_name},
            )
        return self.linked_object_properties[key]


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
        child = _condition(condition["not"], "not")
        if _uses_group_identity(child):
            raise ValidationFailed("group identity conditions cannot be negated")
        validate_action_condition(child)
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


def referenced_condition_value_kinds(condition: Mapping[str, object]) -> frozenset[str]:
    """Return every value-source kind used by a condition tree."""
    if "all" in condition:
        return _children_value_kinds(condition["all"], "all")
    if "any" in condition:
        return _children_value_kinds(condition["any"], "any")
    if "not" in condition:
        return referenced_condition_value_kinds(_condition(condition["not"], "not"))
    values = (condition.get("left"), condition.get("right"))
    return frozenset(kind for raw in values if (kind := _condition_value_kind(raw)) is not None)


def referenced_condition_object_properties(condition: Mapping[str, object]) -> frozenset[str]:
    """Return target-object property names read by a condition tree."""
    if "all" in condition:
        return _children_object_properties(condition["all"], "all")
    if "any" in condition:
        return _children_object_properties(condition["any"], "any")
    if "not" in condition:
        return referenced_condition_object_properties(_condition(condition["not"], "not"))
    values = (condition.get("left"), condition.get("right"))
    return frozenset(name for raw in values if (name := _object_property_ref(raw)) is not None)


def referenced_linked_object_properties(
    condition: Mapping[str, object],
) -> frozenset[LinkedObjectPropertyReference]:
    """Return every unique linked-object property read by a condition tree."""
    if "all" in condition:
        return _children_linked_refs(condition["all"], "all")
    if "any" in condition:
        return _children_linked_refs(condition["any"], "any")
    if "not" in condition:
        return referenced_linked_object_properties(_condition(condition["not"], "not"))
    refs = {_linked_object_ref(condition.get("left")), _linked_object_ref(condition.get("right"))}
    return frozenset(ref for ref in refs if ref is not None)


def linked_object_property_key(link_type: str, direction: str, property_name: str, aggregation: str = "values") -> str:
    """Build the stable in-memory coordinate for one resolved linked value set."""
    return f"{direction}:{link_type}:{property_name}:{aggregation}"


def _evaluate_comparison(condition: Mapping[str, object], context: ActionConditionContext) -> bool:
    operator = _operator(condition)
    left = _condition_value(condition.get("left"), context)
    if operator == "exists":
        if _condition_value_kind(condition.get("left")) == "linkedObjectProperty":
            return bool(left)
        return left is not None
    right = _condition_value(condition.get("right"), context)
    return _compare(operator, left, right)


def _compare(operator: str, left: object, right: object) -> bool:
    comparison = _COMPARISONS.get(operator)
    if comparison is None:
        raise ValidationFailed("unsupported action condition operator", details={"operator": operator})
    return comparison(left, right)


def _contains_any(container: object, members: object) -> bool:
    """Palantir's `includes any`: the left collection shares at least one member with the right."""
    if not isinstance(members, Sequence) or isinstance(members, str | bytes):
        return _contains(container, members)
    return any(_contains(container, member) for member in cast(Sequence[object], members))


def _matches(left: object, pattern: object) -> bool:
    """Palantir's `matches`: a full-string regex test.

    Anchored with ``fullmatch`` rather than ``search`` so an author writing ``ACTIVE`` does not
    silently accept ``INACTIVE`` — a partial match is the kind of near-miss that reads as
    correct in review and passes the wrong rows in production.
    """
    if not isinstance(left, str) or not isinstance(pattern, str):
        return False
    try:
        return re.fullmatch(pattern, left) is not None
    except re.error as exc:
        raise ValidationFailed(
            "action condition regex is invalid", details={"operator": "matches", "pattern": pattern}
        ) from exc


def _each_is(operator: str, left: object, right: object) -> bool:
    """Palantir's `each is` / `each is not`: every member of the left collection is compared.

    An empty collection satisfies both, matching the vacuous-truth reading of "every member".
    A non-collection left side fails rather than being treated as a single-element collection,
    because silently widening the type would hide an authoring mistake.
    """
    if not isinstance(left, Sequence) or isinstance(left, str | bytes):
        return False
    members = cast(Sequence[object], left)
    if operator == "eachIs":
        return all(member == right for member in members)
    return all(member != right for member in members)


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


# A table rather than an if/elif ladder. Palantir's submission-criteria operator set grows with
# their releases, and a chain costs one branch per operator until the function trips the
# complexity gate -- which is exactly how it broke when `matches`, `eachIs`, `eachIsNot`, and
# `containsAny` landed. A row costs nothing. Keep in step with COMPARISON_OPERATORS; a test
# pins the two together so an operator can never be accepted upfront and then be undispatchable.
_COMPARISONS: Mapping[str, Callable[[object, object], bool]] = {
    "eq": lambda left, right: left == right,
    "neq": lambda left, right: left != right,
    "in": lambda left, right: _contains(right, left),
    "notIn": lambda left, right: not _contains(right, left),
    "lt": partial(_ordered_compare, "lt"),
    "lte": partial(_ordered_compare, "lte"),
    "gt": partial(_ordered_compare, "gt"),
    "gte": partial(_ordered_compare, "gte"),
    "contains": _contains,
    "containsAny": _contains_any,
    "startsWith": lambda left, right: isinstance(left, str) and isinstance(right, str) and left.startswith(right),
    "matches": _matches,
    "eachIs": partial(_each_is, "eachIs"),
    "eachIsNot": partial(_each_is, "eachIsNot"),
}


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
    if kind == "linkedObjectProperty":
        return context.linked_object_property(
            _required_text(value, "linkType"),
            _linked_direction(value),
            _required_text(value, "property"),
            _linked_aggregation(value),
        )
    raise ValidationFailed("unsupported action condition value", details={"kind": kind})


def _validate_comparison(condition: Mapping[str, object]) -> None:
    operator = _operator(condition)
    _validate_condition_value(condition.get("left"))
    if operator != "exists":
        _validate_condition_value(condition.get("right"))
    if operator in {"neq", "notIn"} and _comparison_uses_group_identity(condition):
        raise ValidationFailed("group identity conditions require a positive operator")


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
        _validate_current_user_attribute(value)
        return
    if kind == "linkedObjectProperty":
        _required_text(value, "linkType")
        _linked_direction(value)
        _required_text(value, "property")
        _linked_aggregation(value)
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


def _children_value_kinds(raw: object, field: str) -> frozenset[str]:
    kinds: set[str] = set()
    for child in _condition_list(raw, field):
        kinds.update(referenced_condition_value_kinds(child))
    return frozenset(kinds)


def _children_object_properties(raw: object, field: str) -> frozenset[str]:
    names: set[str] = set()
    for child in _condition_list(raw, field):
        names.update(referenced_condition_object_properties(child))
    return frozenset(names)


def _children_linked_refs(raw: object, field: str) -> frozenset[LinkedObjectPropertyReference]:
    refs: set[LinkedObjectPropertyReference] = set()
    for child in _condition_list(raw, field):
        refs.update(referenced_linked_object_properties(child))
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


def _object_property_ref(raw: object) -> str | None:
    if not isinstance(raw, Mapping):
        return None
    payload = cast(Mapping[str, object], raw)
    if payload.get("kind") != "objectProperty":
        return None
    value = payload.get("property")
    return value if isinstance(value, str) and value else None


def _condition_value_kind(raw: object) -> str | None:
    if not isinstance(raw, Mapping):
        return None
    kind = cast(Mapping[str, object], raw).get("kind")
    return kind if isinstance(kind, str) and kind else None


def _uses_group_identity(condition: Mapping[str, object]) -> bool:
    if "all" in condition:
        return any(_uses_group_identity(child) for child in _condition_list(condition["all"], "all"))
    if "any" in condition:
        return any(_uses_group_identity(child) for child in _condition_list(condition["any"], "any"))
    if "not" in condition:
        return _uses_group_identity(_condition(condition["not"], "not"))
    return _comparison_uses_group_identity(condition)


def _comparison_uses_group_identity(condition: Mapping[str, object]) -> bool:
    return any(_is_group_identity_value(condition.get(side)) for side in ("left", "right"))


def _is_group_identity_value(raw: object) -> bool:
    if not isinstance(raw, Mapping):
        return False
    value = cast(Mapping[str, object], raw)
    return value.get("kind") == "currentUser" and value.get("attribute", "id") in {
        "group",
        "groups",
        "roles",
    }


def _linked_object_ref(raw: object) -> LinkedObjectPropertyReference | None:
    if not isinstance(raw, Mapping):
        return None
    value = cast(Mapping[str, object], raw)
    if value.get("kind") != "linkedObjectProperty":
        return None
    return LinkedObjectPropertyReference(
        _required_text(value, "linkType"),
        _linked_direction(value),
        _required_text(value, "property"),
        _linked_aggregation(value),
    )


def _linked_direction(value: Mapping[str, object]) -> str:
    direction = value.get("direction", "outgoing")
    if direction not in {"outgoing", "incoming"}:
        raise ValidationFailed(
            "linked-object condition direction must be outgoing or incoming",
            details={"direction": direction},
        )
    return str(direction)


def _linked_aggregation(value: Mapping[str, object]) -> str:
    aggregation = value.get("aggregation", "values")
    if aggregation not in {"values", "count"}:
        raise ValidationFailed(
            "linked-object condition aggregation must be values or count",
            details={"aggregation": aggregation},
        )
    return str(aggregation)


def _validate_current_user_attribute(value: Mapping[str, object]) -> None:
    attribute = value.get("attribute", "id")
    if not isinstance(attribute, str) or not attribute.strip():
        raise ValidationFailed("unsupported current-user attribute", details={"attribute": attribute})


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
