"""Startup guard tests for AuthProvider profile selection."""

from __future__ import annotations

import pytest
from foundry_lite.infrastructure.auth import (
    AUTH_PROFILE_ENV,
    RUNTIME_PROFILE_ENV,
    AuthProfileConfigurationError,
    DemoAuthProvider,
    HeaderTrustAuthProvider,
    auth_provider_for_profile,
    auth_provider_from_env,
)


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


def test_production_demo_profile_fails_fast() -> None:
    with pytest.raises(AuthProfileConfigurationError, match="cannot use FOUNDRY_LITE_AUTH_PROFILE=demo"):
        auth_provider_for_profile("demo", runtime_profile="prod")


@pytest.mark.parametrize("auth_profile", ["jwt", "oidc"])
def test_strict_auth_profile_requires_real_adapter(auth_profile: str) -> None:
    with pytest.raises(AuthProfileConfigurationError, match="is not implemented yet"):
        auth_provider_for_profile(auth_profile, runtime_profile="local")


def test_unknown_auth_profile_fails_fast() -> None:
    with pytest.raises(AuthProfileConfigurationError, match="unknown auth profile"):
        auth_provider_for_profile("mystery", runtime_profile="local")
