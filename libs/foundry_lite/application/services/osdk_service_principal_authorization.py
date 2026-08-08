"""Narrow online authorization for confidential OSDK service principals."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from foundry_lite.application.ports import OsdkResourceOperation, OsdkResourceType
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied
from foundry_lite.domain.platform.scopes import resource_scope

OSDK_SERVICE_PRINCIPAL_ROLE = "osdk_service_principal"


class ServicePrincipalAccessSessionBoundary(Protocol):
    """Online bearer-session check used by narrow machine entrypoints."""

    def require_active(self, ctx: RequestContext, application_id: str) -> None: ...


class ServicePrincipalApplicationScopeBoundary(Protocol):
    """Current durable application-grant check used after token validation."""

    def require_resource_scope(
        self,
        ctx: RequestContext,
        *,
        resource_type: OsdkResourceType,
        resource_api_name: str,
        operation: OsdkResourceOperation,
    ) -> None: ...


def is_client_credentials_service_principal(ctx: RequestContext) -> bool:
    """Return whether the context has the non-elevated machine identity shape."""

    return bool(
        ctx.application_id
        and ctx.client_id
        and ctx.oauth_session_id
        and ctx.actor_user_id == f"service-principal:{ctx.client_id}"
        and ctx.roles == (OSDK_SERVICE_PRINCIPAL_ROLE,)
    )


def require_service_principal_scope(
    ctx: RequestContext,
    access_sessions: ServicePrincipalAccessSessionBoundary,
    application_scopes: ServicePrincipalApplicationScopeBoundary,
    *,
    resource_type: OsdkResourceType,
    resource_api_name: str,
    operation: OsdkResourceOperation,
) -> None:
    """Require exact token, live session, and current app grant without role elevation."""

    if not is_client_credentials_service_principal(ctx):
        raise PermissionDenied("OSDK client_credentials service principal is required")
    expected = resource_scope(resource_type, resource_api_name, operation)
    if expected not in ctx.token_scopes:
        raise PermissionDenied(
            "OSDK service principal token scope denied",
            details={"requiredScope": expected},
        )
    application_id = ctx.application_id
    if application_id is None:
        raise PermissionDenied("OSDK service principal application is required")
    access_sessions.require_active(ctx, application_id)
    application_scopes.require_resource_scope(
        ctx,
        resource_type=resource_type,
        resource_api_name=resource_api_name,
        operation=operation,
    )


def service_principal_reader_context(ctx: RequestContext) -> RequestContext:
    """Return the read-only policy projection used only after the exact online check."""

    if not is_client_credentials_service_principal(ctx):
        raise PermissionDenied("OSDK client_credentials service principal is required")
    return replace(ctx, roles=("viewer",))
