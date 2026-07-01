"""AuthProvider port: turn raw transport credentials into a verified Principal.

Sprint 02A's Scale Foundation calls out an AuthProvider boundary so the
application layer never trusts raw transport headers directly. The local
``HeaderTrustAuthProvider`` preserves the dev/demo header behaviour as an
explicit, named "I trust the proxy" adapter, while the JWT/OIDC adapter verifies
bearer tokens before building the same ``Principal`` shape. Future production
auth profiles can replace either adapter without touching application services.

The port deliberately keeps the credential type opaque (``Mapping[str, str]``)
so adapters can decide what they read: headers, bearer tokens, mTLS subject,
etc. Anonymous access is also an explicit method instead of a magic empty
dictionary - it forces a service composition to opt into anonymous fallback.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract

__all__ = ["Credentials", "Principal", "AuthProvider"]

Credentials = Mapping[str, str]


@dataclass(frozen=True)
class Principal:
    """Verified identity produced by an AuthProvider.

    Every field must be non-empty after authentication succeeds. Adapters are
    responsible for rejecting partial credentials before constructing a
    Principal; the application layer treats the value as trustworthy.
    """

    tenant_id: str
    actor_user_id: str
    roles: tuple[str, ...]
    application_id: str | None = None
    client_id: str | None = None
    token_scopes: tuple[str, ...] = ()


@runtime_checkable
class AuthProvider(Protocol):
    """Translate raw transport credentials into a verified Principal.

    Implementations may raise :class:`PermissionDenied` for malformed or
    rejected credentials. ``anonymous()`` returns the fallback Principal used
    when no credentials are provided; the demo and dev adapters return a viewer
    role, while strict production adapters raise instead.
    """

    @property
    def profile_name(self) -> str: ...

    def failure_contract(self) -> AdapterFailureContract:
        """Return the adapter failure taxonomy promised by this profile."""
        ...

    def authenticate(self, credentials: Credentials) -> Principal: ...

    def anonymous(self) -> Principal: ...
