"""Exact online authorization for Consumer MCP Action planning."""

from foundry_lite.application.ports import TransactionManager
from foundry_lite.application.services.action_permission_guards import require_action_target_read
from foundry_lite.application.services.action_protocols import ActionOsdkScopeBoundary, ActionRuntimeBoundary
from foundry_lite.application.services.osdk_service_principal_authorization import (
    ServicePrincipalAccessSessionBoundary,
    require_service_principal_scope,
    service_principal_reader_context,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.security.policy import PolicyService


def authorize_external_mcp_action_plan(
    engine: TransactionManager,
    policy: PolicyService,
    runtime: ActionRuntimeBoundary,
    access_sessions: ServicePrincipalAccessSessionBoundary,
    application_scopes: ActionOsdkScopeBoundary,
    ctx: RequestContext,
    action_api_name: str,
    object_type: str,
    object_id: str,
) -> RequestContext:
    require_service_principal_scope(
        ctx,
        access_sessions,
        application_scopes,
        resource_type="action",
        resource_api_name=action_api_name,
        operation="validate",
    )
    require_service_principal_scope(
        ctx,
        access_sessions,
        application_scopes,
        resource_type="object",
        resource_api_name=object_type,
        operation="read",
    )
    reader_ctx = service_principal_reader_context(ctx)
    require_action_target_read(
        engine,
        policy,
        runtime,
        reader_ctx,
        action_api_name,
        object_type,
        object_id,
        action="plan",
    )
    return reader_ctx
