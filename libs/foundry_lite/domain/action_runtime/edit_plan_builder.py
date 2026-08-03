"""Resolve validated Action IR v2 rules into an immutable EditPlan.

The builder walks the ordered rules and, through an injected resolution context
(which performs all I/O — parameter/object lookups, id generation, version
reads), produces the concrete object/link edits. A created object's id is
recorded so a later rule referencing it (a link to a newly created object) binds
to the right id; per Palantir a modify/delete rule targets existing objects only,
so those targets always resolve to an already-stored object with a read version.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.domain.action_runtime.action_ir import (
    ActionDefinitionV2,
    ActionRule,
    CreateLinkRule,
    CreateObjectRule,
    DeleteLinkRule,
    DeleteObjectRule,
    ModifyObjectRule,
    PropertyAssignment,
)
from foundry_lite.domain.action_runtime.edit_plan import (
    EditPlan,
    LinkCreate,
    LinkDelete,
    ObjectCreate,
    ObjectDelete,
    ObjectModify,
    ObjectRef,
)
from foundry_lite.domain.action_runtime.value_expression import (
    GeneratedIdValue,
    PriorRuleOutputValue,
    ValueExpression,
)
from foundry_lite.domain.errors import ValidationFailed


class PlanResolutionContext(Protocol):
    """Application-provided I/O for planning. Domain stays pure.

    ``resolve_existing_object`` takes the rule's declared ``object_type`` because a
    value expression alone (e.g. a parameter naming an object id) does not carry the
    type, and object ids are unique only per type; the adapter also uses the type to
    enforce row/segment visibility when resolving the object.
    """

    def evaluate(self, expression: ValueExpression) -> object: ...

    def resolve_existing_object(self, object_type: str, expression: ValueExpression) -> ObjectRef: ...

    def resolve_existing_object_set(self, object_type: str, expression: ValueExpression) -> tuple[ObjectRef, ...]: ...

    def resolve_link_endpoint(self, link_type: str, role: str, expression: ValueExpression) -> str: ...

    def generate_object_id(self, rule_id: str) -> str: ...

    def operation_key(self, rule_id: str, discriminator: str) -> str: ...


class _PlanAccumulator:
    """Mutable working set threaded through the ordered rule walk."""

    def __init__(self) -> None:
        self.creates: list[ObjectCreate] = []
        self.modifies: list[ObjectModify] = []
        self.deletes: list[ObjectDelete] = []
        self.link_creates: list[LinkCreate] = []
        self.link_deletes: list[LinkDelete] = []
        self.read_versions: dict[str, int] = {}
        self.created_ids: dict[str, str] = {}

    def record_read(self, ref: ObjectRef) -> ObjectRef:
        self.read_versions[ref.object_id] = ref.version
        return ref

    def to_plan(self) -> EditPlan:
        return EditPlan(
            objects_to_create=tuple(self.creates),
            objects_to_modify=tuple(self.modifies),
            objects_to_delete=tuple(self.deletes),
            links_to_create=tuple(self.link_creates),
            links_to_delete=tuple(self.link_deletes),
            read_set_versions=dict(self.read_versions),
        )


def build_edit_plan(definition: ActionDefinitionV2, context: PlanResolutionContext) -> EditPlan:
    """Resolve every rule into concrete edits in declared order."""
    accumulator = _PlanAccumulator()
    for rule in definition.rules:
        _apply_rule(rule, context, accumulator)
    return accumulator.to_plan()


def _apply_rule(rule: ActionRule, context: PlanResolutionContext, acc: _PlanAccumulator) -> None:
    if isinstance(rule, CreateObjectRule):
        _plan_create(rule, context, acc)
    elif isinstance(rule, ModifyObjectRule):
        _plan_modify(rule, context, acc)
    elif isinstance(rule, DeleteObjectRule):
        _plan_delete(rule, context, acc)
    elif isinstance(rule, CreateLinkRule | DeleteLinkRule):
        _plan_link(rule, context, acc)
    else:  # FunctionEditRule — the only remaining member of the closed rule union
        raise ValidationFailed(
            "function-backed actions are planned by the function edit adapter, not the rule planner",
            details={"ruleId": rule.rule_id},
        )


def _resolved_properties(
    assignments: tuple[PropertyAssignment, ...], context: PlanResolutionContext
) -> dict[str, object]:
    return {assignment.property: context.evaluate(assignment.value) for assignment in assignments}


def _allocate_object_id(rule: CreateObjectRule, context: PlanResolutionContext) -> str:
    """Mint a created object's identity through the single allocation channel.

    A generated-id or absent primary key delegates to ``generate_object_id`` so
    the id threads into ``created_ids`` for later link resolution; an explicit
    deterministic key (literal/parameter/…) is evaluated and used verbatim.
    """
    if rule.primary_key is None or isinstance(rule.primary_key, GeneratedIdValue):
        return context.generate_object_id(rule.rule_id)
    resolved = context.evaluate(rule.primary_key)
    if resolved is None or not str(resolved):
        # An explicitly-declared deterministic key must be used verbatim; a random
        # fallback would silently mint a mismatched natural key. Fail closed.
        raise ValidationFailed("createObject primary key resolved to an empty value", details={"ruleId": rule.rule_id})
    return str(resolved)


def _plan_create(rule: CreateObjectRule, context: PlanResolutionContext, acc: _PlanAccumulator) -> None:
    object_id = _allocate_object_id(rule, context)
    acc.created_ids[rule.rule_id] = object_id
    acc.creates.append(
        ObjectCreate(
            operation_key=context.operation_key(rule.rule_id, object_id),
            rule_id=rule.rule_id,
            object_type=rule.object_type,
            primary_key=object_id,
            properties=_resolved_properties(rule.assignments, context),
        )
    )


def _plan_modify(rule: ModifyObjectRule, context: PlanResolutionContext, acc: _PlanAccumulator) -> None:
    patch = _resolved_properties(rule.assignments, context)
    targets = _resolve_targets(rule.object_type, rule.cardinality, rule.target, context, acc)
    for ref in targets:
        acc.modifies.append(
            ObjectModify(
                operation_key=context.operation_key(rule.rule_id, ref.object_id),
                rule_id=rule.rule_id,
                object_type=rule.object_type,
                object_id=ref.object_id,
                expected_version=ref.version,
                patch=dict(patch),
                should_create_if_absent=rule.should_create_if_absent,
            )
        )


def _plan_delete(rule: DeleteObjectRule, context: PlanResolutionContext, acc: _PlanAccumulator) -> None:
    for ref in _resolve_targets(rule.object_type, rule.cardinality, rule.target, context, acc):
        acc.deletes.append(
            ObjectDelete(
                operation_key=context.operation_key(rule.rule_id, ref.object_id),
                rule_id=rule.rule_id,
                object_type=rule.object_type,
                object_id=ref.object_id,
                expected_version=ref.version,
            )
        )


def _resolve_targets(
    object_type: str, cardinality: str, target: ValueExpression, context: PlanResolutionContext, acc: _PlanAccumulator
) -> tuple[ObjectRef, ...]:
    if cardinality == "many":
        refs = context.resolve_existing_object_set(object_type, target)
    else:
        refs = (context.resolve_existing_object(object_type, target),)
    return tuple(acc.record_read(ref) for ref in refs)


def _plan_link(rule: CreateLinkRule | DeleteLinkRule, context: PlanResolutionContext, acc: _PlanAccumulator) -> None:
    source = _resolve_endpoint(rule.link_type, "source", rule.source, context, acc)
    target = _resolve_endpoint(rule.link_type, "target", rule.target, context, acc)
    key = context.operation_key(rule.rule_id, f"{source}->{target}")
    if isinstance(rule, CreateLinkRule):
        acc.link_creates.append(LinkCreate(key, rule.rule_id, rule.link_type, source, target))
    else:
        acc.link_deletes.append(LinkDelete(key, rule.rule_id, rule.link_type, source, target))


def _resolve_endpoint(
    link_type: str, role: str, expression: ValueExpression, context: PlanResolutionContext, acc: _PlanAccumulator
) -> str:
    # A prior-rule output is a freshly created (already authorized) object; any other
    # endpoint is resolved through the context so the endpoint's row/segment visibility
    # is enforced (a hidden or missing object raises, never a silent link write).
    if isinstance(expression, PriorRuleOutputValue):
        created = acc.created_ids.get(expression.rule_id)
        if created is None:
            raise ValidationFailed(
                "link endpoint references an object not created in this action",
                details={"ruleId": expression.rule_id},
            )
        return created
    return context.resolve_link_endpoint(link_type, role, expression)


def read_set_versions_of(plan: EditPlan) -> Mapping[str, int]:
    return plan.read_set_versions
