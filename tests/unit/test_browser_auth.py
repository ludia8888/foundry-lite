from __future__ import annotations

from dataclasses import replace

import pytest
from foundry_lite.application.ports.auth_provider import Principal
from foundry_lite.domain.errors import PermissionDenied
from foundry_lite.infrastructure.auth import (
    AuthProfileConfigurationError,
    HeaderTrustAuthProvider,
    JwtOidcAuthConfig,
    JwtOidcAuthProvider,
)
from foundry_lite_api.browser_auth import browser_auth_from_env


def _provider() -> JwtOidcAuthProvider:
    return JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer="https://identity.example.test/realms/private",
            audience="api-audience",
            jwks={"keys": []},
            allowed_client_ids=frozenset({"private-browser", "existing-mcp"}),
        )
    )


def _env() -> dict[str, str]:
    return {
        "FOUNDRY_LITE_BROWSER_CLIENT_ID": "private-browser",
        "FOUNDRY_LITE_BROWSER_PUBLIC_BASE_URL": "https://app.example.test",
        "FOUNDRY_LITE_BROWSER_APPLICATION_ID": "private-app",
        "FOUNDRY_LITE_BROWSER_OWNER_SUBJECT": "owner-subject",
    }


def test_strict_api_without_browser_configuration_never_falls_back_to_demo() -> None:
    assert browser_auth_from_env({}, _provider()).public_configuration()["mode"] == "unavailable"
    assert browser_auth_from_env({}, HeaderTrustAuthProvider()).public_configuration()["mode"] == "local"


def test_browser_configuration_exposes_only_public_login_coordinates() -> None:
    config = browser_auth_from_env(_env(), _provider())
    assert config.public_configuration() == {
        "mode": "keycloak",
        "issuer": "https://identity.example.test/realms/private",
        "clientId": "private-browser",
        "redirectUri": "https://app.example.test/auth/callback",
        "applicationId": "private-app",
    }
    assert "owner-subject" not in str(config.public_configuration())


@pytest.mark.parametrize("setting", list(_env()))
def test_partial_browser_configuration_fails_closed(setting: str) -> None:
    env = _env()
    del env[setting]
    with pytest.raises(AuthProfileConfigurationError):
        browser_auth_from_env(env, _provider())


@pytest.mark.parametrize(
    "origin",
    [
        "http://app.example.test",
        "https://evil@app.example.test",
        "https://app.example.test/redirect",
        "https://app.example.test?next=evil",
    ],
)
def test_browser_rejects_unsafe_redirect_origins(origin: str) -> None:
    with pytest.raises(AuthProfileConfigurationError):
        browser_auth_from_env({**_env(), "FOUNDRY_LITE_BROWSER_PUBLIC_BASE_URL": origin}, _provider())


def test_browser_requires_real_oidc_and_an_allowlisted_client() -> None:
    with pytest.raises(AuthProfileConfigurationError):
        browser_auth_from_env(_env(), HeaderTrustAuthProvider())
    with pytest.raises(AuthProfileConfigurationError):
        browser_auth_from_env({**_env(), "FOUNDRY_LITE_BROWSER_CLIENT_ID": "unlisted"}, _provider())


def test_private_browser_principal_is_bound_to_one_owner_and_application() -> None:
    config = browser_auth_from_env(_env(), _provider())
    owner = Principal(
        tenant_id="tenant",
        actor_user_id="owner-subject",
        roles=("viewer",),
        client_id="private-browser",
        application_id="private-app",
        is_human_oauth=True,
        oauth_grant_type="authorization_code",
    )
    config.require_browser_principal(owner)
    for principal in [
        replace(owner, actor_user_id="other"),
        replace(owner, application_id="other"),
        replace(owner, client_id="existing-mcp"),
        replace(owner, is_human_oauth=False),
        replace(owner, roles=("admin",)),
        replace(owner, roles=("viewer", "admin")),
    ]:
        with pytest.raises(PermissionDenied):
            config.require_browser_principal(principal)
    # Existing MCP identities retain their own authentication path, without gaining browser access.
    config.check_principal(replace(owner, actor_user_id="reviewer", client_id="existing-mcp"))
    with pytest.raises(PermissionDenied):
        config.check_principal(replace(owner, actor_user_id="reviewer"))
    config.check_request(owner, "/api/aip/pilot/operating-applications/private-app", "GET")
    config.check_request(owner, "/api/aip/pilot/operating-applications/private-app/objects/Patient/query", "POST")
    config.check_request(owner, "/api/aip/pilot/operating-applications/private-app/actions/CheckIn/runs", "POST")
    for path, method in [
        ("/api/datasets", "GET"),
        ("/api/projects", "POST"),
        ("/api/aip/pilot/operating-applications/other-app", "GET"),
        ("/api/aip/pilot/operating-applications/private-app/objects/Patient/query", "DELETE"),
        ("/mcp/release/release-app", "POST"),
        ("/api/ontology/catalog", "WEBSOCKET"),
    ]:
        with pytest.raises(PermissionDenied):
            config.check_request(owner, path, method)
