"""Public browser login coordinates and private-pilot admission at the API edge.

This composition-root policy never creates a principal or adds business roles.
OIDC verifies the token; the existing application services still enforce grants.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from foundry_lite.application.ports.auth_provider import AuthProvider, Principal
from foundry_lite.domain.errors import PermissionDenied
from foundry_lite.infrastructure.auth import (
    AuthProfileConfigurationError,
    DemoAuthProvider,
    HeaderTrustAuthProvider,
    JwtOidcAuthProvider,
)

_PREFIX = "FOUNDRY_LITE_BROWSER_"
_FIELDS = ("CLIENT_ID", "PUBLIC_BASE_URL", "APPLICATION_ID", "OWNER_SUBJECT")


@dataclass(frozen=True)
class BrowserAuthConfig:
    mode: Literal["local", "unavailable", "keycloak"] = "unavailable"
    issuer: str | None = None
    client_id: str | None = None
    public_base_url: str | None = None
    application_id: str | None = None
    owner_subject: str | None = None

    def public_configuration(self) -> dict[str, str]:
        if self.mode != "keycloak":
            return {"mode": self.mode}
        return {
            "mode": self.mode,
            "issuer": str(self.issuer),
            "clientId": str(self.client_id),
            "redirectUri": f"{self.public_base_url}/auth/callback",
            "applicationId": str(self.application_id),
        }

    def check_principal(self, principal: Principal) -> None:
        if self.mode == "keycloak" and principal.client_id == self.client_id:
            self.require_browser_principal(principal)

    def check_request(self, principal: Principal, path: str, method: str) -> None:
        if self.mode != "keycloak" or principal.client_id != self.client_id:
            return
        self.require_browser_principal(principal)
        root = f"/api/aip/pilot/operating-applications/{self.application_id}"
        pattern = re.escape(root)
        is_read = method == "GET" and path in {"/api/auth/browser/session", "/api/ontology/catalog", root}
        is_work = method == "POST" and re.fullmatch(rf"{pattern}/(objects/[^/]+/query|actions/[^/]+/runs)", path)
        # Relationship reads retain the existing Object/Link OSDK scope checks.
        is_link_read = method == "GET" and re.fullmatch(r"/api/objects/[^/]+/[^/]+/links/[^/]+", path)
        if not (is_read or is_work or is_link_read):
            raise PermissionDenied(
                "이 로그인은 배정된 업무 앱에서만 사용할 수 있습니다.",
                details={"resource": "browser_session", "reason": "private_application_surface_required"},
            )

    def require_browser_principal(self, principal: Principal) -> None:
        if not (
            self.mode == "keycloak"
            and principal.client_id == self.client_id
            and principal.application_id == self.application_id
            and principal.actor_user_id == self.owner_subject
            and principal.roles == ("viewer",)
            and principal.is_human_oauth is True
            and principal.oauth_grant_type == "authorization_code"
        ):
            raise PermissionDenied(
                "이 비공개 업무 앱에 초대된 계정이 아닙니다.",
                details={"resource": "browser_session", "reason": "private_application_access_required"},
            )


def browser_auth_from_env(source: Mapping[str, str], provider: AuthProvider) -> BrowserAuthConfig:
    values = {name: source.get(_PREFIX + name, "").strip() for name in _FIELDS}
    if not any(values.values()):
        mode: Literal["local", "unavailable"] = (
            "local" if isinstance(provider, HeaderTrustAuthProvider | DemoAuthProvider) else "unavailable"
        )
        return BrowserAuthConfig(mode=mode)
    if not all(values.values()) or not isinstance(provider, JwtOidcAuthProvider):
        raise AuthProfileConfigurationError("browser login requires complete private-app settings and strict OIDC")
    if values["CLIENT_ID"] not in provider.config.allowed_client_ids:
        raise AuthProfileConfigurationError("browser login client must be explicitly allowed by the OIDC verifier")
    public_base = _https_origin(values["PUBLIC_BASE_URL"])
    issuer = _keycloak_issuer(provider.config.issuer)
    return BrowserAuthConfig(
        mode="keycloak",
        issuer=issuer,
        client_id=values["CLIENT_ID"],
        public_base_url=public_base,
        application_id=values["APPLICATION_ID"],
        owner_subject=values["OWNER_SUBJECT"],
    )


def _keycloak_issuer(value: str) -> str:
    issuer = value.rstrip("/")
    parsed = urlsplit(issuer)
    _https_origin(f"{parsed.scheme}://{parsed.netloc}")
    if parsed.query or parsed.fragment or "/realms/" not in parsed.path or not parsed.path.rsplit("/", 1)[1]:
        raise AuthProfileConfigurationError("browser Keycloak adapter requires a realm issuer")
    return issuer


def _https_origin(value: str) -> str:
    parsed = urlsplit(value)
    if not (
        parsed.scheme == "https"
        and parsed.hostname
        and not parsed.username
        and not parsed.password
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    ):
        raise AuthProfileConfigurationError("browser login requires a clean HTTPS origin")
    return value.rstrip("/")
