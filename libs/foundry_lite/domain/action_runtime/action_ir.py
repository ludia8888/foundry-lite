"""Action IR v2 — a typed, ordered set of Ontology edit rules.

Palantir models an action type as a set of rules (create/modify/delete objects,
create/delete many-to-many links, or a single function edit) with a stable
identity and evaluation order. This module is the pure-domain representation:
immutable rule dataclasses plus the compile-time structural validation Palantir
performs (function-rule exclusivity, unique rule ids, and the ordering
constraints — an object cannot be modified/deleted/linked before it is created,
and cannot be created twice).

Foreign-key (one-to-many) links are NOT modeled as link rules; per Palantir they
are established by modifying the object's foreign-key property (a ModifyObjectRule
assignment). Only many-to-many links use CreateLinkRule / DeleteLinkRule.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from foundry_lite.domain.action_runtime.value_expression import (
    ObjectPropertyValue,
    PriorRuleOutputValue,
    ValueExpression,
)
from foundry_lite.domain.errors import ValidationFailed

RULE_CARDINALITIES = frozenset({"one", "many"})


@dataclass(frozen=True)
class PropertyAssignment:
    """Assign one property from a value expression."""

    property: str
    value: ValueExpression


@dataclass(frozen=True)
class CreateObjectRule:
    """Create a new object; the primary key may be generated or user-supplied."""

    rule_id: str
    object_type: str
    primary_key: ValueExpression | None
    assignments: tuple[PropertyAssignment, ...]
    on_interface: str | None = None


@dataclass(frozen=True)
class ModifyObjectRule:
    """Modify existing object(s); should_create_if_absent models create-or-modify."""

    rule_id: str
    object_type: str
    target: ValueExpression
    assignments: tuple[PropertyAssignment, ...]
    cardinality: str = "one"
    should_create_if_absent: bool = False
    on_interface: str | None = None


@dataclass(frozen=True)
class DeleteObjectRule:
    """Delete existing object(s)."""

    rule_id: str
    object_type: str
    target: ValueExpression
    cardinality: str = "one"
    on_interface: str | None = None


@dataclass(frozen=True)
class CreateLinkRule:
    """Create a many-to-many link between a source and target object."""

    rule_id: str
    link_type: str
    source: ValueExpression
    target: ValueExpression
    on_interface: str | None = None
    interface_link_constraint: str | None = None


@dataclass(frozen=True)
class DeleteLinkRule:
    """Delete a many-to-many link between a source and target object."""

    rule_id: str
    link_type: str
    source: ValueExpression
    target: ValueExpression
    on_interface: str | None = None
    interface_link_constraint: str | None = None


@dataclass(frozen=True)
class ResolvedInterfaceLinkDeleteRule:
    """Delete every concrete link implementing one interface link constraint."""

    rule_id: str
    concrete_link_types: tuple[str, ...]
    source: ValueExpression
    target: ValueExpression


@dataclass(frozen=True)
class FunctionEditRule:
    """Delegate all edits to a pinned ontology edit function.

    A function rule is mutually exclusive with every other rule: the function's
    returned edit batch is the complete set of edits.
    """

    rule_id: str
    function_api_name: str
    function_version: str


ActionRule = (
    CreateObjectRule
    | ModifyObjectRule
    | DeleteObjectRule
    | CreateLinkRule
    | DeleteLinkRule
    | ResolvedInterfaceLinkDeleteRule
    | FunctionEditRule
)


@dataclass(frozen=True)
class ActionDefinitionV2:
    """An ordered set of edit rules for one action type."""

    api_name: str
    rules: tuple[ActionRule, ...]


def iter_value_expressions(rule: ActionRule) -> Iterator[ValueExpression]:
    """Yield every value expression a rule references."""
    match rule:
        case CreateObjectRule(primary_key=primary_key, assignments=assignments):
            if primary_key is not None:
                yield primary_key
            yield from (assignment.value for assignment in assignments)
        case ModifyObjectRule(target=target, assignments=assignments):
            yield target
            yield from (assignment.value for assignment in assignments)
        case DeleteObjectRule(target=target):
            yield target
        case (
            CreateLinkRule(source=source, target=target)
            | DeleteLinkRule(source=source, target=target)
            | ResolvedInterfaceLinkDeleteRule(source=source, target=target)
        ):
            yield source
            yield target
        case FunctionEditRule():
            return


def referenced_prior_rule_ids(rule: ActionRule) -> frozenset[str]:
    """Rule ids this rule depends on via prior-rule-output value expressions."""
    return frozenset(
        expression.rule_id
        for expression in iter_value_expressions(rule)
        if isinstance(expression, PriorRuleOutputValue)
    )


def rule_id_of(rule: ActionRule) -> str:
    return rule.rule_id


def _literal_primary_keys(rules: tuple[ActionRule, ...]) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    for rule in rules:
        if isinstance(rule, CreateObjectRule) and isinstance(rule.primary_key, ObjectPropertyValue) is False:
            key = getattr(rule.primary_key, "value", None)
            if isinstance(key, str) and key:
                keys.append((rule.object_type, key))
    return keys


def _validate_unique_rule_ids(rules: tuple[ActionRule, ...]) -> None:
    seen: set[str] = set()
    for rule in rules:
        if rule.rule_id in seen:
            raise ValidationFailed("duplicate action rule id", details={"ruleId": rule.rule_id})
        seen.add(rule.rule_id)


def _validate_function_exclusivity(rules: tuple[ActionRule, ...]) -> None:
    has_function = any(isinstance(rule, FunctionEditRule) for rule in rules)
    if has_function and len(rules) != 1:
        raise ValidationFailed("a function rule must be the only rule in the action", details={"ruleCount": len(rules)})


def _validate_double_create(rules: tuple[ActionRule, ...]) -> None:
    seen: set[tuple[str, str]] = set()
    for object_type, key in _literal_primary_keys(rules):
        if (object_type, key) in seen:
            raise ValidationFailed(
                "object created twice in one action", details={"objectType": object_type, "primaryKey": key}
            )
        seen.add((object_type, key))


def _validate_ordering(rules: tuple[ActionRule, ...]) -> None:
    created: set[str] = set()
    deleted: set[str] = set()
    for rule in rules:
        for referenced in referenced_prior_rule_ids(rule):
            if referenced not in created:
                raise ValidationFailed(
                    "rule references an object before it is created",
                    details={"ruleId": rule.rule_id, "references": referenced},
                )
            if referenced in deleted:
                raise ValidationFailed(
                    "rule references an object after it is deleted",
                    details={"ruleId": rule.rule_id, "references": referenced},
                )
        if isinstance(rule, CreateObjectRule):
            created.add(rule.rule_id)
        elif isinstance(rule, DeleteObjectRule):
            deleted.update(referenced_prior_rule_ids(rule))


def validate_action_definition(definition: ActionDefinitionV2) -> None:
    """Run every compile-time structural check Palantir enforces on the rule set."""
    rules = definition.rules
    if not rules:
        raise ValidationFailed("action definition has no rules", details={"apiName": definition.api_name})
    _validate_unique_rule_ids(rules)
    _validate_function_exclusivity(rules)
    _validate_double_create(rules)
    _validate_ordering(rules)
