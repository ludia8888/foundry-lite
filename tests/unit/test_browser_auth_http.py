from __future__ import annotations

import json
import time
from dataclasses import replace
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
from foundry_lite.domain.errors import PermissionDenied
from foundry_lite.infrastructure.auth import JwtOidcAuthConfig, JwtOidcAuthProvider
from foundry_lite_api import runtime
from foundry_lite_api.browser_auth import BrowserAuthConfig
from foundry_lite_api.request_context import _ctx, _websocket_ctx
from foundry_lite_api.routers.auth import router
from jwt.algorithms import RSAAlgorithm


@pytest.fixture
def browser_client(monkeypatch: pytest.MonkeyPatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    public_key["kid"] = "browser-test-key"
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer="https://id.example.test/realms/private",
            audience="api",
            jwks={"keys": [public_key]},
            allowed_client_ids=frozenset({"browser", "existing-mcp"}),
            client_id_claim="azp",
            session_claim="sid",
            grant_type_claim="gty",
            grant_type_value="authorization_code",
            oauth_session_authority="issuer",
            human_grant_claim="human_grant",
            human_grant_value="true",
        )
    )
    config = BrowserAuthConfig(
        mode="keycloak",
        issuer=provider.config.issuer,
        client_id="browser",
        public_base_url="https://app.example.test",
        application_id="private-app",
        owner_subject="owner",
    )
    monkeypatch.setattr(runtime, "get_auth_provider", lambda: provider)
    monkeypatch.setattr(runtime, "get_browser_auth_config", lambda: config)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client, key, provider


def _token(key, **overrides: object) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://id.example.test/realms/private",
        "aud": "api",
        "sub": "owner",
        "tenant_id": "private-tenant",
        "roles": ["viewer"],
        "azp": "browser",
        "osdk_app_id": "private-app",
        "sid": "login-session",
        "gty": "authorization_code",
        "human_grant": "true",
        "iat": now,
        "exp": now + 300,
        **overrides,
    }
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "browser-test-key"})


def test_browser_config_is_public_but_contains_no_owner_or_credentials(browser_client) -> None:
    client, _, _ = browser_client
    response = client.get("/api/auth/browser/config")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["mode"] == "keycloak"
    assert "owner" not in response.text


def test_browser_session_uses_signed_identity_not_demo_headers(browser_client) -> None:
    client, key, _ = browser_client
    response = client.get(
        "/api/auth/browser/session",
        headers={
            "Authorization": f"Bearer {_token(key)}",
            "X-User-ID": "admin",
            "X-Tenant-ID": "other",
            "X-Roles": "admin",
            "X-Foundry-Lite-App-ID": "other-app",
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["userId"] == "owner"
    assert response.json()["tenantId"] == "private-tenant"
    assert response.json()["roles"] == ["viewer"]
    assert "Bearer" not in response.text


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": "other"},
        {"osdk_app_id": "other"},
        {"aud": "other"},
        {"azp": "other"},
        {"exp": 1},
        {"gty": "client_credentials"},
        {"roles": ["admin", "viewer"]},
    ],
)
def test_other_accounts_apps_clients_and_expired_sessions_are_rejected(browser_client, claims) -> None:
    client, key, _ = browser_client
    response = client.get("/api/auth/browser/session", headers={"Authorization": f"Bearer {_token(key, **claims)}"})
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert "private-tenant" not in response.text


def test_anonymous_browser_cannot_use_a_demo_header_as_login(browser_client) -> None:
    client, _, _ = browser_client
    assert (
        client.get("/api/auth/browser/session", headers={"X-User-ID": "owner", "X-Roles": "admin"}).status_code == 403
    )


def test_removed_browser_owner_is_denied_without_waiting_for_token_expiry(browser_client, monkeypatch) -> None:
    client, key, _ = browser_client
    old = runtime.get_browser_auth_config()
    monkeypatch.setattr(runtime, "get_browser_auth_config", lambda: replace(old, owner_subject="new-owner"))
    assert (
        client.get("/api/auth/browser/session", headers={"Authorization": f"Bearer {_token(key)}"}).status_code == 403
    )


@pytest.mark.parametrize(
    "path",
    ["/api/datasets", "/api/projects", "/mcp/release/private-app", "/api/aip/pilot/operating-applications/other"],
)
def test_private_browser_request_context_blocks_non_app_surfaces(browser_client, path) -> None:
    _, key, _ = browser_client
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [(b"authorization", f"Bearer {_token(key)}".encode())],
        }
    )
    with pytest.raises(PermissionDenied, match="배정된 업무 앱"):
        _ctx(request)


def test_private_browser_context_requires_owner_on_every_app_request(browser_client) -> None:
    _, key, _ = browser_client
    path = "/api/aip/pilot/operating-applications/private-app"
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [(b"authorization", f"Bearer {_token(key)}".encode())],
        }
    )
    assert _ctx(request).actor_user_id == "owner"
    rejected = Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [(b"authorization", f"Bearer {_token(key, sub='other')}".encode())],
        }
    )
    with pytest.raises(PermissionDenied):
        _ctx(rejected)
    websocket = WebSocket(
        {
            "type": "websocket",
            "path": "/api/objects/Patient/subscribe",
            "headers": [(b"authorization", f"Bearer {_token(key)}".encode())],
        },
        receive=AsyncMock(),
        send=AsyncMock(),
    )
    with pytest.raises(PermissionDenied):
        _websocket_ctx(websocket)


def test_invited_owner_cannot_borrow_existing_operator_client_authority(browser_client) -> None:
    _, key, _ = browser_client

    def request_for(subject):
        token = _token(key, sub=subject, azp="existing-mcp", roles=["admin"], osdk_app_id="release-app")
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/datasets",
                "headers": [(b"authorization", f"Bearer {token}".encode())],
            }
        )

    with pytest.raises(PermissionDenied):
        _ctx(request_for("owner"))
    # This change restricts only the invited consumer, not existing operators.
    assert _ctx(request_for("existing-reviewer")).actor_user_id == "existing-reviewer"
