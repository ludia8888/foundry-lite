"""Thin facade entrypoints for auth gateway workflows."""

from __future__ import annotations

from collections.abc import Sequence

from foundry_lite.application.ports import RuntimeJsonObject
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
    ) -> None:
        self._osdk_oauth_sessions = osdk_oauth_sessions
        self._osdk_oauth_client_credentials = osdk_oauth_client_credentials

    def osdk_oauth_authorize(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str = "S256",
        scopes: Sequence[str] = (),
        state: str | None = None,
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
        )

    def osdk_oauth_token(
        self,
        *,
        client_id: str,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        ctx: RequestContext | None = None,
    ) -> RuntimeJsonObject:
        return self._osdk_oauth_sessions.exchange_authorization_code(
            ctx=ctx,
            client_id=client_id,
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
        )

    def osdk_oauth_refresh(self, *, refresh_token: str, ctx: RequestContext | None = None) -> RuntimeJsonObject:
        return self._osdk_oauth_sessions.refresh_access_token(ctx=ctx, refresh_token=refresh_token)

    def osdk_oauth_client_credentials(
        self,
        *,
        client_id: str,
        client_secret: str,
        scopes: Sequence[str] = (),
        ctx: RequestContext | None = None,
    ) -> RuntimeJsonObject:
        return self._osdk_oauth_client_credentials.exchange(
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
            ctx=ctx,
        )

    def osdk_oauth_revoke(self, *, refresh_token: str, ctx: RequestContext | None = None) -> RuntimeJsonObject:
        return self._osdk_oauth_sessions.revoke(ctx=ctx, refresh_token=refresh_token)
