"""Immutable startup configuration for public MCP OAuth discovery."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit

from foundry_lite.application.ports.auth_provider import AuthProvider
from foundry_lite.application.ports.governed_release_live_attestation_repository import (
    GovernedReleaseMcpAuthority,
)
from foundry_lite.application.runtime_profile import RuntimeProfile
from foundry_lite.infrastructure.auth import (
    OAUTH_ISSUER_ENV,
    AuthProfileConfigurationError,
    JwtOidcAuthProvider,
)

MCP_AUTHORIZATION_SERVER_ENV = "FOUNDRY_LITE_MCP_AUTHORIZATION_SERVER"
MCP_PUBLIC_BASE_URL_ENV = "FOUNDRY_LITE_MCP_PUBLIC_BASE_URL"
GOVERNED_RELEASE_APPLICATION_ID_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_APPLICATION_ID"
DYNAMIC_CLIENT_APPLICATION_ID_ENV = "FOUNDRY_LITE_MCP_DYNAMIC_CLIENT_APPLICATION_ID"
LOCAL_CONSENT_ROLES_ENV = "FOUNDRY_LITE_MCP_LOCAL_CONSENT_ROLES"
_APPLICATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ROLE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True)
class McpAuthorizationConfig:
    """Public OAuth issuer and canonical MCP origin selected at startup."""

    external_authorization_server: str | None = None
    public_base_url: str | None = None
    governed_release_application_id: str | None = None
    dynamic_client_application_id: str | None = None
    local_consent_roles: tuple[str, ...] = ()

    def authorization_servers(self, local_authorization_server: str) -> tuple[str, ...]:
        return (self.external_authorization_server or local_authorization_server,)

    def canonical_base_url(self, request_base_url: str) -> str:
        return self.public_base_url or request_base_url.rstrip("/")

    def allows_release_application(self, application_id: str) -> bool:
        if self.external_authorization_server is None:
            return True
        return self.governed_release_application_id == application_id

    def dynamic_registration_application_id(self) -> str | None:
        """Local dynamic client registration is off whenever an external IdP owns the clients."""

        if self.external_authorization_server is not None:
            return None
        return self.dynamic_client_application_id

    def consent_roles(self) -> tuple[str, ...]:
        """Roles to stamp on a browser OAuth consent that carries no trusted identity.

        A browser cannot send the `X-Roles` header the local header-trust profile reads, so an
        interactive consent would otherwise mint a `viewer`-only token and every Builder or
        Release tool would fail its permission check. This is a local QA affordance, not an
        identity: an external IdP always wins, and a protected runtime refuses to start with it.
        """

        if self.external_authorization_server is not None:
            return ()
        return self.local_consent_roles


def mcp_authorization_config_from_env(
    source: Mapping[str, str],
    auth_provider: AuthProvider,
) -> McpAuthorizationConfig:
    """Resolve and cross-check external MCP OAuth settings before serving."""

    public_base_url = _optional_https_url(source.get(MCP_PUBLIC_BASE_URL_ENV), is_origin=True)
    authorization_server = _optional_https_url(source.get(MCP_AUTHORIZATION_SERVER_ENV), is_origin=False)
    application_id = _optional_application_id(source.get(GOVERNED_RELEASE_APPLICATION_ID_ENV))
    dynamic_client_application_id = _optional_dynamic_client_application_id(source)
    local_consent_roles = _local_consent_roles(source)
    if authorization_server is None:
        if RuntimeProfile.from_value(source.get("FOUNDRY_LITE_RUNTIME_PROFILE")).is_protected:
            raise AuthProfileConfigurationError(
                f"{MCP_AUTHORIZATION_SERVER_ENV} is required for protected hosted MCP runtime"
            )
        _require_local_public_oauth_issuer(source, public_base_url)
        return McpAuthorizationConfig(
            public_base_url=public_base_url,
            governed_release_application_id=application_id,
            dynamic_client_application_id=dynamic_client_application_id,
            local_consent_roles=local_consent_roles,
        )
    _require_external_oidc_provider(auth_provider, authorization_server)
    if public_base_url is None:
        raise AuthProfileConfigurationError(
            f"{MCP_PUBLIC_BASE_URL_ENV} is required when {MCP_AUTHORIZATION_SERVER_ENV} is configured"
        )
    return McpAuthorizationConfig(
        external_authorization_server=authorization_server,
        public_base_url=public_base_url,
        governed_release_application_id=application_id,
    )


def governed_release_mcp_authority(
    config: McpAuthorizationConfig,
    auth_provider: AuthProvider,
) -> GovernedReleaseMcpAuthority:
    """Project validated startup OAuth settings into the release trust boundary."""

    if (
        config.external_authorization_server is None
        or config.public_base_url is None
        or not isinstance(auth_provider, JwtOidcAuthProvider)
    ):
        return GovernedReleaseMcpAuthority()
    return GovernedReleaseMcpAuthority(
        application_id=config.governed_release_application_id or "",
        public_base_url=config.public_base_url,
        authorization_server_issuer=config.external_authorization_server,
        oauth_audience=auth_provider.config.audience,
        allowed_client_ids=tuple(auth_provider.config.allowed_client_ids),
    )


def _optional_application_id(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip()
    if _APPLICATION_ID.fullmatch(normalized) is None:
        raise AuthProfileConfigurationError(f"{GOVERNED_RELEASE_APPLICATION_ID_ENV} must be a clean application id")
    return normalized


def _local_consent_roles(source: Mapping[str, str]) -> tuple[str, ...]:
    """Parse the opt-in roles a local browser consent is allowed to claim."""

    value = source.get(LOCAL_CONSENT_ROLES_ENV)
    if value is None or not value.strip():
        return ()
    if RuntimeProfile.from_value(source.get("FOUNDRY_LITE_RUNTIME_PROFILE")).is_protected:
        raise AuthProfileConfigurationError(
            f"{LOCAL_CONSENT_ROLES_ENV} is not allowed for protected hosted MCP runtime"
        )
    roles = tuple(role.strip() for role in value.replace(",", " ").split(" ") if role.strip())
    for role in roles:
        if _ROLE.fullmatch(role) is None:
            raise AuthProfileConfigurationError(f"{LOCAL_CONSENT_ROLES_ENV} contains an invalid role name")
    return roles


def _optional_dynamic_client_application_id(source: Mapping[str, str]) -> str | None:
    """Unauthenticated client registration stays off unless a non-protected operator opts in."""

    value = source.get(DYNAMIC_CLIENT_APPLICATION_ID_ENV)
    if value is None or not value.strip():
        return None
    if RuntimeProfile.from_value(source.get("FOUNDRY_LITE_RUNTIME_PROFILE")).is_protected:
        raise AuthProfileConfigurationError(
            f"{DYNAMIC_CLIENT_APPLICATION_ID_ENV} is not allowed for protected hosted MCP runtime"
        )
    normalized = value.strip()
    if _APPLICATION_ID.fullmatch(normalized) is None:
        raise AuthProfileConfigurationError(f"{DYNAMIC_CLIENT_APPLICATION_ID_ENV} must be a clean application id")
    return normalized


def _require_external_oidc_provider(auth_provider: AuthProvider, authorization_server: str) -> None:
    provider = _require_jwt_oidc_provider(auth_provider)
    _require_matching_issuer(provider, authorization_server)
    _require_issuer_session_authority(provider)
    _require_human_grant(provider)
    _require_authorization_code_grant(provider)
    _require_allowed_oauth_clients(provider)
    _require_jwks_verification_key(provider)


def _require_local_public_oauth_issuer(source: Mapping[str, str], public_base_url: str | None) -> None:
    """Keep local OAuth discovery and token identity on one reachable public origin."""

    if public_base_url is None:
        return
    oauth_issuer = _optional_local_oauth_issuer(source.get(OAUTH_ISSUER_ENV))
    if oauth_issuer is None:
        raise AuthProfileConfigurationError(
            f"{OAUTH_ISSUER_ENV} is required when {MCP_PUBLIC_BASE_URL_ENV} exposes the local OAuth server"
        )
    if oauth_issuer != public_base_url:
        raise AuthProfileConfigurationError(
            f"{OAUTH_ISSUER_ENV} must exactly match {MCP_PUBLIC_BASE_URL_ENV} for the local OAuth server"
        )


def _optional_local_oauth_issuer(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip().rstrip("/")
    if not _is_safe_https_url(urlsplit(normalized), is_origin=True):
        raise AuthProfileConfigurationError(f"{OAUTH_ISSUER_ENV} must be a clean absolute HTTPS origin")
    return normalized


def _require_jwt_oidc_provider(auth_provider: AuthProvider) -> JwtOidcAuthProvider:
    if not isinstance(auth_provider, JwtOidcAuthProvider):
        raise AuthProfileConfigurationError(
            f"{MCP_AUTHORIZATION_SERVER_ENV} requires FOUNDRY_LITE_AUTH_PROFILE=jwt or oidc"
        )
    return auth_provider


def _require_matching_issuer(provider: JwtOidcAuthProvider, authorization_server: str) -> None:
    if _issuer_key(provider.config.issuer) != _issuer_key(authorization_server):
        raise AuthProfileConfigurationError(f"{MCP_AUTHORIZATION_SERVER_ENV} must match the configured OIDC issuer")


def _require_issuer_session_authority(provider: JwtOidcAuthProvider) -> None:
    if provider.config.oauth_session_authority != "issuer":
        raise AuthProfileConfigurationError("external MCP OAuth requires issuer-authoritative sessions")


def _require_human_grant(provider: JwtOidcAuthProvider) -> None:
    if not provider.config.human_grant_claim or not provider.config.human_grant_value:
        raise AuthProfileConfigurationError("external MCP OAuth requires configured human grant claim and value")


def _require_authorization_code_grant(provider: JwtOidcAuthProvider) -> None:
    if not provider.config.grant_type_claim or not provider.config.grant_type_value:
        raise AuthProfileConfigurationError(
            "external MCP OAuth requires an explicit authorization-code grant claim and value"
        )


def _require_allowed_oauth_clients(provider: JwtOidcAuthProvider) -> None:
    if not provider.config.allowed_client_ids:
        raise AuthProfileConfigurationError("external MCP OAuth requires at least one allowed OAuth client id")


def _require_jwks_verification_key(provider: JwtOidcAuthProvider) -> None:
    keys = provider.config.jwks.get("keys")
    if not isinstance(keys, list) or not keys:
        raise AuthProfileConfigurationError("external MCP OAuth requires at least one configured JWKS verification key")


def _optional_https_url(value: str | None, *, is_origin: bool) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if not _is_safe_https_url(parsed, is_origin=is_origin):
        setting = MCP_PUBLIC_BASE_URL_ENV if is_origin else MCP_AUTHORIZATION_SERVER_ENV
        raise AuthProfileConfigurationError(f"{setting} must be a clean absolute HTTPS URL")
    return normalized


def _is_safe_https_url(parsed: SplitResult, *, is_origin: bool) -> bool:
    return bool(
        parsed.scheme.lower() == "https"
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and (not is_origin or parsed.path in {"", "/"})
    )


def _issuer_key(value: str) -> str:
    return value.strip().rstrip("/")


__all__ = [
    "DYNAMIC_CLIENT_APPLICATION_ID_ENV",
    "GOVERNED_RELEASE_APPLICATION_ID_ENV",
    "LOCAL_CONSENT_ROLES_ENV",
    "MCP_AUTHORIZATION_SERVER_ENV",
    "MCP_PUBLIC_BASE_URL_ENV",
    "McpAuthorizationConfig",
    "governed_release_mcp_authority",
    "mcp_authorization_config_from_env",
]
