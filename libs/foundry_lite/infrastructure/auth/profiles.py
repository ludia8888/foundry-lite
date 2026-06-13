"""AuthProvider profile selection for application composition roots."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Final

from foundry_lite.application.ports.auth_provider import AuthProvider
from foundry_lite.infrastructure.auth.local import DemoAuthProvider, HeaderTrustAuthProvider

__all__ = [
    "AUTH_PROFILE_ENV",
    "DEFAULT_AUTH_PROFILE",
    "DEFAULT_RUNTIME_PROFILE",
    "RUNTIME_PROFILE_ENV",
    "AuthProfileConfigurationError",
    "auth_provider_for_profile",
    "auth_provider_from_env",
]

AUTH_PROFILE_ENV: Final = "FOUNDRY_LITE_AUTH_PROFILE"
RUNTIME_PROFILE_ENV: Final = "FOUNDRY_LITE_RUNTIME_PROFILE"
DEFAULT_AUTH_PROFILE: Final = "header-trust"
DEFAULT_RUNTIME_PROFILE: Final = "local"

_HEADER_TRUST_AUTH_PROFILES: Final = frozenset({"header-trust", "local-header-trust"})
_DEMO_AUTH_PROFILES: Final = frozenset({"demo", "demo-admin"})
_STRICT_AUTH_PROFILES: Final = frozenset({"jwt", "oidc"})
_PRODUCTION_RUNTIME_PROFILES: Final = frozenset({"production", "prod"})


class AuthProfileConfigurationError(RuntimeError):
    """Raised when a composition root selects an unsafe auth profile."""


def auth_provider_from_env(environ: Mapping[str, str] | None = None) -> AuthProvider:
    """Build the configured AuthProvider from environment variables."""

    source = os.environ if environ is None else environ
    return auth_provider_for_profile(
        auth_profile=source.get(AUTH_PROFILE_ENV),
        runtime_profile=source.get(RUNTIME_PROFILE_ENV),
    )


def auth_provider_for_profile(auth_profile: str | None, runtime_profile: str | None) -> AuthProvider:
    """Build an AuthProvider, failing before startup for unsafe production choices."""

    resolved_auth_profile = _normalise_profile(auth_profile, DEFAULT_AUTH_PROFILE)
    resolved_runtime_profile = _normalise_profile(runtime_profile, DEFAULT_RUNTIME_PROFILE)
    _reject_unsafe_production_profile(resolved_auth_profile, resolved_runtime_profile)
    if resolved_auth_profile in _HEADER_TRUST_AUTH_PROFILES:
        return HeaderTrustAuthProvider()
    if resolved_auth_profile in _DEMO_AUTH_PROFILES:
        return DemoAuthProvider()
    if resolved_auth_profile in _STRICT_AUTH_PROFILES:
        raise AuthProfileConfigurationError(
            f"{AUTH_PROFILE_ENV}={resolved_auth_profile} is not implemented yet; "
            "configure a real JWT/OIDC adapter before using this profile"
        )
    raise AuthProfileConfigurationError(f"unknown auth profile: {resolved_auth_profile}")


def _normalise_profile(value: str | None, default: str) -> str:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower().replace("_", "-")


def _reject_unsafe_production_profile(auth_profile: str, runtime_profile: str) -> None:
    if runtime_profile not in _PRODUCTION_RUNTIME_PROFILES:
        return
    if auth_profile in _HEADER_TRUST_AUTH_PROFILES:
        raise AuthProfileConfigurationError(
            f"{RUNTIME_PROFILE_ENV}=production cannot use {AUTH_PROFILE_ENV}=header-trust; "
            "choose a real JWT/OIDC auth profile before production startup"
        )
    if auth_profile in _DEMO_AUTH_PROFILES:
        raise AuthProfileConfigurationError(
            f"{RUNTIME_PROFILE_ENV}=production cannot use {AUTH_PROFILE_ENV}=demo; "
            "demo identity is allowed only for local/demo execution"
        )
