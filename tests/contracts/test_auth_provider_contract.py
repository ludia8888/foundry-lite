"""Contract tests for the AuthProvider port and its local adapters."""

from __future__ import annotations

import pytest
from foundry_lite.application.ports.auth_provider import AuthProvider, Credentials, Principal
from foundry_lite.domain.context import DEFAULT_ROLES, DEMO_ADMIN_ROLES
from foundry_lite.infrastructure.auth import (
    HEADER_ROLES_KEY,
    HEADER_TENANT_KEY,
    HEADER_USER_KEY,
    DemoAuthProvider,
    HeaderTrustAuthProvider,
)


@pytest.fixture(params=["header_trust", "demo"])
def provider(request: pytest.FixtureRequest) -> AuthProvider:
    if request.param == "header_trust":
        return HeaderTrustAuthProvider()
    return DemoAuthProvider()


def test_auth_provider_protocol_runtime_checkable() -> None:
    assert isinstance(HeaderTrustAuthProvider(), AuthProvider)
    assert isinstance(DemoAuthProvider(), AuthProvider)


def test_auth_provider_anonymous_returns_principal(provider: AuthProvider) -> None:
    principal = provider.anonymous()
    assert principal.tenant_id
    assert principal.actor_user_id
    assert principal.roles


def test_header_trust_authenticate_round_trips_headers() -> None:
    provider = HeaderTrustAuthProvider()
    credentials: Credentials = {
        HEADER_TENANT_KEY: "tenant-A",
        HEADER_USER_KEY: "user-7",
        HEADER_ROLES_KEY: "admin, data_engineer",
    }
    principal = provider.authenticate(credentials)
    assert principal == Principal(
        tenant_id="tenant-A",
        actor_user_id="user-7",
        roles=("admin", "data_engineer"),
    )


def test_header_trust_authenticate_is_case_insensitive() -> None:
    provider = HeaderTrustAuthProvider()
    credentials = {"X-Tenant-ID": "tenant-A", "X-User-ID": "user-7", "X-Roles": "admin"}
    principal = provider.authenticate(credentials)
    assert principal.tenant_id == "tenant-A"
    assert principal.actor_user_id == "user-7"
    assert principal.roles == ("admin",)


def test_header_trust_authenticate_falls_back_to_defaults_on_blank_roles() -> None:
    provider = HeaderTrustAuthProvider()
    principal = provider.authenticate({HEADER_TENANT_KEY: "tenant-A", HEADER_ROLES_KEY: "   "})
    assert principal.roles == DEFAULT_ROLES


def test_header_trust_authenticate_skips_empty_credentials() -> None:
    provider = HeaderTrustAuthProvider()
    principal = provider.authenticate({})
    assert principal == provider.anonymous()


def test_demo_authenticate_ignores_credentials() -> None:
    provider = DemoAuthProvider()
    principal_a = provider.authenticate({"X-Tenant-ID": "ignored", "X-Roles": "viewer"})
    principal_b = provider.anonymous()
    assert principal_a == principal_b
    assert principal_a.actor_user_id == "user-demo-admin"
    assert principal_a.roles == DEMO_ADMIN_ROLES


def test_principal_is_frozen() -> None:
    principal = Principal(tenant_id="t", actor_user_id="u", roles=("admin",))
    with pytest.raises(Exception):  # noqa: B017 - dataclass frozen raises FrozenInstanceError
        principal.tenant_id = "x"  # type: ignore[misc]
