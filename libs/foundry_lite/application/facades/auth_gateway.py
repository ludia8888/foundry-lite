"""Thin facade entrypoints for auth gateway workflows."""

from __future__ import annotations

from collections.abc import Sequence

from foundry_lite.application.ports import RuntimeJsonObject
from foundry_lite.application.services.osdk_application_service import OsdkApplicationService
from foundry_lite.application.services.osdk_dynamic_client_registration import (
    DynamicClientRegistration,
    register_dynamic_client,
)
from foundry_lite.application.services.osdk_oauth_client_credentials_service import (
    OsdkOAuthClientCredentialsService,
)
from foundry_lite.application.services.osdk_oauth_session_service import OsdkOAuthSessionService
from foundry_lite.domain.context import RequestContext
from foundry_lite.observability.tracing import trace_public_methods


@trace_public_methods
class AuthGateway:
    """Auth facade for local OSDK OAuth/session lifecycle."""

    def __init__(
        self,
        osdk_oauth_sessions: OsdkOAuthSessionService,
        osdk_oauth_client_credentials: OsdkOAuthClientCredentialsService,
        osdk_applications: OsdkApplicationService,
    ) -> None:
        self._osdk_oauth_sessions = osdk_oauth_sessions
        self._osdk_oauth_client_credentials = osdk_oauth_client_credentials
        self._osdk_applications = osdk_applications

    def osdk_oauth_register_dynamic_client(
        self,
        *,
        application_id: str,
        registration: DynamicClientRegistration,
    ) -> RuntimeJsonObject:
        return register_dynamic_client(
            applications=self._osdk_applications,
            sessions=self._osdk_oauth_sessions,
            application_id=application_id,
            registration=registration,
        )

    def osdk_oauth_authorize(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str = "S256",
        scopes: Sequence[str] = (),
        state: str | None = None,
        resource: str | None = None,
        resource_application_id: str | None = None,
        ctx: RequestContext | None = None,
    ) -> RuntimeJsonObject:
        return self._osdk_oauth_sessions.authorize(
            ctx=ctx,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            scopes=scopes,
            state=state,
            resource=resource,
            resource_application_id=resource_application_id,
        )

    def osdk_oauth_token(
        self,
        *,
        client_id: str,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        resource: str | None = None,
        resource_application_id: str | None = None,
        ctx: RequestContext | None = None,
    ) -> RuntimeJsonObject:
        return self._osdk_oauth_sessions.exchange_authorization_code(
            ctx=ctx,
            client_id=client_id,
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
            resource=resource,
            resource_application_id=resource_application_id,
        )

    def osdk_oauth_refresh(
        self,
        *,
        refresh_token: str,
        client_id: str | None = None,
        resource: str | None = None,
        resource_application_id: str | None = None,
        should_reevaluate_roles: bool = True,
        ctx: RequestContext | None = None,
    ) -> RuntimeJsonObject:
        return self._osdk_oauth_sessions.refresh_access_token(
            ctx=ctx,
            refresh_token=refresh_token,
            client_id=client_id,
            resource=resource,
            resource_application_id=resource_application_id,
            should_reevaluate_roles=should_reevaluate_roles,
        )

    def osdk_oauth_resource_tenant(self, application_id: str, client_id: str) -> str:
        return self._osdk_oauth_sessions.resolve_resource_tenant(application_id, client_id)

    def osdk_oauth_application_scopes(self, application_id: str) -> tuple[str, ...]:
        return self._osdk_oauth_sessions.application_scopes(application_id)

    def osdk_oauth_issuer(self) -> str:
        return str(self._osdk_oauth_sessions.oauth_token_issuer.issuer)

    def osdk_oauth_client_credentials(
        self,
        *,
        client_id: str,
        client_secret: str,
        scopes: Sequence[str] = (),
        resource: str | None = None,
        ctx: RequestContext | None = None,
    ) -> RuntimeJsonObject:
        return self._osdk_oauth_client_credentials.exchange(
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
            resource=resource,
            ctx=ctx,
        )

    def verify_osdk_oauth_client_credentials(
        self,
        *,
        client_id: str,
        client_secret: str,
        ctx: RequestContext,
    ) -> None:
        self._osdk_oauth_client_credentials.verify_client_credentials(
            client_id=client_id,
            client_secret=client_secret,
            ctx=ctx,
        )

    def osdk_oauth_revoke(self, *, refresh_token: str, ctx: RequestContext | None = None) -> RuntimeJsonObject:
        return self._osdk_oauth_sessions.revoke(ctx=ctx, refresh_token=refresh_token)
