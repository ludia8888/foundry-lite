"""Per-edit authorization and schema checks for resolved Action plans."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.action_types import ActionApplyCommand
from foundry_lite.application.ports import ActionTypeRow, PropertyTypeRow, TransactionContext
from foundry_lite.application.services.action_ir_compiler import (
    compile_action_definition,
    require_interface_create_plan_target,
    resolve_interface_action_definition,
)
from foundry_lite.application.services.action_plan_resolution import LivePlanResolutionContext
from foundry_lite.application.services.action_protocols import ActionObjectRecordLookup
from foundry_lite.application.services.ontology_lookup_service import OntologyLookupService
from foundry_lite.domain.action_runtime.action_contract import ActionDefinitionV3, compile_action_contract
from foundry_lite.domain.action_runtime.action_risk import action_plan_risk, require_declared_risk_floor
from foundry_lite.domain.action_runtime.edit_plan import EditPlan, validate_edit_plan
from foundry_lite.domain.action_runtime.edit_plan_builder import build_edit_plan
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed
from foundry_lite.security.policy import PolicyService


def resolve_authorized_action_edit_plan(
    conn: TransactionContext,
    ctx: RequestContext,
    policy: PolicyService,
    object_lookup: ActionObjectRecordLookup,
    ontology: OntologyLookupService,
    action_type: ActionTypeRow,
    command: ActionApplyCommand,
) -> EditPlan:
    """Resolve and authorize the exact plan that the committer will consume."""
    contract = compile_action_contract(action_type["definition"])
    if contract.function is not None:
        raise ValidationFailed("function-backed action requires a durable run")
    compiled = resolve_interface_action_definition(
        conn,
        ctx,
        ontology,
        contract,
        compile_action_definition(action_type["definition"]),
        command.object_type,
    )
    resolution = LivePlanResolutionContext(conn, ctx, command, object_lookup, ontology, ontology)
    plan = build_edit_plan(compiled, resolution)
    validate_edit_plan(plan)
    require_interface_create_plan_target(contract, compiled, plan, command.object_type, command.object_id)
    sensitive = authorize_action_edit_plan(conn, ctx, policy, ontology, contract, plan)
    require_declared_risk_floor(action_plan_risk(contract, plan, sensitive))
    return plan


def authorize_action_edit_plan(
    conn: TransactionContext,
    ctx: RequestContext,
    policy: PolicyService,
    ontology: OntologyLookupService,
    contract: ActionDefinitionV3,
    plan: EditPlan,
) -> Mapping[str, frozenset[str]]:
    """Require the principal and declared Action policy for every planned edit."""
    touched_types = _touched_object_types(plan)
    _require_declared_resource_roles(ctx, contract.permissions, "objectTypeRoles", touched_types)
    sensitive_by_type: dict[str, frozenset[str]] = {}
    for object_type in sorted(touched_types):
        policy.require(ctx, "object:read")
        policy.require(ctx, "object:edit")
        type_row = ontology._active_object_type(conn, ctx, object_type)
        properties = ontology._properties_for_object_type(conn, type_row["id"])
        _require_editable_properties(
            ctx, policy, contract, object_type, type_row["primary_key_property"], properties, plan
        )
        sensitive_by_type[object_type] = frozenset(
            prop["api_name"] for prop in properties if prop["classification"] in {"finance", "pii"}
        )
    if plan.objects_to_delete:
        policy.require(ctx, "object:delete")
    if plan.links_to_create or plan.links_to_delete:
        policy.require(ctx, "link:edit")
        links = {item.link_type for item in plan.links_to_create}
        links.update(item.link_type for item in plan.links_to_delete)
        _require_declared_resource_roles(ctx, contract.permissions, "linkTypeRoles", links)
    return sensitive_by_type


def inspect_action_edit_plan(
    conn: TransactionContext,
    ctx: RequestContext,
    ontology: OntologyLookupService,
    contract: ActionDefinitionV3,
    plan: EditPlan,
) -> Mapping[str, frozenset[str]]:
    """Validate a read-only plan without granting its caller mutation permissions."""

    touched_types = _touched_object_types(plan)
    _require_declared_resource_entries(contract.permissions, "objectTypeRoles", touched_types)
    sensitive_by_type: dict[str, frozenset[str]] = {}
    for object_type in sorted(touched_types):
        type_row = ontology._active_object_type(conn, ctx, object_type)
        properties = ontology._properties_for_object_type(conn, type_row["id"])
        _inspect_editable_properties(object_type, type_row["primary_key_property"], properties, plan)
        sensitive_by_type[object_type] = frozenset(
            prop["api_name"] for prop in properties if prop["classification"] in {"finance", "pii"}
        )
    links = {item.link_type for item in plan.links_to_create}
    links.update(item.link_type for item in plan.links_to_delete)
    _require_declared_resource_entries(contract.permissions, "linkTypeRoles", links)
    return sensitive_by_type


def _require_editable_properties(
    ctx: RequestContext,
    policy: PolicyService,
    contract: ActionDefinitionV3,
    object_type: str,
    primary_key_property: str,
    properties: Sequence[PropertyTypeRow],
    plan: EditPlan,
) -> None:
    sensitive = _inspect_editable_properties(object_type, primary_key_property, properties, plan)
    if sensitive:
        policy.require(ctx, "object:edit:sensitive")
    _require_declared_resource_roles(
        ctx,
        contract.permissions,
        "propertyRoles",
        {f"{object_type}.{name}" for name in _edited_properties(plan, object_type)},
    )


def _inspect_editable_properties(
    object_type: str,
    primary_key_property: str,
    properties: Sequence[PropertyTypeRow],
    plan: EditPlan,
) -> frozenset[str]:
    by_name = {row["api_name"]: row for row in properties}
    edited = _edited_properties(plan, object_type)
    modified = _modified_properties(plan, object_type)
    _require_known_properties(object_type, edited, by_name)
    _require_primary_key_unchanged(object_type, primary_key_property, modified)
    non_editable = {name for name in modified if not by_name[name]["editable"]}
    if non_editable:
        raise PermissionDenied(
            "action plan edits non-editable properties",
            details={"objectType": object_type, "properties": sorted(non_editable)},
        )
    return frozenset(name for name in edited if by_name[name]["classification"] in {"finance", "pii"})


def _require_known_properties(
    object_type: str,
    edited: set[str],
    by_name: Mapping[str, PropertyTypeRow],
) -> None:
    missing = edited - set(by_name)
    if missing:
        raise ValidationFailed(
            "action plan edits unknown properties",
            details={"objectType": object_type, "properties": sorted(missing)},
        )


def _require_primary_key_unchanged(
    object_type: str,
    primary_key_property: str,
    modified: set[str],
) -> None:
    if primary_key_property in modified:
        raise PermissionDenied(
            "action plan cannot modify an object primary key",
            details={"objectType": object_type, "property": primary_key_property},
        )


def _touched_object_types(plan: EditPlan) -> set[str]:
    return {
        item.object_type
        for items in (plan.objects_to_create, plan.objects_to_modify, plan.objects_to_delete)
        for item in items
    }


def _edited_properties(plan: EditPlan, object_type: str) -> set[str]:
    created = {name for item in plan.objects_to_create if item.object_type == object_type for name in item.properties}
    return created | _modified_properties(plan, object_type)


def _modified_properties(plan: EditPlan, object_type: str) -> set[str]:
    return {name for item in plan.objects_to_modify if item.object_type == object_type for name in item.patch}


def _require_declared_resource_roles(
    ctx: RequestContext,
    permissions: Mapping[str, object],
    field: str,
    resources: set[str],
) -> None:
    declarations = permissions.get(field)
    if declarations is None:
        return
    if not isinstance(declarations, Mapping):
        raise PermissionDenied("action resource permission policy is invalid", details={"field": field})
    for resource in sorted(resources):
        allowed = declarations.get(resource)
        if allowed is None:
            raise PermissionDenied(
                "action resource permission policy is missing",
                details={"field": field, "resource": resource},
            )
        if not _has_declared_role(ctx, allowed):
            raise PermissionDenied(
                "action resource permission denied",
                details={"field": field, "resource": resource},
            )


def _require_declared_resource_entries(permissions: Mapping[str, object], field: str, resources: set[str]) -> None:
    declarations = permissions.get(field)
    if declarations is None:
        return
    if not isinstance(declarations, Mapping):
        raise PermissionDenied("action resource permission policy is invalid", details={"field": field})
    missing = sorted(resource for resource in resources if declarations.get(resource) is None)
    if missing:
        raise PermissionDenied(
            "action resource permission policy is missing",
            details={"field": field, "resources": missing},
        )


def _has_declared_role(ctx: RequestContext, raw: object) -> bool:
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        return False
    allowed = {item for item in raw if isinstance(item, str)} | {"admin"}
    return bool(set(ctx.roles) & allowed)
