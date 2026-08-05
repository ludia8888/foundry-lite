"""Resolve interface-scoped Action rules to one authorized concrete object type."""

from __future__ import annotations

from dataclasses import replace

from foundry_lite.application.ports import (
    InterfaceTypeRow,
    LinkTypeRow,
    ObjectRecordRow,
    ObjectTypeRow,
    TransactionContext,
)
from foundry_lite.application.ports.ontology_definitions import InterfaceLinkConstraintDefinition
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.action_protocols import ActionOntologyLookup
from foundry_lite.application.services.ontology_interface_validation import persisted_implements
from foundry_lite.domain.action_runtime.action_contract import ActionDefinitionV3
from foundry_lite.domain.action_runtime.action_ir import (
    ActionDefinitionV2,
    ActionRule,
    CreateLinkRule,
    CreateObjectRule,
    DeleteLinkRule,
    DeleteObjectRule,
    ModifyObjectRule,
    PropertyAssignment,
    ResolvedInterfaceLinkDeleteRule,
)
from foundry_lite.domain.action_runtime.edit_plan import EditPlan, ObjectCreate
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


def is_interface_create_action(contract: ActionDefinitionV3, definition: ActionDefinitionV2) -> bool:
    """Whether the request target is the new object created by an interface rule."""
    if contract.target.kind != "interface":
        return False
    return any(
        isinstance(rule, CreateObjectRule) and rule.on_interface == contract.target.api_name
        for rule in definition.rules
    )


def interface_create_target_record(
    contract: ActionDefinitionV3,
    definition: ActionDefinitionV2,
    concrete: ObjectTypeRow,
    object_id: str,
    expected_object_version: int,
    tenant_id: str,
) -> ObjectRecordRow | None:
    """Build the version-zero validation context for an interface create request."""
    if not is_interface_create_action(contract, definition):
        return None
    if expected_object_version != 0 or not object_id.strip():
        raise ValidationFailed(
            "interface create requires a non-empty object id and expectedObjectVersion 0",
            details={"objectId": object_id, "expectedObjectVersion": expected_object_version},
        )
    now = _now()
    return {
        "id": f"new:{concrete['api_name']}:{object_id}",
        "tenant_id": tenant_id,
        "object_type_id": concrete["id"],
        "object_type_api_name": concrete["api_name"],
        "object_id": object_id,
        "index_version": "action-interface-create",
        "is_active": True,
        "properties": {},
        "base_properties": {},
        "edit_properties": {},
        "property_versions": {},
        "source_dataset_version_id": None,
        "source_hash": None,
        "object_version": 0,
        "deleted": False,
        "deletion_reason": None,
        "created_at": now,
        "updated_at": now,
    }


def require_interface_create_plan_target(
    contract: ActionDefinitionV3,
    definition: ActionDefinitionV2,
    plan: EditPlan,
    concrete_type: str,
    requested_object_id: str,
) -> None:
    if not is_interface_create_action(contract, definition):
        return
    targets = _interface_create_targets(contract, definition, plan, concrete_type)
    if len(targets) == 1 and targets[0].primary_key == requested_object_id:
        return
    raise ValidationFailed(
        "interface create must produce exactly the requested concrete object",
        details={
            "objectType": concrete_type,
            "objectId": requested_object_id,
            "createdObjectIds": [item.primary_key for item in targets],
        },
    )


def _interface_create_targets(
    contract: ActionDefinitionV3,
    definition: ActionDefinitionV2,
    plan: EditPlan,
    concrete_type: str,
) -> list[ObjectCreate]:
    rule_ids = {
        rule.rule_id
        for rule in definition.rules
        if isinstance(rule, CreateObjectRule) and rule.on_interface == contract.target.api_name
    }
    return [item for item in plan.objects_to_create if item.rule_id in rule_ids and item.object_type == concrete_type]


def resolve_interface_action_definition(
    conn: TransactionContext,
    ctx: RequestContext,
    ontology: ActionOntologyLookup,
    contract: ActionDefinitionV3,
    definition: ActionDefinitionV2,
    requested_object_type: str,
) -> ActionDefinitionV2:
    """Validate interface membership and bind polymorphic rules to a concrete type."""
    if contract.target.kind != "interface":
        _validate_declared_rule_interfaces(conn, ctx, ontology, definition)
        return definition
    concrete = ontology._active_object_type(conn, ctx, requested_object_type)
    interface = _required_interface(conn, ctx, ontology, contract.target.api_name)
    _require_implements(concrete, interface["api_name"])
    rules = tuple(
        bound
        for rule in definition.rules
        for bound in _bind_interface_rules(conn, ctx, ontology, rule, interface, requested_object_type)
    )
    return ActionDefinitionV2(api_name=definition.api_name, rules=rules)


def require_interface_action_target(
    conn: TransactionContext,
    ctx: RequestContext,
    ontology: ActionOntologyLookup,
    contract: ActionDefinitionV3,
    requested_object_type: str,
) -> ObjectTypeRow:
    """Validate a request target against an object or interface Action contract."""
    concrete = ontology._active_object_type(conn, ctx, requested_object_type)
    if contract.target.kind == "object":
        if concrete["api_name"] != contract.target.api_name:
            raise ValidationFailed(
                "action target object type mismatch",
                details={"expectedObjectType": contract.target.api_name, "requestedObjectType": requested_object_type},
            )
        return concrete
    interface = _required_interface(conn, ctx, ontology, contract.target.api_name)
    _require_implements(concrete, interface["api_name"])
    return concrete


def _validate_declared_rule_interfaces(
    conn: TransactionContext,
    ctx: RequestContext,
    ontology: ActionOntologyLookup,
    definition: ActionDefinitionV2,
) -> None:
    for rule in definition.rules:
        if isinstance(rule, CreateLinkRule | DeleteLinkRule) and rule.on_interface is not None:
            raise ValidationFailed(
                "interface link rules require an interface-target Action",
                details={"interface": rule.on_interface, "ruleId": rule.rule_id},
            )
        interface = getattr(rule, "on_interface", None)
        object_type = getattr(rule, "object_type", None)
        if interface is None or not isinstance(object_type, str):
            continue
        concrete = ontology._active_object_type(conn, ctx, object_type)
        _require_implements(concrete, interface)
        _require_shared_assignments(_required_interface(conn, ctx, ontology, interface), _assignments(rule))


def _bind_interface_rules(
    conn: TransactionContext,
    ctx: RequestContext,
    ontology: ActionOntologyLookup,
    rule: ActionRule,
    interface: InterfaceTypeRow,
    concrete_type: str,
) -> tuple[ActionRule, ...]:
    if isinstance(rule, CreateLinkRule | DeleteLinkRule):
        return _bind_interface_link_rules(conn, ctx, ontology, rule, interface, concrete_type)
    if not isinstance(rule, CreateObjectRule | ModifyObjectRule | DeleteObjectRule):
        return (rule,)
    if rule.on_interface != interface["api_name"]:
        raise ValidationFailed(
            "interface Action rule is not bound to its target interface",
            details={"interface": interface["api_name"], "ruleId": rule.rule_id},
        )
    _require_shared_assignments(interface, _assignments(rule))
    return (replace(rule, object_type=concrete_type),)


def _bind_interface_link_rules(
    conn: TransactionContext,
    ctx: RequestContext,
    ontology: ActionOntologyLookup,
    rule: CreateLinkRule | DeleteLinkRule,
    interface: InterfaceTypeRow,
    concrete_type: str,
) -> tuple[ActionRule, ...]:
    constraint = _required_link_constraint(rule, interface)
    candidates = _concrete_link_types(conn, ctx, ontology, interface, concrete_type, constraint)
    if not candidates:
        raise ValidationFailed(
            "concrete object type does not implement the interface link constraint",
            details={
                "interface": interface["api_name"],
                "constraint": constraint["apiName"],
                "objectType": concrete_type,
            },
        )
    if isinstance(rule, CreateLinkRule):
        return (_single_create_link(rule, interface, constraint, candidates),)
    if len(candidates) == 1:
        return (replace(rule, link_type=candidates[0]["api_name"]),)
    return (
        ResolvedInterfaceLinkDeleteRule(
            rule_id=rule.rule_id,
            concrete_link_types=tuple(row["api_name"] for row in candidates),
            source=rule.source,
            target=rule.target,
        ),
    )


def _required_link_constraint(
    rule: CreateLinkRule | DeleteLinkRule, interface: InterfaceTypeRow
) -> InterfaceLinkConstraintDefinition:
    if rule.on_interface != interface["api_name"] or rule.interface_link_constraint is None:
        raise ValidationFailed(
            "interface Action link rules require their target interface and link constraint",
            details={"interface": interface["api_name"], "ruleId": rule.rule_id},
        )
    for constraint in interface["definition"].get("linkConstraints", ()):
        if constraint["apiName"] == rule.interface_link_constraint:
            return constraint
    raise ValidationFailed(
        "interface link constraint reference was not found",
        details={"interface": interface["api_name"], "constraint": rule.interface_link_constraint},
    )


def _concrete_link_types(
    conn: TransactionContext,
    ctx: RequestContext,
    ontology: ActionOntologyLookup,
    interface: InterfaceTypeRow,
    concrete_type: str,
    constraint: InterfaceLinkConstraintDefinition,
) -> tuple[LinkTypeRow, ...]:
    rows = ontology._link_types_for_version(conn, ctx, interface["ontology_version_id"])
    matches = [
        row
        for row in rows
        if row["from_api_name"] == concrete_type
        and _link_cardinality_matches(row, constraint)
        and _link_target_matches(conn, ctx, ontology, row, constraint)
    ]
    return tuple(sorted(matches, key=lambda row: row["api_name"]))


def _link_cardinality_matches(row: LinkTypeRow, constraint: InterfaceLinkConstraintDefinition) -> bool:
    allowed = (
        {"one_to_one", "many_to_one"}
        if constraint["cardinality"] == "one"
        else {
            "one_to_many",
            "many_to_many",
        }
    )
    return row["cardinality"] in allowed


def _link_target_matches(
    conn: TransactionContext,
    ctx: RequestContext,
    ontology: ActionOntologyLookup,
    row: LinkTypeRow,
    constraint: InterfaceLinkConstraintDefinition,
) -> bool:
    if constraint["targetKind"] == "object":
        return row["to_api_name"] == constraint["target"]
    target = ontology._active_object_type(conn, ctx, row["to_api_name"])
    return constraint["target"] in persisted_implements(target["config"])


def _single_create_link(
    rule: CreateLinkRule,
    interface: InterfaceTypeRow,
    constraint: InterfaceLinkConstraintDefinition,
    candidates: tuple[LinkTypeRow, ...],
) -> CreateLinkRule:
    if len(candidates) != 1:
        raise ValidationFailed(
            "interface link create requires exactly one concrete implementation",
            details={
                "interface": interface["api_name"],
                "constraint": constraint["apiName"],
                "linkTypes": [row["api_name"] for row in candidates],
            },
        )
    return replace(rule, link_type=candidates[0]["api_name"])


def _assignments(rule: ActionRule) -> tuple[PropertyAssignment, ...]:
    return rule.assignments if isinstance(rule, CreateObjectRule | ModifyObjectRule) else ()


def _require_shared_assignments(interface: InterfaceTypeRow, assignments: tuple[PropertyAssignment, ...]) -> None:
    shared = {str(prop["apiName"]) for prop in interface["definition"]["properties"]}
    invalid = sorted({assignment.property for assignment in assignments} - shared)
    if invalid:
        raise ValidationFailed(
            "interface Action may edit shared interface properties only",
            details={"interface": interface["api_name"], "properties": invalid},
        )


def _required_interface(
    conn: TransactionContext,
    ctx: RequestContext,
    ontology: ActionOntologyLookup,
    api_name: str,
) -> InterfaceTypeRow:
    active = ontology._active_ontology_version(conn, ctx)
    for row in ontology._interface_types_for_version(conn, ctx, active["id"]):
        if row["api_name"] == api_name:
            return row
    raise ValidationFailed("action interface reference was not found", details={"interface": api_name})


def _require_implements(object_type: ObjectTypeRow, interface: str) -> None:
    if interface in persisted_implements(object_type["config"]):
        return
    raise ValidationFailed(
        "action target object type does not implement the interface",
        details={"objectType": object_type["api_name"], "interface": interface},
    )
