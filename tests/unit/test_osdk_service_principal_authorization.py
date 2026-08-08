from __future__ import annotations

import pytest
from foundry_lite.application.services import osdk_service_principal_authorization as authorization
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied


class _UnexpectedAccessSession:
    def require_active(self, ctx: RequestContext, application_id: str) -> None:
        raise AssertionError("missing application must fail before the access-session lookup")


class _UnexpectedApplicationScope:
    def require_resource_scope(
        self,
        ctx: RequestContext,
        *,
        resource_type: str,
        resource_api_name: str,
        operation: str,
    ) -> None:
        raise AssertionError("missing application must fail before the application-scope lookup")


def test_require_service_principal_scope_rejects_missing_application_defensively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = RequestContext(
        actor_user_id="service-principal:machine",
        client_id="machine",
        oauth_session_id="session-1",
        roles=(authorization.OSDK_SERVICE_PRINCIPAL_ROLE,),
        token_scopes=("osdk:object:Order:read",),
    )
    monkeypatch.setattr(authorization, "is_client_credentials_service_principal", lambda _ctx: True)

    with pytest.raises(PermissionDenied, match="application is required"):
        authorization.require_service_principal_scope(
            ctx,
            _UnexpectedAccessSession(),
            _UnexpectedApplicationScope(),
            resource_type="object",
            resource_api_name="Order",
            operation="read",
        )
