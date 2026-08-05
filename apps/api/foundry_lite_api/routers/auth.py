"""OSDK OAuth authorize/token/refresh/revoke routes."""

from __future__ import annotations

import json
from typing import cast
from urllib.parse import parse_qs

from fastapi import APIRouter, Query, Request
from foundry_lite.domain.errors import FoundryLiteError, ValidationFailed
from pydantic import ValidationError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import JsonObject, OsdkOAuthRefreshRequest, OsdkOAuthTokenRequest

router = APIRouter()
_MAX_TOKEN_REQUEST_BYTES = 16_384


def _scope_query(scope: str | None) -> tuple[str, ...]:
    if not scope:
        return ()
    return tuple(item.strip() for item in scope.replace(",", " ").split(" ") if item.strip())


@router.get("/api/auth/osdk/oauth/authorize")
def authorize_osdk_oauth(
    request: Request,
    client_id: str | None = Query(default=None, alias="clientId"),
    oauth_client_id: str | None = Query(default=None, alias="client_id"),
    redirect_uri: str | None = Query(default=None, alias="redirectUri"),
    oauth_redirect_uri: str | None = Query(default=None, alias="redirect_uri"),
    code_challenge: str | None = Query(default=None, alias="codeChallenge"),
    oauth_code_challenge: str | None = Query(default=None, alias="code_challenge"),
    code_challenge_method: str | None = Query(default=None, alias="codeChallengeMethod"),
    oauth_code_challenge_method: str | None = Query(default=None, alias="code_challenge_method"),
    scope: str | None = Query(default=None),
    state: str | None = Query(default=None),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            runtime.foundry.auth.osdk_oauth_authorize(
                client_id=_oauth_query(client_id, oauth_client_id, "client_id"),
                redirect_uri=_oauth_query(redirect_uri, oauth_redirect_uri, "redirect_uri"),
                code_challenge=_oauth_query(code_challenge, oauth_code_challenge, "code_challenge"),
                code_challenge_method=_optional_oauth_query(
                    code_challenge_method, oauth_code_challenge_method, "code_challenge_method", "S256"
                ),
                scopes=_scope_query(scope),
                state=state,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/auth/osdk/oauth/token")
async def exchange_osdk_oauth_token(request: Request) -> JsonObject:
    try:
        payload = await _token_request(request)
        if payload.grant_type == "client_credentials":
            return _oauth_token_response(
                cast(
                    JsonObject,
                    runtime.foundry.auth.osdk_oauth_client_credentials(
                        client_id=payload.client_id,
                        client_secret=_required(payload.client_secret, "clientSecret"),
                        scopes=_scope_query(payload.scope),
                        ctx=_ctx(request),
                    ),
                )
            )
        return _oauth_token_response(
            cast(
                JsonObject,
                runtime.foundry.auth.osdk_oauth_token(
                    client_id=payload.client_id,
                    code=_required(payload.code, "code"),
                    redirect_uri=_required(payload.redirect_uri, "redirectUri"),
                    code_verifier=_required(payload.code_verifier, "codeVerifier"),
                    ctx=_ctx(request),
                ),
            )
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


async def _token_request(request: Request) -> OsdkOAuthTokenRequest:
    body = await request.body()
    if len(body) > _MAX_TOKEN_REQUEST_BYTES:
        raise ValidationFailed("OSDK OAuth token request exceeds 16 KiB")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    try:
        if content_type == "application/json":
            raw = json.loads(body)
        elif content_type == "application/x-www-form-urlencoded":
            raw = _single_form_values(parse_qs(body.decode("utf-8"), keep_blank_values=True))
        else:
            raise ValidationFailed("OSDK OAuth token request content type is not supported")
        return OsdkOAuthTokenRequest.model_validate(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
        raise ValidationFailed("OSDK OAuth token request is invalid") from exc


def _single_form_values(values: dict[str, list[str]]) -> dict[str, str]:
    if any(len(items) != 1 for items in values.values()):
        raise ValidationFailed("OSDK OAuth token request contains duplicate parameters")
    return {key: items[0] for key, items in values.items()}


def _oauth_token_response(payload: JsonObject) -> JsonObject:
    response = dict(payload)
    for camel, standard in (
        ("accessToken", "access_token"),
        ("tokenType", "token_type"),
        ("expiresIn", "expires_in"),
        ("refreshToken", "refresh_token"),
        ("refreshExpiresAt", "refresh_expires_at"),
    ):
        if camel in payload:
            response[standard] = payload[camel]
    return response


def _oauth_query(camel: str | None, standard: str | None, field_name: str) -> str:
    if camel and standard and camel != standard:
        raise ValidationFailed(f"OSDK OAuth {field_name} query values conflict")
    return _required(camel or standard, field_name)


def _optional_oauth_query(camel: str | None, standard: str | None, field_name: str, default: str) -> str:
    if camel is None and standard is None:
        return default
    return _oauth_query(camel, standard, field_name)


def _required(value: str | None, field_name: str) -> str:
    if value is None or value == "":
        raise ValidationFailed(f"OSDK OAuth {field_name} is required")
    return value


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
