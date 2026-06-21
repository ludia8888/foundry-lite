"""AuthProvider profile selection for application composition roots."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Final

from foundry_lite.application.ports.auth_provider import AuthProvider
from foundry_lite.infrastructure.auth.local import DemoAuthProvider, HeaderTrustAuthProvider
from foundry_lite.infrastructure.auth.oidc import JwtOidcAuthProvider, jwt_oidc_auth_provider_from_env

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
_LOCAL_RUNTIME_PROFILES: Final = frozenset({"local", "dev", "development", "demo", "test"})
_PROTECTED_RUNTIME_PROFILES: Final = frozenset({"production", "prod", "staging", "stage"})
_KNOWN_RUNTIME_PROFILES: Final = _LOCAL_RUNTIME_PROFILES | _PROTECTED_RUNTIME_PROFILES


class AuthProfileConfigurationError(RuntimeError):
    """Raised when a composition root selects an unsafe auth profile."""


def auth_provider_from_env(environ: Mapping[str, str] | None = None) -> AuthProvider:
    """Build the configured AuthProvider from environment variables."""

    source = os.environ if environ is None else environ
    return auth_provider_for_profile(
        auth_profile=source.get(AUTH_PROFILE_ENV),
        runtime_profile=source.get(RUNTIME_PROFILE_ENV),
        environ=source,
    )


def auth_provider_for_profile(
    auth_profile: str | None,
    runtime_profile: str | None,
    environ: Mapping[str, str] | None = None,
) -> AuthProvider:
    """Build an AuthProvider, failing before startup for unsafe production choices."""

    resolved_auth_profile = _normalise_profile(auth_profile, DEFAULT_AUTH_PROFILE)
    resolved_runtime_profile = _normalise_profile(runtime_profile, DEFAULT_RUNTIME_PROFILE)
    _reject_unknown_runtime_profile(resolved_runtime_profile)
    _reject_unsafe_protected_runtime_profile(resolved_auth_profile, resolved_runtime_profile)
    if resolved_auth_profile in _HEADER_TRUST_AUTH_PROFILES:
        return HeaderTrustAuthProvider()
    if resolved_auth_profile in _DEMO_AUTH_PROFILES:
        return DemoAuthProvider()
    if resolved_auth_profile in _STRICT_AUTH_PROFILES:
        return _strict_auth_provider_from_env(environ)
    raise AuthProfileConfigurationError(f"unknown auth profile: {resolved_auth_profile}")


def _strict_auth_provider_from_env(environ: Mapping[str, str] | None) -> JwtOidcAuthProvider:
    try:
        return jwt_oidc_auth_provider_from_env(environ)
    except (json.JSONDecodeError, ValueError) as exc:
        raise AuthProfileConfigurationError(f"JWT/OIDC auth configuration error: {exc}") from exc


def _normalise_profile(value: str | None, default: str) -> str:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower().replace("_", "-")


def _reject_unknown_runtime_profile(runtime_profile: str) -> None:
    if runtime_profile in _KNOWN_RUNTIME_PROFILES:
        return
    raise AuthProfileConfigurationError(
        f"unknown {RUNTIME_PROFILE_ENV}: {runtime_profile}; choose local, demo, test, staging, or production"
    )


def _reject_unsafe_protected_runtime_profile(auth_profile: str, runtime_profile: str) -> None:
    if runtime_profile not in _PROTECTED_RUNTIME_PROFILES:
        return
    if auth_profile in _HEADER_TRUST_AUTH_PROFILES:
        raise AuthProfileConfigurationError(
            f"{RUNTIME_PROFILE_ENV}={runtime_profile} cannot use {AUTH_PROFILE_ENV}=header-trust; "
            "choose a real JWT/OIDC auth profile before protected runtime startup"
        )
    if auth_profile in _DEMO_AUTH_PROFILES:
        raise AuthProfileConfigurationError(
            f"{RUNTIME_PROFILE_ENV}={runtime_profile} cannot use {AUTH_PROFILE_ENV}=demo; "
            "demo identity is allowed only for local/demo execution"
        )
