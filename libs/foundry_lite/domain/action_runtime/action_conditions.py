"""Typed, eval-free condition AST for Action Contract v3.

Conditions are persisted as JSON objects and interpreted through this closed
evaluator.  No expression strings, interpolation, or ``eval`` are accepted.
The same AST is used by parameter overrides and submission criteria.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from functools import partial
from math import isfinite
from typing import Protocol, cast

from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.scalar_values import is_finite_decimal, matches_scalar_type, parse_aware_timestamp

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

_CONDITION_NODE_FIELDS = frozenset({"all", "any", "not", "op"})
_CONDITION_METADATA_FIELDS = frozenset({"message", "policyName"})
_MAX_REGEX_PATTERN_CHARS = 256
_MAX_REGEX_INPUT_CHARS = 4096
_MISSING = object()
_SCALAR_CONDITION_TYPES = frozenset({"string", "integer", "long", "float", "decimal", "boolean", "date", "timestamp"})


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
    parameter_types: Mapping[str, str] = field(default_factory=dict[str, str])
    object_property_types: Mapping[str, str] = field(default_factory=dict[str, str])
    linked_object_property_types: Mapping[str, str] = field(default_factory=dict[str, str])

    def parameter(self, name: str) -> object:
        return self.parameters.get(name, _MISSING)

    def object_property(self, name: str) -> object:
        return self.object_properties.get(name, _MISSING)

    def current_user(self, attribute: str | None) -> object:
        if attribute is None or attribute == "id":
            return self.actor_user_id
        if attribute in {"group", "groups", "roles"}:
            return self.actor_groups
        return self.actor_attributes.get(attribute, _MISSING)

    def linked_object_property(self, link_type: str, direction: str, property_name: str, aggregation: str) -> object:
        key = linked_object_property_key(link_type, direction, property_name, aggregation)
        if key not in self.linked_object_properties:
            raise ValidationFailed(
                "linked-object condition value was not resolved",
                details={"linkType": link_type, "direction": direction, "property": property_name},
            )
        return self.linked_object_properties[key]

    def condition_value_type(self, kind: str, name: str) -> str | None:
        if kind == "parameter":
            return self.parameter_types.get(name)
        if kind == "objectProperty":
            return self.object_property_types.get(name)
        if kind == "linkedObjectProperty":
            return self.linked_object_property_types.get(name)
        return None


@dataclass(frozen=True, slots=True)
class _ConditionOperand:
    value: object
    data_type: str | None = None


def evaluate_action_condition(condition: Mapping[str, object], context: ActionConditionContext) -> bool:
    """Evaluate one closed condition node, failing on unknown shapes."""
    validate_action_condition(condition)
    return _evaluate_validated_condition(condition, context) is True


def validate_action_condition(condition: Mapping[str, object]) -> None:
    """Validate every node without needing runtime values."""
    kind = _condition_node_kind(condition)
    _validate_condition_fields(condition, kind)
    if kind == "all":
        _validate_children(condition["all"], "all")
        return
    if kind == "any":
        _validate_children(condition["any"], "any")
        return
    if kind == "not":
        child = _condition(condition["not"], "not")
        if _uses_group_identity(child):
            raise ValidationFailed("group identity conditions cannot be negated")
        validate_action_condition(child)
        return
    _validate_comparison(condition)


def validate_action_condition_parameter_literals(
    condition: Mapping[str, object], parameter_types: Mapping[str, str]
) -> None:
    """Reject literals that cannot inhabit the referenced parameter's scalar type."""

    kind = _condition_node_kind(condition)
    if kind in {"all", "any"}:
        for child in _condition_list(condition[kind], kind):
            validate_action_condition_parameter_literals(child, parameter_types)
        return
    if kind == "not":
        validate_action_condition_parameter_literals(_condition(condition["not"], "not"), parameter_types)
        return
    _validate_comparison_parameter_literals(condition, parameter_types)


def referenced_condition_parameters(condition: Mapping[str, object]) -> frozenset[str]:
    """Return parameter names read anywhere in a condition tree."""
    kind = _condition_node_kind(condition)
    if kind == "all":
        return _children_parameter_refs(condition["all"], "all")
    if kind == "any":
        return _children_parameter_refs(condition["any"], "any")
    if kind == "not":
        return referenced_condition_parameters(_condition(condition["not"], "not"))
    refs = {_parameter_ref(condition.get("left")), _parameter_ref(condition.get("right"))}
    return frozenset(ref for ref in refs if ref is not None)


def referenced_condition_value_kinds(condition: Mapping[str, object]) -> frozenset[str]:
    """Return every value-source kind used by a condition tree."""
    kind = _condition_node_kind(condition)
    if kind == "all":
        return _children_value_kinds(condition["all"], "all")
    if kind == "any":
        return _children_value_kinds(condition["any"], "any")
    if kind == "not":
        return referenced_condition_value_kinds(_condition(condition["not"], "not"))
    values = (condition.get("left"), condition.get("right"))
    return frozenset(value_kind for raw in values if (value_kind := _condition_value_kind(raw)) is not None)


def referenced_condition_object_properties(condition: Mapping[str, object]) -> frozenset[str]:
    """Return target-object property names read by a condition tree."""
    kind = _condition_node_kind(condition)
    if kind == "all":
        return _children_object_properties(condition["all"], "all")
    if kind == "any":
        return _children_object_properties(condition["any"], "any")
    if kind == "not":
        return referenced_condition_object_properties(_condition(condition["not"], "not"))
    values = (condition.get("left"), condition.get("right"))
    return frozenset(name for raw in values if (name := _object_property_ref(raw)) is not None)


def referenced_linked_object_properties(
    condition: Mapping[str, object],
) -> frozenset[LinkedObjectPropertyReference]:
    """Return every unique linked-object property read by a condition tree."""
    kind = _condition_node_kind(condition)
    if kind == "all":
        return _children_linked_refs(condition["all"], "all")
    if kind == "any":
        return _children_linked_refs(condition["any"], "any")
    if kind == "not":
        return referenced_linked_object_properties(_condition(condition["not"], "not"))
    refs = {_linked_object_ref(condition.get("left")), _linked_object_ref(condition.get("right"))}
    return frozenset(ref for ref in refs if ref is not None)


def linked_object_property_key(link_type: str, direction: str, property_name: str, aggregation: str = "values") -> str:
    """Build the stable in-memory coordinate for one resolved linked value set."""
    return f"{direction}:{link_type}:{property_name}:{aggregation}"


def action_condition_values_equal(left: object, right: object) -> bool:
    """Compare JSON-like Action values without Python's bool/number coercion."""
    if left is _MISSING or right is _MISSING:
        return False
    return _present_condition_values_equal(left, right)


def _present_condition_values_equal(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return _boolean_values_equal(left, right)
    if _is_numeric_type(left) or _is_numeric_type(right):
        return _numeric_values_equal(left, right)
    mapping_result = _mapping_equality_result(left, right)
    if mapping_result is not None:
        return mapping_result
    return (
        _collection_values_equal(left, right)
        if _is_collection(left) or _is_collection(right)
        else type(left) is type(right) and left == right
    )


def _boolean_values_equal(left: object, right: object) -> bool:
    return isinstance(left, bool) and isinstance(right, bool) and left is right


def _numeric_values_equal(left: object, right: object) -> bool:
    return _is_number(left) and _is_number(right) and _decimal_number(left) == _decimal_number(right)


def _mapping_equality_result(left: object, right: object) -> bool | None:
    left_mapping = _object_mapping(left)
    right_mapping = _object_mapping(right)
    if left_mapping is None and right_mapping is None:
        return None
    return _mapping_values_equal(left_mapping, right_mapping)


def _evaluate_validated_condition(condition: Mapping[str, object], context: ActionConditionContext) -> bool | None:
    kind = _condition_node_kind(condition)
    if kind in {"all", "any"}:
        children = _condition_list(condition[kind], kind)
        results = tuple(_evaluate_validated_condition(child, context) for child in children)
        return _logical_result(kind, results)
    if kind == "not":
        result = _evaluate_validated_condition(_condition(condition["not"], "not"), context)
        return None if result is None else not result
    return _evaluate_comparison(condition, context)


def _evaluate_comparison(condition: Mapping[str, object], context: ActionConditionContext) -> bool | None:
    operator = _operator(condition)
    left = _condition_value(condition.get("left"), context)
    if operator == "exists":
        return _has_value(left.value)
    right = _condition_value(condition.get("right"), context)
    if left.value is _MISSING or right.value is _MISSING:
        return None
    return _compare_operands(operator, left, right)


def _compare_operands(operator: str, left: _ConditionOperand, right: _ConditionOperand) -> bool:
    if left.data_type and right.data_type and left.data_type != right.data_type:
        return False
    data_type = left.data_type or right.data_type
    if data_type == "decimal":
        return _compare_decimal_values(operator, left.value, right.value)
    return _compare(operator, left.value, right.value)


def _compare_decimal_values(operator: str, left: object, right: object) -> bool:
    collection_result = _compare_decimal_collection_operation(operator, left, right)
    if collection_result is not None:
        return collection_result
    return _compare_decimal_scalars(operator, left, right)


def _compare_decimal_collection_operation(operator: str, left: object, right: object) -> bool | None:
    if operator in {"in", "notIn"}:
        is_contained = _decimal_collection_contains(right, left)
        return is_contained if operator == "in" else _is_collection(right) and not is_contained
    if operator == "contains":
        return _decimal_collection_contains(left, right)
    if operator in {"eachIs", "eachIsNot"}:
        return _each_decimal_is(operator, left, right)
    return None


def _compare_decimal_scalars(operator: str, left: object, right: object) -> bool:
    left_number = _condition_decimal(left)
    right_number = _condition_decimal(right)
    if left_number is None or right_number is None:
        return False
    if operator == "eq":
        return left_number == right_number
    if operator == "neq":
        return left_number != right_number
    if operator in {"lt", "lte", "gt", "gte"}:
        return _apply_decimal_order(operator, left_number, right_number)
    return False


def _decimal_collection_contains(container: object, member: object) -> bool:
    if not _is_collection(container):
        return False
    member_number = _condition_decimal(member)
    if member_number is None:
        return False
    return any(_condition_decimal(item) == member_number for item in cast(Sequence[object], container))


def _each_decimal_is(operator: str, left: object, right: object) -> bool:
    if not _is_collection(left):
        return False
    right_number = _condition_decimal(right)
    if right_number is None:
        return False
    comparisons = (_condition_decimal(item) == right_number for item in cast(Sequence[object], left))
    return all(comparisons) if operator == "eachIs" else all(not item for item in comparisons)


def _condition_decimal(value: object) -> Decimal | None:
    return Decimal(cast(str, value)) if is_finite_decimal(value) else None


def _compare(operator: str, left: object, right: object) -> bool:
    comparison = _COMPARISONS.get(operator)
    if comparison is None:
        raise ValidationFailed("unsupported action condition operator", details={"operator": operator})
    return comparison(left, right)


def _contains_any(container: object, members: object) -> bool:
    """Palantir's `includes any`: the left collection shares at least one member with the right."""
    if not _is_collection(container) or not _is_collection(members):
        return False
    return any(_contains(container, member) for member in cast(Sequence[object], members))


def _matches(left: object, pattern: object) -> bool:
    """Palantir's `matches`: a full-string regex test.

    Anchored with ``fullmatch`` rather than ``search`` so an author writing ``ACTIVE`` does not
    silently accept ``INACTIVE`` — a partial match is the kind of near-miss that reads as
    correct in review and passes the wrong rows in production.
    """
    if not isinstance(left, str) or not isinstance(pattern, str) or len(left) > _MAX_REGEX_INPUT_CHARS:
        return False
    _validate_safe_regex_pattern(pattern)
    return re.fullmatch(pattern, left) is not None


def _each_is(operator: str, left: object, right: object) -> bool:
    """Palantir's `each is` / `each is not`: every member of the left collection is compared.

    An empty collection satisfies both, matching the vacuous-truth reading of "every member".
    A non-collection left side fails rather than being treated as a single-element collection,
    because silently widening the type would hide an authoring mistake.
    """
    if not _is_collection(left):
        return False
    members = cast(Sequence[object], left)
    if operator == "eachIs":
        return all(action_condition_values_equal(member, right) for member in members)
    return all(not action_condition_values_equal(member, right) for member in members)


def _ordered_compare(operator: str, left: object, right: object) -> bool:
    if _is_number(left) and _is_number(right):
        return _apply_decimal_order(operator, _decimal_number(left), _decimal_number(right))
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    timestamp_result = _timestamp_order_result(operator, left, right)
    return timestamp_result if timestamp_result is not None else _apply_text_order(operator, left, right)


def _timestamp_order_result(operator: str, left: str, right: str) -> bool | None:
    left_timestamp = parse_aware_timestamp(left)
    right_timestamp = parse_aware_timestamp(right)
    has_timestamp_shape = _looks_like_timestamp(left) or _looks_like_timestamp(right)
    if left_timestamp is None and right_timestamp is None and not has_timestamp_shape:
        return None
    if left_timestamp is None or right_timestamp is None:
        return False
    return _apply_datetime_order(operator, left_timestamp, right_timestamp)


def _looks_like_timestamp(value: str) -> bool:
    return re.match(r"^\d{4}-\d{2}-\d{2}[Tt]", value) is not None


def _apply_datetime_order(operator: str, left: datetime, right: datetime) -> bool:
    if operator == "lt":
        return left < right
    if operator == "lte":
        return left <= right
    if operator == "gt":
        return left > right
    return left >= right


def _apply_decimal_order(operator: str, left: Decimal, right: Decimal) -> bool:
    if operator == "lt":
        return left < right
    if operator == "lte":
        return left <= right
    if operator == "gt":
        return left > right
    return left >= right


def _apply_text_order(operator: str, left: str, right: str) -> bool:
    if operator == "lt":
        return left < right
    if operator == "lte":
        return left <= right
    if operator == "gt":
        return left > right
    return left >= right


def _contains(container: object, member: object) -> bool:
    if isinstance(container, str):
        return isinstance(member, str) and member in container
    if _is_collection(container):
        return any(action_condition_values_equal(item, member) for item in cast(Sequence[object], container))
    return False


def _is_in_collection(member: object, container: object) -> bool:
    return _is_collection(container) and _contains(container, member)


def _is_not_in_collection(member: object, container: object) -> bool:
    return _is_collection(container) and not _contains(container, member)


# A table rather than an if/elif ladder. Palantir's submission-criteria operator set grows with
# their releases, and a chain costs one branch per operator until the function trips the
# complexity gate -- which is exactly how it broke when `matches`, `eachIs`, `eachIsNot`, and
# `containsAny` landed. A row costs nothing. Keep in step with COMPARISON_OPERATORS; a test
# pins the two together so an operator can never be accepted upfront and then be undispatchable.
_COMPARISONS: Mapping[str, Callable[[object, object], bool]] = {
    "eq": action_condition_values_equal,
    "neq": lambda left, right: not action_condition_values_equal(left, right),
    "in": _is_in_collection,
    "notIn": _is_not_in_collection,
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


def _condition_value(raw: object, context: ActionConditionContext) -> _ConditionOperand:
    value = _mapping(raw, "condition value")
    kind = value.get("kind")
    if kind == "literal":
        return _ConditionOperand(value.get("value"))
    if kind == "parameter":
        name = _required_text(value, "parameter")
        return _ConditionOperand(context.parameter(name), _context_value_type(context, "parameter", name))
    if kind == "objectProperty":
        name = _required_text(value, "property")
        return _ConditionOperand(context.object_property(name), _context_value_type(context, "objectProperty", name))
    if kind == "currentUser":
        attribute = value.get("attribute")
        return _ConditionOperand(context.current_user(attribute if isinstance(attribute, str) else None))
    if kind == "linkedObjectProperty":
        link_type = _required_text(value, "linkType")
        direction = _linked_direction(value)
        property_name = _required_text(value, "property")
        aggregation = _linked_aggregation(value)
        key = linked_object_property_key(link_type, direction, property_name, aggregation)
        return _ConditionOperand(
            context.linked_object_property(link_type, direction, property_name, aggregation),
            _context_value_type(context, "linkedObjectProperty", key),
        )
    raise ValidationFailed("unsupported action condition value", details={"kind": kind})


def _context_value_type(context: ActionConditionContext, kind: str, name: str) -> str | None:
    resolver = getattr(context, "condition_value_type", None)
    if not callable(resolver):
        return None
    value = resolver(kind, name)
    return value if isinstance(value, str) and value else None


def _validate_comparison_parameter_literals(
    condition: Mapping[str, object], parameter_types: Mapping[str, str]
) -> None:
    operator = _operator(condition)
    if operator == "exists":
        return
    left = _mapping(condition.get("left"), "condition value")
    right = _mapping(condition.get("right"), "condition value")
    _validate_parameter_literal_pair(left, right, operator, parameter_types, is_parameter_left=True)
    _validate_parameter_literal_pair(right, left, operator, parameter_types, is_parameter_left=False)


def _validate_parameter_literal_pair(
    parameter: Mapping[str, object],
    literal: Mapping[str, object],
    operator: str,
    parameter_types: Mapping[str, str],
    *,
    is_parameter_left: bool,
) -> None:
    if parameter.get("kind") != "parameter" or literal.get("kind") != "literal":
        return
    name = _required_text(parameter, "parameter")
    data_type = parameter_types.get(name)
    if data_type not in _SCALAR_CONDITION_TYPES:
        return
    value = literal.get("value")
    should_expect_collection = _expects_parameter_literal_collection(
        operator, value, is_parameter_left=is_parameter_left
    )
    values = _parameter_literal_values(name, value, should_expect_collection=should_expect_collection)
    if not all(matches_scalar_type(data_type, item) for item in values):
        raise ValidationFailed(
            "action condition literal has the wrong parameter type",
            details={"parameter": name, "type": data_type},
        )


def _expects_parameter_literal_collection(operator: str, value: object, *, is_parameter_left: bool) -> bool:
    if is_parameter_left:
        return operator in {"in", "notIn"}
    return operator in {"eachIs", "eachIsNot"} or (operator == "contains" and _is_collection(value))


def _parameter_literal_values(name: str, value: object, *, should_expect_collection: bool) -> Sequence[object]:
    if not should_expect_collection:
        return (value,)
    if not _is_collection(value):
        raise ValidationFailed("action condition literal must be a collection", details={"parameter": name})
    return cast(Sequence[object], value)


def _validate_comparison(condition: Mapping[str, object]) -> None:
    operator = _operator(condition)
    _validate_condition_value(condition.get("left"))
    if operator == "exists":
        if "right" in condition:
            raise ValidationFailed("exists condition does not accept a right value")
    else:
        _validate_condition_value(condition.get("right"))
    if operator == "matches":
        _validate_literal_regex(condition.get("right"))
    if operator in {"neq", "notIn"} and _comparison_uses_group_identity(condition):
        raise ValidationFailed("group identity conditions require a positive operator")


def _validate_condition_value(raw: object) -> None:
    value = _mapping(raw, "condition value")
    kind = value.get("kind")
    if kind == "literal":
        _validate_value_fields(value, required={"kind", "value"})
        return
    if kind == "parameter":
        _validate_value_fields(value, required={"kind", "parameter"})
        _required_text(value, "parameter")
        return
    if kind == "objectProperty":
        _validate_value_fields(value, required={"kind", "property"})
        _required_text(value, "property")
        return
    if kind == "currentUser":
        _validate_value_fields(value, required={"kind"}, optional={"attribute"})
        _validate_current_user_attribute(value)
        return
    if kind == "linkedObjectProperty":
        _validate_value_fields(
            value,
            required={"kind", "linkType", "property"},
            optional={"direction", "aggregation"},
        )
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
    kind = _condition_node_kind(condition)
    if kind == "all":
        return any(_uses_group_identity(child) for child in _condition_list(condition["all"], "all"))
    if kind == "any":
        return any(_uses_group_identity(child) for child in _condition_list(condition["any"], "any"))
    if kind == "not":
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


def _condition_node_kind(condition: Mapping[str, object]) -> str:
    kinds = tuple(field for field in _CONDITION_NODE_FIELDS if field in condition)
    if len(kinds) != 1:
        raise ValidationFailed(
            "action condition must contain exactly one node kind",
            details={"nodeFields": sorted(kinds)},
        )
    return kinds[0]


def _validate_condition_fields(condition: Mapping[str, object], kind: str) -> None:
    allowed = set(_CONDITION_METADATA_FIELDS) | {kind}
    if kind == "op":
        allowed.update({"left", "right"})
    unexpected = sorted(str(field) for field in condition if field not in allowed)
    if unexpected:
        raise ValidationFailed("unsupported action condition fields", details={"fields": unexpected})
    for metadata_field in _CONDITION_METADATA_FIELDS:
        if metadata_field in condition and not _is_nonempty_text(condition[metadata_field]):
            raise ValidationFailed(
                "action condition metadata must be non-empty text", details={"field": metadata_field}
            )


def _validate_value_fields(
    value: Mapping[str, object],
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    allowed = required | (optional or set())
    missing = sorted(field for field in required if field not in value)
    unexpected = sorted(str(field) for field in value if field not in allowed)
    if missing or unexpected:
        raise ValidationFailed(
            "action condition value has invalid fields",
            details={"missingFields": missing, "unexpectedFields": unexpected},
        )


def _logical_result(kind: str, results: Sequence[bool | None]) -> bool | None:
    decisive = False if kind == "all" else True
    if decisive in results:
        return decisive
    if None in results:
        return None
    return not decisive


def _has_value(value: object) -> bool:
    if value is _MISSING or value is None:
        return False
    if isinstance(value, str | bytes | bytearray):
        return len(value) > 0
    if isinstance(value, Mapping):
        return len(cast(Mapping[object, object], value)) > 0
    if isinstance(value, Sequence):
        return len(cast(Sequence[object], value)) > 0
    return True


def _is_number(value: object) -> bool:
    if not _is_numeric_type(value):
        return False
    if isinstance(value, float):
        return isfinite(value)
    if isinstance(value, Decimal):
        return value.is_finite()
    return True


def _is_numeric_type(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int | float | Decimal)


def _decimal_number(value: object) -> Decimal:
    if not _is_number(value):
        raise ValidationFailed("action condition value is not a finite number")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _mapping_values_equal(
    left: Mapping[object, object] | None,
    right: Mapping[object, object] | None,
) -> bool:
    if left is None or right is None or len(left) != len(right):
        return False
    if not all(isinstance(key, str) for key in (*left.keys(), *right.keys())):
        return False
    if set(left) != set(right):
        return False
    return all(action_condition_values_equal(left[key], right[key]) for key in left)


def _object_mapping(value: object) -> Mapping[object, object] | None:
    if not isinstance(value, Mapping):
        return None
    return cast(Mapping[object, object], value)


def _is_collection(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray)


def _collection_values_equal(left: object, right: object) -> bool:
    if not _is_collection(left) or not _is_collection(right):
        return False
    left_values = cast(Sequence[object], left)
    right_values = cast(Sequence[object], right)
    return len(left_values) == len(right_values) and all(
        action_condition_values_equal(left_item, right_item)
        for left_item, right_item in zip(left_values, right_values, strict=True)
    )


def _validate_literal_regex(raw: object) -> None:
    value = _mapping(raw, "condition value")
    if value.get("kind") != "literal":
        return
    pattern = value.get("value")
    if not isinstance(pattern, str):
        raise ValidationFailed("action condition regex must be text", details={"operator": "matches"})
    _validate_safe_regex_pattern(pattern)


def _validate_safe_regex_pattern(pattern: str) -> None:
    if len(pattern) > _MAX_REGEX_PATTERN_CHARS:
        raise ValidationFailed(
            "action condition regex is too long",
            details={"operator": "matches", "maxChars": _MAX_REGEX_PATTERN_CHARS},
        )
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValidationFailed(
            "action condition regex is invalid", details={"operator": "matches", "pattern": pattern}
        ) from exc
    risk = _regex_backtracking_risk(pattern)
    if risk is not None:
        raise ValidationFailed(
            "action condition regex uses an unsafe backtracking construct",
            details={"operator": "matches", "reason": risk},
        )


def _regex_backtracking_risk(pattern: str) -> str | None:
    masked, has_backreference = _masked_regex_structure(pattern)
    if has_backreference:
        return "backreference"
    if re.search(r"\(\?(?!:)", masked) is not None:
        return "lookaround-or-inline-group"
    closed_groups = _closed_regex_groups(masked)
    if _has_unsafe_closed_regex_group(masked, closed_groups):
        return "repeated-variable-or-alternating-group"
    return None


def _closed_regex_groups(masked: str) -> dict[int, tuple[bool, bool]]:
    group_stack: list[tuple[int, bool, bool]] = []
    closed_groups: dict[int, tuple[bool, bool]] = {}
    for index, character in enumerate(masked):
        if character == "?" and index > 0 and masked[index - 1] == "(":
            continue
        _track_regex_group_character(character, index, group_stack, closed_groups)
    return closed_groups


def _has_unsafe_closed_regex_group(pattern: str, closed_groups: Mapping[int, tuple[bool, bool]]) -> bool:
    return any(
        _regex_quantifier_at(pattern, close_index + 1) and (has_repeat or has_alternation)
        for close_index, (has_repeat, has_alternation) in closed_groups.items()
    )


def _masked_regex_structure(pattern: str) -> tuple[str, bool]:
    masked = list(pattern)
    is_escaped = False
    is_class = False
    has_backreference = False
    for index, character in enumerate(pattern):
        if is_escaped:
            has_backreference = has_backreference or (character in "123456789")
            masked[index - 1] = masked[index] = "_"
            is_escaped = False
        elif character == "\\":
            is_escaped = True
        elif is_class:
            masked[index] = "_"
            is_class = character != "]"
        elif character == "[":
            masked[index] = "_"
            is_class = True
    return "".join(masked), has_backreference


def _track_regex_group_character(
    character: str,
    index: int,
    stack: list[tuple[int, bool, bool]],
    closed: dict[int, tuple[bool, bool]],
) -> None:
    if character == "(":
        stack.append((index, False, False))
        return
    if not stack:
        return
    if character == "|":
        start, has_repeat, _ = stack[-1]
        stack[-1] = (start, has_repeat, True)
        return
    if character in "*+?{":
        start, _, has_alternation = stack[-1]
        stack[-1] = (start, True, has_alternation)
        return
    if character != ")":
        return
    _close_regex_group(index, stack, closed)


def _close_regex_group(
    index: int,
    stack: list[tuple[int, bool, bool]],
    closed: dict[int, tuple[bool, bool]],
) -> None:
    _, has_repeat, has_alternation = stack.pop()
    closed[index] = (has_repeat, has_alternation)
    if stack:
        start, parent_repeat, parent_alternation = stack[-1]
        stack[-1] = (start, parent_repeat or has_repeat, parent_alternation or has_alternation)


def _regex_quantifier_at(pattern: str, index: int) -> bool:
    if index >= len(pattern):
        return False
    if pattern[index] in "*+?":
        return True
    return re.match(r"\{\d+(?:,\d*)?\}", pattern[index:]) is not None


def _is_nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
