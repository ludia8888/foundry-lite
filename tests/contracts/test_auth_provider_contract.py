"""Contract tests for the AuthProvider port and its local adapters."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from foundry_lite.application.ports.auth_provider import AuthProvider, Credentials, Principal
from foundry_lite.domain.context import DEFAULT_ROLES, DEMO_ADMIN_ROLES
from foundry_lite.domain.errors import PermissionDenied
from foundry_lite.infrastructure.auth import (
    AUTH_PROFILE_ENV,
    HEADER_ROLES_KEY,
    HEADER_TENANT_KEY,
    HEADER_USER_ATTRIBUTES_KEY,
    HEADER_USER_KEY,
    OIDC_ALLOWED_CLIENT_IDS_JSON_ENV,
    OIDC_AUDIENCE_ENV,
    OIDC_CLIENT_ID_CLAIM_ENV,
    OIDC_DISCOVERY_JSON_ENV,
    OIDC_GRANT_TYPE_CLAIM_ENV,
    OIDC_GRANT_TYPE_VALUE_ENV,
    OIDC_HUMAN_GRANT_CLAIM_ENV,
    OIDC_HUMAN_GRANT_VALUE_ENV,
    OIDC_JWKS_JSON_ENV,
    OIDC_REVOKED_JTIS_JSON_ENV,
    OIDC_SERVICE_ACCOUNT_CLAIM_ENV,
    OIDC_SESSION_CLAIM_ENV,
    DemoAuthProvider,
    HeaderTrustAuthProvider,
    JwtOidcAuthConfig,
    JwtOidcAuthProvider,
    LocalOAuthTokenIssuer,
    auth_provider_from_env,
)
from jwt.algorithms import RSAAlgorithm


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


def test_header_trust_authenticate_preserves_verified_user_attributes() -> None:
    principal = HeaderTrustAuthProvider().authenticate(
        {
            HEADER_TENANT_KEY: "tenant-A",
            HEADER_USER_KEY: "user-7",
            HEADER_ROLES_KEY: "ops_manager",
            HEADER_USER_ATTRIBUTES_KEY: json.dumps({"department": "sales", "region": "apac"}),
        }
    )

    assert principal.user_attributes == {"department": "sales", "region": "apac"}


def test_header_trust_authenticate_round_trips_osdk_application_scope_headers() -> None:
    provider = HeaderTrustAuthProvider()

    principal = provider.authenticate(
        {
            HEADER_TENANT_KEY: "tenant-A",
            HEADER_USER_KEY: "user-7",
            HEADER_ROLES_KEY: "admin",
            "X-Foundry-Lite-App-ID": "osdk_app_orders",
            "X-Foundry-Lite-Client-ID": "orders-web-client",
            "X-Foundry-Lite-Scopes": "osdk:object:Order:read, osdk:object:Order:subscribe",
        }
    )

    assert principal.application_id == "osdk_app_orders"
    assert principal.client_id == "orders-web-client"
    assert principal.token_scopes == ("osdk:object:Order:read", "osdk:object:Order:subscribe")


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


def test_jwt_auth_provider_maps_verified_token_to_principal() -> None:
    private_key, jwk = _rsa_key("kid-a")
    provider = _jwt_provider({"keys": [jwk]})
    token = _jwt_token(private_key, "kid-a", roles=("admin", "ops_manager"))

    principal = provider.authenticate({"Authorization": f"Bearer {token}"})

    assert principal == Principal(
        tenant_id="tenant-A",
        actor_user_id="user-oidc",
        roles=("admin", "ops_manager"),
        authorization_server_issuer="https://issuer.example.test",
        is_human_oauth=False,
    )


def test_jwt_auth_provider_extracts_application_claims() -> None:
    private_key, jwk = _rsa_key("kid-osdk")
    provider = _jwt_provider({"keys": [jwk]})
    token = _jwt_token(
        private_key,
        "kid-osdk",
        client_id="orders-web-client",
        extra_claims={
            "osdk_app_id": "osdk_app_orders",
            "scp": ["osdk:object:Order:read", "osdk:object:Order:subscribe"],
        },
    )

    principal = provider.authenticate({"Authorization": f"Bearer {token}"})

    assert principal.application_id == "osdk_app_orders"
    assert principal.client_id == "orders-web-client"
    assert principal.token_scopes == ("osdk:object:Order:read", "osdk:object:Order:subscribe")


def test_jwt_auth_provider_extracts_namespaced_user_attributes() -> None:
    private_key, jwk = _rsa_key("kid-attributes")
    provider = _jwt_provider({"keys": [jwk]})
    token = _jwt_token(
        private_key,
        "kid-attributes",
        extra_claims={"user_attributes": {"department": "sales", "clearance": 3}},
    )

    principal = provider.authenticate({"Authorization": f"Bearer {token}"})

    assert principal.user_attributes == {"department": "sales", "clearance": 3}


def test_jwt_auth_provider_extracts_local_oauth_session_claims(tmp_path) -> None:
    issuer = LocalOAuthTokenIssuer.from_key_path(tmp_path / "oauth-private-key.pem")
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer=issuer.issuer,
            audience=issuer.audience,
            jwks=issuer.public_jwks(),
            grant_type_claim="gty",
            grant_type_value="authorization_code",
        )
    )
    token = issuer.issue_access_token(
        {
            "tenant_id": "tenant-osdk",
            "actor_user_id": "user-osdk",
            "roles": ["admin", "data_engineer"],
            "application_id": "osdk_app_orders",
            "client_id": "orders-web-client",
            "scopes": ["osdk:object:Order:read", "osdk:object:Order:subscribe"],
            "session_id": "session-1",
        },
        ttl_seconds=900,
    )

    principal = provider.authenticate_for_audience(
        {"Authorization": f"Bearer {token['accessToken']}"},
        issuer.audience,
    )

    assert principal.tenant_id == "tenant-osdk"
    assert principal.actor_user_id == "user-osdk"
    assert principal.roles == ("admin", "data_engineer")
    assert principal.application_id == "osdk_app_orders"
    assert principal.client_id == "orders-web-client"
    assert principal.token_scopes == ("osdk:object:Order:read", "osdk:object:Order:subscribe")
    assert principal.oauth_session_hash is not None
    assert "session-1" not in principal.oauth_session_hash
    assert principal.oauth_grant_type == "authorization_code"
    assert principal.oauth_resource == issuer.audience
    assert isinstance(principal.oauth_token_issued_at, int)
    assert isinstance(principal.oauth_token_expires_at, int)


def test_external_oidc_principal_uses_verified_human_grant_and_hashed_session_binding() -> None:
    private_key, jwk = _rsa_key("kid-external-human")
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer="https://issuer.example.test",
            audience="foundry-lite",
            jwks={"keys": [jwk]},
            client_id_claim="azp",
            session_claim="sid",
            oauth_session_authority="issuer",
            human_grant_claim="gty",
            human_grant_value="authorization_code",
            grant_type_claim="gty",
            grant_type_value="authorization_code",
            allowed_client_ids=frozenset({"https://chatgpt.com/oauth/client.json"}),
        )
    )
    token = _jwt_token(
        private_key,
        "kid-external-human",
        extra_claims={
            "azp": "https://chatgpt.com/oauth/client.json",
            "sid": "raw-idp-session-must-not-leak",
            "gty": "authorization_code",
            "scope": "osdk:connector:governed_release:execute",
        },
    )

    principal = provider.authenticate_for_audience(
        {"Authorization": f"Bearer {token}"},
        "foundry-lite",
    )

    assert principal.application_id is None
    assert principal.client_id == "https://chatgpt.com/oauth/client.json"
    assert principal.oauth_session_authority == "issuer"
    assert principal.oauth_session_id is not None
    assert principal.oauth_session_id.startswith("issuer-session:")
    assert "raw-idp-session" not in principal.oauth_session_id
    assert principal.oauth_session_hash is not None
    assert "raw-idp-session" not in principal.oauth_session_hash
    assert principal.authorization_server_issuer == "https://issuer.example.test"
    assert principal.oauth_grant_type == "authorization_code"
    assert principal.oauth_resource == "foundry-lite"
    assert isinstance(principal.oauth_token_issued_at, int)
    assert isinstance(principal.oauth_token_expires_at, int)
    assert principal.is_human_oauth is True


def test_external_oidc_rejects_a_token_from_an_unlisted_oauth_client() -> None:
    private_key, jwk = _rsa_key("kid-external-wrong-client")
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer="https://issuer.example.test",
            audience="foundry-lite",
            jwks={"keys": [jwk]},
            client_id_claim="azp",
            allowed_client_ids=frozenset({"https://chatgpt.com/oauth/client.json"}),
        )
    )
    token = _jwt_token(
        private_key,
        "kid-external-wrong-client",
        extra_claims={"azp": "https://attacker.example.test/oauth/client.json"},
    )

    with pytest.raises(PermissionDenied, match="authentication failed") as error:
        provider.authenticate({"Authorization": f"Bearer {token}"})

    assert error.value.details["reason"] == "client_id_not_allowed"


@pytest.mark.parametrize(
    "extra_claims",
    [
        {"azp": "chatgpt-client", "sid": "session-1", "gty": "client_credentials"},
        {"azp": "chatgpt-client", "gty": "authorization_code"},
    ],
)
def test_external_oidc_principal_fails_human_evidence_closed(extra_claims: dict[str, object]) -> None:
    private_key, jwk = _rsa_key("kid-external-non-human")
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer="https://issuer.example.test",
            audience="foundry-lite",
            jwks={"keys": [jwk]},
            client_id_claim="azp",
            session_claim="sid",
            oauth_session_authority="issuer",
            human_grant_claim="gty",
            human_grant_value="authorization_code",
            grant_type_claim="gty",
            grant_type_value="authorization_code",
        )
    )
    token = _jwt_token(private_key, "kid-external-non-human", extra_claims=extra_claims)

    principal = provider.authenticate({"Authorization": f"Bearer {token}"})

    assert principal.is_human_oauth is False


def test_expired_or_wrong_audience_jwt_is_denied() -> None:
    private_key, jwk = _rsa_key("kid-a")
    provider = _jwt_provider({"keys": [jwk]})

    expired = _jwt_token(private_key, "kid-a", expires_in=timedelta(seconds=-30))
    wrong_audience = _jwt_token(private_key, "kid-a", audience="other-audience")

    with pytest.raises(PermissionDenied, match="authentication failed"):
        provider.authenticate({"Authorization": f"Bearer {expired}"})
    with pytest.raises(PermissionDenied, match="authentication failed"):
        provider.authenticate({"Authorization": f"Bearer {wrong_audience}"})


def test_jwt_auth_provider_requires_time_and_identity_claims() -> None:
    private_key, jwk = _rsa_key("kid-required-claims")
    provider = _jwt_provider({"keys": [jwk]})

    for claim in ("exp", "iat", "iss", "aud"):
        token = _jwt_token(private_key, "kid-required-claims", omitted_claims=frozenset({claim}))

        with pytest.raises(PermissionDenied, match="authentication failed"):
            provider.authenticate({"Authorization": f"Bearer {token}"})


def test_jwks_rotation_keeps_valid_sessions() -> None:
    old_private_key, old_jwk = _rsa_key("kid-old")
    new_private_key, new_jwk = _rsa_key("kid-new")
    jwks_state: dict[str, object] = {"keys": [old_jwk]}
    provider = _jwt_provider(jwks_state, jwks_loader=lambda: jwks_state)

    old_token = _jwt_token(old_private_key, "kid-old")
    new_token = _jwt_token(new_private_key, "kid-new", subject="user-new")

    assert provider.authenticate({"Authorization": f"Bearer {old_token}"}).actor_user_id == "user-oidc"
    jwks_state = {"keys": [new_jwk]}
    assert provider.authenticate({"Authorization": f"Bearer {new_token}"}).actor_user_id == "user-new"
    assert provider.authenticate({"Authorization": f"Bearer {old_token}"}).actor_user_id == "user-oidc"


def test_retired_jwks_key_is_denied_after_grace_period() -> None:
    old_private_key, old_jwk = _rsa_key("kid-old-retired")
    new_private_key, new_jwk = _rsa_key("kid-new-retired")
    jwks_state: dict[str, object] = {"keys": [old_jwk]}
    clock_value = 1_000.0
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer="https://issuer.example.test",
            audience="foundry-lite",
            jwks=jwks_state,
            jwks_refresh_interval_seconds=0,
            retired_key_grace_seconds=10,
        ),
        jwks_loader=lambda: jwks_state,
        clock=lambda: clock_value,
    )
    old_token = _jwt_token(old_private_key, "kid-old-retired", expires_in=timedelta(minutes=10))
    new_token = _jwt_token(new_private_key, "kid-new-retired", subject="user-new")

    assert provider.authenticate({"Authorization": f"Bearer {old_token}"}).actor_user_id == "user-oidc"
    jwks_state = {"keys": [new_jwk]}
    clock_value += 1
    assert provider.authenticate({"Authorization": f"Bearer {new_token}"}).actor_user_id == "user-new"
    assert provider.authenticate({"Authorization": f"Bearer {old_token}"}).actor_user_id == "user-oidc"
    clock_value += 11

    with pytest.raises(PermissionDenied, match="authentication failed"):
        provider.authenticate({"Authorization": f"Bearer {old_token}"})


def test_oidc_profile_loads_discovery_and_jwks_from_env() -> None:
    private_key, jwk = _rsa_key("kid-env")
    env = {
        AUTH_PROFILE_ENV: "oidc",
        OIDC_DISCOVERY_JSON_ENV: json.dumps({"issuer": "https://issuer.example.test"}),
        OIDC_AUDIENCE_ENV: "foundry-lite",
        OIDC_JWKS_JSON_ENV: json.dumps({"keys": [jwk]}),
    }
    provider = auth_provider_from_env(env)
    token = _jwt_token(private_key, "kid-env")

    assert isinstance(provider, JwtOidcAuthProvider)
    assert provider.authenticate({"Authorization": f"Bearer {token}"}).tenant_id == "tenant-A"


def test_oidc_profile_loads_external_mcp_client_session_and_human_grant_claims() -> None:
    _private_key, jwk = _rsa_key("kid-external-env")
    provider = auth_provider_from_env(
        {
            AUTH_PROFILE_ENV: "oidc",
            OIDC_DISCOVERY_JSON_ENV: json.dumps({"issuer": "https://issuer.example.test"}),
            OIDC_AUDIENCE_ENV: "foundry-lite",
            OIDC_JWKS_JSON_ENV: json.dumps({"keys": [jwk]}),
            OIDC_CLIENT_ID_CLAIM_ENV: "azp",
            OIDC_SESSION_CLAIM_ENV: "sid",
            OIDC_HUMAN_GRANT_CLAIM_ENV: "gty",
            OIDC_HUMAN_GRANT_VALUE_ENV: "authorization_code",
            OIDC_GRANT_TYPE_CLAIM_ENV: "gty",
            OIDC_GRANT_TYPE_VALUE_ENV: "authorization_code",
            OIDC_ALLOWED_CLIENT_IDS_JSON_ENV: '["https://chatgpt.com/oauth/client.json"]',
        }
    )

    assert isinstance(provider, JwtOidcAuthProvider)
    assert provider.config.client_id_claim == "azp"
    assert provider.config.session_claim == "sid"
    assert provider.config.oauth_session_authority == "issuer"
    assert provider.config.human_grant_claim == "gty"
    assert provider.config.human_grant_value == "authorization_code"
    assert provider.config.grant_type_claim == "gty"
    assert provider.config.grant_type_value == "authorization_code"
    assert provider.config.allowed_client_ids == frozenset({"https://chatgpt.com/oauth/client.json"})


def test_service_account_is_tenant_scoped() -> None:
    private_key, jwk = _rsa_key("kid-service")
    provider = _jwt_provider({"keys": [jwk]})
    token = _jwt_token(
        private_key,
        "kid-service",
        subject=None,
        client_id="connector-sync",
        roles=("data_engineer",),
    )

    principal = provider.authenticate({"Authorization": f"Bearer {token}", HEADER_TENANT_KEY: "tenant-B"})

    assert principal == Principal(
        tenant_id="tenant-A",
        actor_user_id="service-account:connector-sync",
        roles=("data_engineer",),
        client_id="connector-sync",
        authorization_server_issuer="https://issuer.example.test",
        is_human_oauth=False,
    )

    tenantless = _jwt_token(private_key, "kid-service", subject=None, client_id="connector-sync", tenant_id=None)
    with pytest.raises(PermissionDenied, match="authentication failed"):
        provider.authenticate({"Authorization": f"Bearer {tenantless}"})


def test_service_account_claim_is_configurable_from_env() -> None:
    private_key, jwk = _rsa_key("kid-service-env")
    env = {
        AUTH_PROFILE_ENV: "jwt",
        OIDC_AUDIENCE_ENV: "foundry-lite",
        OIDC_JWKS_JSON_ENV: json.dumps({"keys": [jwk]}),
        OIDC_DISCOVERY_JSON_ENV: json.dumps({"issuer": "https://issuer.example.test"}),
        OIDC_SERVICE_ACCOUNT_CLAIM_ENV: "azp",
    }
    provider = auth_provider_from_env(env)
    token = _jwt_token(private_key, "kid-service-env", subject=None, client_id=None, extra_claims={"azp": "m2m-job"})

    assert provider.authenticate({"Authorization": f"Bearer {token}"}).actor_user_id == "service-account:m2m-job"


def test_session_revocation_policy_denies_revoked_jti() -> None:
    private_key, jwk = _rsa_key("kid-revocation")
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer="https://issuer.example.test",
            audience="foundry-lite",
            jwks={"keys": [jwk]},
            revoked_token_ids=frozenset({"session-revoked"}),
        )
    )

    active = _jwt_token(private_key, "kid-revocation", jwt_id="session-active")
    revoked = _jwt_token(private_key, "kid-revocation", jwt_id="session-revoked")

    assert provider.authenticate({"Authorization": f"Bearer {active}"}).actor_user_id == "user-oidc"
    with pytest.raises(PermissionDenied, match="authentication failed"):
        provider.authenticate({"Authorization": f"Bearer {revoked}"})


def test_revocation_list_loads_from_env() -> None:
    private_key, jwk = _rsa_key("kid-revocation-env")
    env = {
        AUTH_PROFILE_ENV: "oidc",
        OIDC_AUDIENCE_ENV: "foundry-lite",
        OIDC_JWKS_JSON_ENV: json.dumps({"keys": [jwk]}),
        OIDC_DISCOVERY_JSON_ENV: json.dumps({"issuer": "https://issuer.example.test"}),
        OIDC_REVOKED_JTIS_JSON_ENV: json.dumps(["blocked-jti"]),
    }
    provider = auth_provider_from_env(env)
    token = _jwt_token(private_key, "kid-revocation-env", jwt_id="blocked-jti")

    with pytest.raises(PermissionDenied, match="authentication failed"):
        provider.authenticate({"Authorization": f"Bearer {token}"})


def _jwt_provider(
    jwks: dict[str, object],
    *,
    jwks_loader: Callable[[], Mapping[str, object]] | None = None,
) -> JwtOidcAuthProvider:
    return JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer="https://issuer.example.test",
            audience="foundry-lite",
            jwks=jwks,
        ),
        jwks_loader=jwks_loader,
    )


def _rsa_key(kid: str) -> tuple[RSAPrivateKey, dict[str, object]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk["kid"] = kid
    jwk["alg"] = "RS256"
    jwk["use"] = "sig"
    return private_key, jwk


def _jwt_token(
    private_key: RSAPrivateKey,
    kid: str,
    *,
    audience: str = "foundry-lite",
    issuer: str = "https://issuer.example.test",
    subject: str | None = "user-oidc",
    tenant_id: str | None = "tenant-A",
    client_id: str | None = None,
    jwt_id: str | None = None,
    roles: tuple[str, ...] = ("admin",),
    expires_in: timedelta = timedelta(minutes=5),
    extra_claims: Mapping[str, object] | None = None,
    omitted_claims: frozenset[str] = frozenset(),
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "iss": issuer,
        "aud": audience,
        "roles": list(roles),
        "iat": now,
        "exp": now + expires_in,
    }
    if subject is not None:
        payload["sub"] = subject
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id
    if client_id is not None:
        payload["client_id"] = client_id
    if jwt_id is not None:
        payload["jti"] = jwt_id
    if extra_claims is not None:
        payload.update(extra_claims)
    for claim in omitted_claims:
        payload.pop(claim, None)
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})
