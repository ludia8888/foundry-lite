"""Startup guard tests for AuthProvider profile selection."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from foundry_lite.domain.errors import PermissionDenied
from foundry_lite.infrastructure.auth import (
    AUTH_PROFILE_ENV,
    AUTHORIZATION_HEADER,
    OIDC_AUDIENCE_ENV,
    OIDC_DISCOVERY_JSON_ENV,
    OIDC_ISSUER_ENV,
    OIDC_JWKS_JSON_ENV,
    OIDC_REVOKED_JTIS_JSON_ENV,
    RUNTIME_PROFILE_ENV,
    AuthProfileConfigurationError,
    DemoAuthProvider,
    HeaderTrustAuthProvider,
    JwtOidcAuthConfig,
    JwtOidcAuthProvider,
    auth_provider_for_profile,
    auth_provider_from_env,
)
from jwt.algorithms import RSAAlgorithm


def test_default_local_auth_profile_uses_header_trust_provider() -> None:
    provider = auth_provider_from_env({})
    assert isinstance(provider, HeaderTrustAuthProvider)


def test_local_header_trust_profile_is_explicitly_allowed() -> None:
    provider = auth_provider_for_profile("header-trust", runtime_profile="local")
    assert isinstance(provider, HeaderTrustAuthProvider)


def test_demo_auth_profile_is_allowed_for_demo_runtime() -> None:
    provider = auth_provider_for_profile("demo", runtime_profile="demo")
    assert isinstance(provider, DemoAuthProvider)


def test_production_default_header_trust_profile_fails_fast() -> None:
    with pytest.raises(AuthProfileConfigurationError, match="cannot use FOUNDRY_LITE_AUTH_PROFILE=header-trust"):
        auth_provider_for_profile(None, runtime_profile="production")


def test_production_header_trust_profile_fails_fast() -> None:
    env = {
        AUTH_PROFILE_ENV: "local_header_trust",
        RUNTIME_PROFILE_ENV: "production",
    }
    with pytest.raises(AuthProfileConfigurationError, match="production cannot use"):
        auth_provider_from_env(env)


@pytest.mark.parametrize("auth_profile", ["header-trust", "local_header_trust", "demo", "demo_admin"])
def test_production_refuses_dev_header_trust_auth(auth_profile: str) -> None:
    with pytest.raises(AuthProfileConfigurationError, match="production cannot use"):
        auth_provider_from_env(
            {
                AUTH_PROFILE_ENV: auth_profile,
                RUNTIME_PROFILE_ENV: "production",
            }
        )


def test_production_demo_profile_fails_fast() -> None:
    with pytest.raises(AuthProfileConfigurationError, match="cannot use FOUNDRY_LITE_AUTH_PROFILE=demo"):
        auth_provider_for_profile("demo", runtime_profile="prod")


@pytest.mark.parametrize("auth_profile", ["jwt", "oidc"])
def test_strict_auth_profile_requires_oidc_configuration(auth_profile: str) -> None:
    with pytest.raises(AuthProfileConfigurationError, match="JWT/OIDC auth"):
        auth_provider_for_profile(auth_profile, runtime_profile="local")


@pytest.mark.parametrize("auth_profile", ["jwt", "oidc"])
def test_production_strict_auth_profile_builds_jwt_oidc_provider(auth_profile: str) -> None:
    provider = auth_provider_from_env(
        {
            AUTH_PROFILE_ENV: auth_profile,
            RUNTIME_PROFILE_ENV: "production",
            OIDC_ISSUER_ENV: "https://issuer.example.test",
            OIDC_AUDIENCE_ENV: "foundry-lite",
            OIDC_JWKS_JSON_ENV: '{"keys":[]}',
        }
    )

    assert isinstance(provider, JwtOidcAuthProvider)


def test_unknown_auth_profile_fails_fast() -> None:
    with pytest.raises(AuthProfileConfigurationError, match="unknown auth profile"):
        auth_provider_for_profile("mystery", runtime_profile="local")


def test_unknown_runtime_profile_fails_fast() -> None:
    with pytest.raises(AuthProfileConfigurationError, match="unknown FOUNDRY_LITE_RUNTIME_PROFILE"):
        auth_provider_for_profile("header-trust", runtime_profile="cluster")


def test_staging_refuses_dev_header_trust_auth() -> None:
    with pytest.raises(AuthProfileConfigurationError, match="staging cannot use"):
        auth_provider_from_env(
            {
                AUTH_PROFILE_ENV: "header-trust",
                RUNTIME_PROFILE_ENV: "staging",
            }
        )


def test_jwt_oidc_authenticates_human_bearer_token() -> None:
    private_key, jwks = _jwt_key_material("kid-human")
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer="https://issuer.example.test",
            audience="foundry-lite",
            jwks=jwks,
        )
    )
    token = _jwt_token(
        private_key,
        "kid-human",
        sub="user-123",
        tenant_id="tenant-demo",
        roles="ops_manager,data_engineer",
    )

    principal = provider.authenticate({AUTHORIZATION_HEADER.upper(): f"Bearer {token}"})

    assert principal.tenant_id == "tenant-demo"
    assert principal.actor_user_id == "user-123"
    assert principal.roles == ("ops_manager", "data_engineer")
    assert provider.failure_contract().adapter_profile == "jwt-oidc-auth"


def test_jwt_oidc_refreshes_jwks_and_authenticates_service_account() -> None:
    private_key, jwks = _jwt_key_material("kid-service")
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer="https://issuer.example.test",
            audience="foundry-lite",
            jwks={"keys": []},
        ),
        jwks_loader=lambda: jwks,
    )
    token = _jwt_token(
        private_key,
        "kid-service",
        tenant_id="tenant-demo",
        client_id="sync-worker",
        roles=["service", "", "data_engineer"],
    )

    principal = provider.authenticate({AUTHORIZATION_HEADER: f"Bearer {token}"})

    assert principal.actor_user_id == "service-account:sync-worker"
    assert principal.roles == ("service", "data_engineer")


def test_jwt_oidc_rejects_revoked_and_missing_tokens() -> None:
    private_key, jwks = _jwt_key_material("kid-revoked")
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer="https://issuer.example.test",
            audience="foundry-lite",
            jwks=jwks,
            revoked_token_ids=frozenset({"revoked-1"}),
        )
    )
    token = _jwt_token(
        private_key,
        "kid-revoked",
        sub="user-123",
        tenant_id="tenant-demo",
        roles=["ops_manager"],
        jti="revoked-1",
    )

    with pytest.raises(PermissionDenied, match="authentication failed"):
        provider.authenticate({AUTHORIZATION_HEADER: f"Bearer {token}"})
    with pytest.raises(PermissionDenied, match="authentication failed"):
        provider.authenticate({})
    with pytest.raises(PermissionDenied, match="authentication failed"):
        provider.anonymous()


def test_jwt_oidc_audience_bound_auth_requires_one_exact_resource() -> None:
    private_key, jwks = _jwt_key_material("kid-exact-audience")
    resource = "https://foundry.example.test/mcp/ontology/app-a"
    other_resource = "https://foundry.example.test/mcp/builder/app-a"
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer="https://issuer.example.test",
            audience="foundry-lite",
            jwks=jwks,
        )
    )
    claims = {
        "sub": "user-123",
        "tenant_id": "tenant-demo",
        "roles": ["ops_manager"],
    }
    exact = _jwt_token(private_key, "kid-exact-audience", aud=resource, **claims)
    multiple = _jwt_token(
        private_key,
        "kid-exact-audience",
        aud=[resource, other_resource],
        **claims,
    )

    principal = provider.authenticate_for_audience({AUTHORIZATION_HEADER: f"Bearer {exact}"}, resource)

    assert principal.actor_user_id == "user-123"
    for audience in (resource, other_resource):
        with pytest.raises(PermissionDenied, match="authentication failed"):
            provider.authenticate_for_audience({AUTHORIZATION_HEADER: f"Bearer {multiple}"}, audience)


def test_jwt_oidc_provider_from_env_uses_discovery_and_revoked_ids() -> None:
    _, jwks = _jwt_key_material("kid-env")

    provider = auth_provider_from_env(
        {
            AUTH_PROFILE_ENV: "jwt",
            RUNTIME_PROFILE_ENV: "production",
            OIDC_DISCOVERY_JSON_ENV: '{"issuer":"https://issuer.example.test"}',
            OIDC_AUDIENCE_ENV: "foundry-lite",
            OIDC_JWKS_JSON_ENV: _json(jwks),
            OIDC_REVOKED_JTIS_JSON_ENV: '["old-token"]',
        }
    )

    assert isinstance(provider, JwtOidcAuthProvider)
    assert provider.config.issuer == "https://issuer.example.test"
    assert provider.config.revoked_token_ids == frozenset({"old-token"})


def test_jwt_oidc_rejects_unknown_keys_bad_headers_algorithms_and_claims() -> None:
    private_key, jwks = _jwt_key_material("kid-valid")
    empty_provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer="https://issuer.example.test",
            audience="foundry-lite",
            jwks={"keys": []},
        )
    )
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer="https://issuer.example.test",
            audience="foundry-lite",
            jwks=jwks,
        )
    )
    algorithm_guard = JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer="https://issuer.example.test",
            audience="foundry-lite",
            jwks=jwks,
            algorithm="ES256",
        )
    )

    with pytest.raises(PermissionDenied, match="authentication failed"):
        empty_provider.authenticate(
            {
                AUTHORIZATION_HEADER: "Bearer "
                + _jwt_token(private_key, "kid-missing", sub="user-123", tenant_id="tenant-demo", roles=["ops"])
            }
        )
    with pytest.raises(PermissionDenied, match="authentication failed"):
        provider.authenticate({AUTHORIZATION_HEADER: "Bearer not-a-jwt"})
    with pytest.raises(PermissionDenied, match="authentication failed"):
        algorithm_guard.authenticate(
            {
                AUTHORIZATION_HEADER: "Bearer "
                + _jwt_token(private_key, "kid-valid", sub="user-123", tenant_id="tenant-demo", roles=["ops"])
            }
        )
    for claims in (
        {"sub": "user-123", "roles": ["ops"]},
        {"tenant_id": "tenant-demo", "roles": ["ops"]},
        {"sub": "user-123", "tenant_id": "tenant-demo"},
    ):
        with pytest.raises(PermissionDenied, match="authentication failed"):
            provider.authenticate({AUTHORIZATION_HEADER: "Bearer " + _jwt_token(private_key, "kid-valid", **claims)})


def test_jwt_oidc_configuration_errors_fail_before_startup() -> None:
    _, jwks = _jwt_key_material("kid-config")
    base_env = {
        AUTH_PROFILE_ENV: "jwt",
        RUNTIME_PROFILE_ENV: "production",
        OIDC_ISSUER_ENV: "https://issuer.example.test",
        OIDC_AUDIENCE_ENV: "foundry-lite",
        OIDC_JWKS_JSON_ENV: _json(jwks),
    }

    for env, message in (
        ({**base_env, OIDC_ISSUER_ENV: ""}, "issuer"),
        ({**base_env, OIDC_AUDIENCE_ENV: ""}, OIDC_AUDIENCE_ENV),
        ({**base_env, OIDC_JWKS_JSON_ENV: "[1]"}, "JSON object"),
        ({**base_env, OIDC_REVOKED_JTIS_JSON_ENV: "{}"}, "JSON array"),
        ({**base_env, OIDC_REVOKED_JTIS_JSON_ENV: '[""]'}, "non-empty strings"),
    ):
        with pytest.raises(AuthProfileConfigurationError, match=message):
            auth_provider_from_env(env)

    with pytest.raises(ValueError, match="JWK is missing kid"):
        JwtOidcAuthProvider(
            JwtOidcAuthConfig(
                issuer="https://issuer.example.test",
                audience="foundry-lite",
                jwks={"keys": [{}]},
            )
        )


def _jwt_key_material(kid: str) -> tuple[Any, dict[str, object]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    jwk["kid"] = kid
    jwk["alg"] = "RS256"
    jwk["use"] = "sig"
    return private_key, {"keys": [jwk]}


def _jwt_token(private_key: Any, kid: str, **claims: object) -> str:
    now = datetime.now(UTC)
    payload = {
        "iss": "https://issuer.example.test",
        "aud": "foundry-lite",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        **claims,
    }
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


def _json(payload: dict[str, object]) -> str:
    return json.dumps(payload)
