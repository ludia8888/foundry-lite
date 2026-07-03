"""Application service helpers for action permission guards workflows."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.action_types import ActionApplyCommand
from foundry_lite.application.ports import ActionTypeRow, TransactionManager
from foundry_lite.application.services.action_helpers import (
    SupportsAudit,
    failure_injection_audit_ref,
    require_failure_injection_allowed,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied
from foundry_lite.security.policy import PolicyService


def require_action_permission(
    engine: TransactionManager,
    policy: PolicyService,
    runtime_service: SupportsAudit,
    ctx: RequestContext,
    action_api_name: str,
    *,
    action: str = "apply",
) -> None:
    """Require the per-action execute permission, leaving audit evidence on deny."""
    permission = f"action:execute:{action_api_name}"
    _require_permission_with_audit(
        engine,
        policy,
        runtime_service,
        ctx,
        permission,
        resource_type="action_type",
        resource_id=action_api_name,
        action=action,
        after_ref={"permission": permission},
    )


def require_action_target_read(
    engine: TransactionManager,
    policy: PolicyService,
    runtime_service: SupportsAudit,
    ctx: RequestContext,
    action_api_name: str,
    object_type: str,
    object_id: str,
    *,
    action: str = "apply",
) -> None:
    """Deny acting on a target object the caller cannot view.

    Palantir semantics: executing or validating an action presumes the actor can
    load the target object, so per-action execute permission must never bypass
    ``object:read`` policy. Denials leave the same audit evidence as execute denials.
    """
    _require_permission_with_audit(
        engine,
        policy,
        runtime_service,
        ctx,
        "object:read",
        resource_type="object",
        resource_id=f"{object_type}:{object_id}",
        action=action,
        after_ref={"permission": "object:read", "action_type": action_api_name},
    )


def segment_mutation_denied_error(
    policy: PolicyService,
    ctx: RequestContext,
    action_type: ActionTypeRow,
) -> PermissionDenied | None:
    """Deny mutating a property whose datasource segment the caller cannot view.

    Palantir semantics: editing a property requires VIEW access to that
    property's datasource segment. Segment-masked properties are exactly the
    ones whose values read as null for this caller, so writing through them
    would be a blind (and privilege-escalating) edit.
    """
    denied = policy.segment_masked_property_names(ctx, str(action_type["target_api_name"]))
    if not denied:
        return None
    blocked = sorted(_mutation_property_names(action_type) & denied)
    if not blocked:
        return None
    return PermissionDenied(
        "action mutates properties in a datasource segment the caller cannot view",
        details={"actionType": str(action_type["api_name"]), "properties": blocked},
    )


def _mutation_property_names(action_type: ActionTypeRow) -> set[str]:
    mutations = action_type["definition"].get("mutations", ())
    names: set[str] = set()
    for mutation in mutations:
        if isinstance(mutation, Mapping) and isinstance(mutation.get("property"), str):
            names.add(str(mutation["property"]))
    return names


def _require_permission_with_audit(
    engine: TransactionManager,
    policy: PolicyService,
    runtime_service: SupportsAudit,
    ctx: RequestContext,
    permission: str,
    *,
    resource_type: str,
    resource_id: str,
    action: str,
    after_ref: dict[str, object],
) -> None:
    """Shared deny path: a permission failure must be audited before it propagates."""
    try:
        policy.require(ctx, permission)
    except PermissionDenied:
        with engine.begin() as conn:
            runtime_service._audit(
                conn,
                ctx,
                event_type="permission.denied",
                resource_type=resource_type,
                resource_id=resource_id,
                action=action,
                decision="deny",
                after_ref=after_ref,
            )
        raise


def require_failure_injection_for_command(
    engine: TransactionManager,
    runtime_service: SupportsAudit,
    ctx: RequestContext,
    command: ActionApplyCommand,
) -> None:
    try:
        require_failure_injection_allowed(command)
    except PermissionDenied:
        with engine.begin() as conn:
            runtime_service._audit(
                conn,
                ctx,
                event_type="action.failure_injection.denied",
                resource_type="action_type",
                resource_id=command.action_api_name,
                action="apply",
                decision="deny",
                after_ref=failure_injection_audit_ref(command),
            )
        raise
