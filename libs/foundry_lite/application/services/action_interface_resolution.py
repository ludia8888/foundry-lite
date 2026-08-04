"""Resolve interface-scoped Action rules to one authorized concrete object type."""

from __future__ import annotations

from dataclasses import replace

from foundry_lite.application.ports import InterfaceTypeRow, ObjectTypeRow, TransactionContext
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
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


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
    rules = tuple(_bind_interface_rule(rule, interface, requested_object_type) for rule in definition.rules)
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
        interface = getattr(rule, "on_interface", None)
        object_type = getattr(rule, "object_type", None)
        if interface is None or not isinstance(object_type, str):
            continue
        concrete = ontology._active_object_type(conn, ctx, object_type)
        _require_implements(concrete, interface)
        _require_shared_assignments(_required_interface(conn, ctx, ontology, interface), _assignments(rule))


def _bind_interface_rule(rule: ActionRule, interface: InterfaceTypeRow, concrete_type: str) -> ActionRule:
    if isinstance(rule, CreateLinkRule | DeleteLinkRule):
        raise ValidationFailed(
            "interface Action link rules require an interface link constraint",
            details={"interface": interface["api_name"], "ruleId": rule.rule_id},
        )
    if not isinstance(rule, CreateObjectRule | ModifyObjectRule | DeleteObjectRule):
        return rule
    if rule.on_interface != interface["api_name"]:
        raise ValidationFailed(
            "interface Action rule is not bound to its target interface",
            details={"interface": interface["api_name"], "ruleId": rule.rule_id},
        )
    _require_shared_assignments(interface, _assignments(rule))
    return replace(rule, object_type=concrete_type)


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
