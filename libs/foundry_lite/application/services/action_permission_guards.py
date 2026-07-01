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
    permission = f"action:execute:{action_api_name}"
    try:
        policy.require(ctx, permission)
    except PermissionDenied:
        with engine.begin() as conn:
            runtime_service._audit(
                conn,
                ctx,
                event_type="permission.denied",
                resource_type="action_type",
                resource_id=action_api_name,
                action=action,
                decision="deny",
                after_ref={"permission": permission},
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
