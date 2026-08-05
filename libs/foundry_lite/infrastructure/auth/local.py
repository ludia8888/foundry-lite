"""Local AuthProvider adapters: HeaderTrust and Demo.

These adapters document - rather than fix - the current authentication posture
so future scale work can swap them out cleanly.

``HeaderTrustAuthProvider``
    Mirrors what ``apps/api/foundry_lite_api/main.py`` does today. It reads
    ``X-Tenant-ID``, ``X-User-ID``, ``X-Roles`` straight from the credential
    map and trusts them. The name makes the trust explicit so audit reviewers
    don't have to read FastAPI dependencies to discover the implicit policy.

``DemoAuthProvider``
    Returns a fixed admin Principal regardless of credentials. Used by the CLI
    and demo flows that need to bypass header parsing entirely.

The JWT/OIDC adapter now lives in ``foundry_lite.infrastructure.auth.oidc``.
These local adapters remain useful for demo/dev execution, but production
startup refuses them unless a strict profile is configured explicitly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.auth_provider import (
    AuthProvider,
    Credentials,
    Principal,
)
from foundry_lite.domain.context import (
    DEFAULT_ACTOR_USER_ID,
    DEFAULT_ROLES,
    DEFAULT_TENANT_ID,
    DEMO_ADMIN_ROLES,
)

__all__ = [
    "HEADER_APP_ID_KEY",
    "HEADER_CLIENT_ID_KEY",
    "HEADER_ROLES_KEY",
    "HEADER_SCOPES_KEY",
    "HEADER_TENANT_KEY",
    "HEADER_USER_KEY",
    "HEADER_USER_ATTRIBUTES_KEY",
    "DemoAuthProvider",
    "HeaderTrustAuthProvider",
]

HEADER_TENANT_KEY = "x-tenant-id"
HEADER_USER_KEY = "x-user-id"
HEADER_ROLES_KEY = "x-roles"
HEADER_APP_ID_KEY = "x-foundry-lite-app-id"
HEADER_CLIENT_ID_KEY = "x-foundry-lite-client-id"
HEADER_SCOPES_KEY = "x-foundry-lite-scopes"
HEADER_USER_ATTRIBUTES_KEY = "x-user-attributes"

_DEMO_USER_ID = "user-demo-admin"


def _normalise(credentials: Credentials) -> dict[str, str]:
    return {key.lower(): value for key, value in credentials.items()}


def _split_roles(value: str | None, fallback: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return fallback
    roles = tuple(role.strip() for role in value.split(",") if role.strip())
    return roles or fallback


def _split_scopes(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    normalized = value.replace(",", " ")
    return tuple(scope.strip() for scope in normalized.split(" ") if scope.strip())


def _user_attributes(value: str | None) -> dict[str, object]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict) or not all(isinstance(key, str) and key for key in parsed):
        raise ValueError("X-User-Attributes must be a JSON object with non-empty string keys")
    return parsed


@dataclass(frozen=True)
class HeaderTrustAuthProvider:
    """Trust ``X-Tenant-ID`` / ``X-User-ID`` / ``X-Roles`` headers verbatim.

    This is the pre-AuthProvider behaviour of the HTTP API, now named so it
    cannot hide. Use it only in environments where the proxy in front of the
    API is the security boundary (mTLS, internal network, dev/demo).
    """

    default_tenant_id: str = DEFAULT_TENANT_ID
    default_actor_user_id: str = DEFAULT_ACTOR_USER_ID
    default_roles: tuple[str, ...] = DEFAULT_ROLES
    profile_name: str = "header-trust-auth"

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "authenticate",
                    "authentication",
                    False,
                    "Header-trust credentials were missing or malformed.",
                ),
            ),
        )

    def authenticate(self, credentials: Credentials) -> Principal:
        headers = _normalise(credentials)
        tenant_id = headers.get(HEADER_TENANT_KEY) or self.default_tenant_id
        actor_user_id = headers.get(HEADER_USER_KEY) or self.default_actor_user_id
        roles = _split_roles(headers.get(HEADER_ROLES_KEY), self.default_roles)
        return Principal(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            roles=roles,
            application_id=headers.get(HEADER_APP_ID_KEY),
            client_id=headers.get(HEADER_CLIENT_ID_KEY),
            token_scopes=_split_scopes(headers.get(HEADER_SCOPES_KEY)),
            user_attributes=_user_attributes(headers.get(HEADER_USER_ATTRIBUTES_KEY)),
        )

    def anonymous(self) -> Principal:
        return Principal(
            tenant_id=self.default_tenant_id,
            actor_user_id=self.default_actor_user_id,
            roles=self.default_roles,
        )


@dataclass(frozen=True)
class DemoAuthProvider:
    """Fixed admin Principal for CLI/demo flows.

    ``authenticate`` ignores credentials and returns the demo admin so that
    seeding scripts and end-to-end demos don't need to fabricate header maps.
    """

    tenant_id: str = DEFAULT_TENANT_ID
    actor_user_id: str = _DEMO_USER_ID
    roles: tuple[str, ...] = field(default_factory=lambda: tuple(DEMO_ADMIN_ROLES))
    profile_name: str = "demo-auth"

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "authenticate",
                    "authorization",
                    False,
                    "Demo identity is only valid for local and demo execution.",
                ),
            ),
        )

    def authenticate(self, credentials: Credentials) -> Principal:  # noqa: ARG002 - fixed identity
        return self.anonymous()

    def anonymous(self) -> Principal:
        return Principal(tenant_id=self.tenant_id, actor_user_id=self.actor_user_id, roles=self.roles)


# Static assertion: both adapters satisfy the AuthProvider Protocol.
_: AuthProvider = HeaderTrustAuthProvider()
_ = DemoAuthProvider()
