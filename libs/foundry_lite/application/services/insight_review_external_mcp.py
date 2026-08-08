"""Exact authorization helpers for Consumer MCP-owned review rows."""

from collections.abc import Mapping

from foundry_lite.application.services.action_protocols import ActionOsdkScopeBoundary
from foundry_lite.application.services.osdk_service_principal_authorization import (
    ServicePrincipalAccessSessionBoundary,
    is_client_credentials_service_principal,
    require_service_principal_scope,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, PermissionDenied
from foundry_lite.domain.platform.scopes import resource_scope


def require_external_mcp_action(
    ctx: RequestContext,
    action_name: str,
    access_sessions: ServicePrincipalAccessSessionBoundary,
    application_scopes: ActionOsdkScopeBoundary,
) -> None:
    if is_client_credentials_service_principal(ctx):
        require_service_principal_scope(
            ctx,
            access_sessions,
            application_scopes,
            resource_type="action",
            resource_api_name=action_name,
            operation="execute",
        )
        return
    expected_scope = resource_scope("action", action_name, "execute")
    if not ctx.application_id or not ctx.client_id or expected_scope not in ctx.token_scopes:
        raise PermissionDenied("Ontology MCP approval access is not authorized")
    application_scopes.require_resource_scope(
        ctx,
        resource_type="action",
        resource_api_name=action_name,
        operation="execute",
    )


def external_mcp_review_action_name(review: Mapping[str, object], application_id: str, ctx: RequestContext) -> str:
    proposal = review.get("actionProposal")
    if not isinstance(proposal, Mapping):
        raise NotFound("Ontology MCP approval was not found")
    action_name = proposal.get("actionApiName", proposal.get("actionType"))
    owner_mismatch = (
        proposal.get("source") != "ontology_mcp"
        or proposal.get("applicationId") != application_id
        or review.get("createdByUserId") != ctx.actor_user_id
    )
    if is_client_credentials_service_principal(ctx):
        owner_mismatch = owner_mismatch or proposal.get("clientId") != ctx.client_id
    if owner_mismatch or not isinstance(action_name, str) or not action_name:
        raise NotFound("Ontology MCP approval was not found")
    return action_name
