from __future__ import annotations

import pytest

from scripts.operations.private_browser_keycloak import client_payload


def test_private_browser_uses_exact_redirect_pkce_and_no_qa_admin_scope() -> None:
    client = client_payload("browser", "private-app", "tenant", "https://app.example.test", "api-audience")
    assert client["redirectUris"] == ["https://app.example.test/auth/callback"]
    assert client["webOrigins"] == ["https://app.example.test"]
    assert client["defaultClientScopes"] == ["profile", "email"]
    assert client["optionalClientScopes"] == []
    assert client["fullScopeAllowed"] is False
    assert client["implicitFlowEnabled"] is False
    assert client["directAccessGrantsEnabled"] is False
    assert client["serviceAccountsEnabled"] is False
    assert client["attributes"]["pkce.code.challenge.method"] == "S256"
    claims = {item["config"].get("claim.name"): item["config"].get("claim.value") for item in client["protocolMappers"]}
    assert claims["roles"] == '["viewer"]'
    assert claims["tenant_id"] == "tenant"
    assert claims["osdk_app_id"] == "private-app"
    assert "admin" not in str(client)
    assert "foundry-lite-runtime" not in str(client)


@pytest.mark.parametrize(
    "origin", ["http://app.example.test", "https://app.example.test/wildcard", "https://user@app.example.test"]
)
def test_private_browser_rejects_unsafe_redirect_origin(origin: str) -> None:
    with pytest.raises(ValueError):
        client_payload("browser", "private-app", "tenant", origin, "api-audience")
