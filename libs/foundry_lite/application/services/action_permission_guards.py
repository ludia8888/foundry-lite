"""Application service helpers for action permission guards workflows."""

from __future__ import annotations

from foundry_lite.application.action_types import ActionApplyCommand
from foundry_lite.application.ports import TransactionManager
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
