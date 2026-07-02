"""OSDK OAuth authorize/token/refresh/revoke routes."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Query, Request
from foundry_lite.domain.errors import FoundryLiteError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import JsonObject, OsdkOAuthRefreshRequest, OsdkOAuthTokenRequest

router = APIRouter()


def _scope_query(scope: str | None) -> tuple[str, ...]:
    if not scope:
        return ()
    return tuple(item.strip() for item in scope.replace(",", " ").split(" ") if item.strip())


@router.get("/api/auth/osdk/oauth/authorize")
def authorize_osdk_oauth(
    request: Request,
    client_id: str = Query(alias="clientId"),
    redirect_uri: str = Query(alias="redirectUri"),
    code_challenge: str = Query(alias="codeChallenge"),
    code_challenge_method: str = Query(default="S256", alias="codeChallengeMethod"),
    scope: str | None = Query(default=None),
    state: str | None = Query(default=None),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            runtime.foundry.auth.osdk_oauth_authorize(
                client_id=client_id,
                redirect_uri=redirect_uri,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
                scopes=_scope_query(scope),
                state=state,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/auth/osdk/oauth/token")
def exchange_osdk_oauth_token(request: Request, payload: OsdkOAuthTokenRequest) -> JsonObject:
    try:
        return cast(
            JsonObject,
            runtime.foundry.auth.osdk_oauth_token(
                client_id=payload.client_id,
                code=payload.code,
                redirect_uri=payload.redirect_uri,
                code_verifier=payload.code_verifier,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/auth/osdk/oauth/refresh")
def refresh_osdk_oauth_token(request: Request, payload: OsdkOAuthRefreshRequest) -> JsonObject:
    try:
        return cast(
            JsonObject, runtime.foundry.auth.osdk_oauth_refresh(refresh_token=payload.refresh_token, ctx=_ctx(request))
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/auth/osdk/oauth/revoke")
def revoke_osdk_oauth_token(request: Request, payload: OsdkOAuthRefreshRequest) -> JsonObject:
    try:
        return cast(
            JsonObject, runtime.foundry.auth.osdk_oauth_revoke(refresh_token=payload.refresh_token, ctx=_ctx(request))
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
