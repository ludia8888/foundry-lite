"""Application port contract for oauth session repository."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypedDict

from foundry_lite.application.ports.runtime_repository import RuntimeJsonObject
from foundry_lite.application.ports.transaction_context import TransactionContext


class OAuthAuthorizationCodeRow(TypedDict):
    id: str
    tenant_id: str
    code_hash: str
    app_id: str
    client_id: str
    actor_user_id: str
    roles: list[str]
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    scopes: list[str]
    expires_at: str
    consumed_at: str | None
    created_at: str


class OAuthSessionRow(TypedDict):
    id: str
    tenant_id: str
    app_id: str
    client_id: str
    actor_user_id: str
    roles: list[str]
    scopes: list[str]
    status: str
    created_at: str
    last_refreshed_at: str | None
    expires_at: str
    revoked_at: str | None
    compromised_at: str | None
    compromise_reason: str | None


class OAuthRefreshTokenRow(TypedDict):
    id: str
    tenant_id: str
    session_id: str
    token_hash: str
    status: str
    expires_at: str
    replaced_by_token_id: str | None
    created_at: str
    used_at: str | None
    revoked_at: str | None


@dataclass(frozen=True)
class OAuthAuthorizationCodeRecord:
    code_id: str
    tenant_id: str
    code_hash: str
    app_id: str
    client_id: str
    actor_user_id: str
    roles: tuple[str, ...]
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    scopes: tuple[str, ...]
    expires_at: str
    created_at: str


@dataclass(frozen=True)
class OAuthSessionRecord:
    session_id: str
    tenant_id: str
    app_id: str
    client_id: str
    actor_user_id: str
    roles: tuple[str, ...]
    scopes: tuple[str, ...]
    status: str
    created_at: str
    expires_at: str


@dataclass(frozen=True)
class OAuthRefreshTokenRecord:
    token_id: str
    tenant_id: str
    session_id: str
    token_hash: str
    status: str
    expires_at: str
    created_at: str


class OAuthSessionRepository(Protocol):
    """Durable local OAuth authorization-code, session, and refresh-token store."""

    def insert_authorization_code(
        self, *, transaction: TransactionContext, record: OAuthAuthorizationCodeRecord
    ) -> OAuthAuthorizationCodeRow: ...

    def authorization_code_by_hash(
        self, *, transaction: TransactionContext, tenant_id: str, code_hash: str
    ) -> OAuthAuthorizationCodeRow | None: ...

    def mark_authorization_code_consumed(
        self, *, transaction: TransactionContext, tenant_id: str, code_id: str, consumed_at: str
    ) -> bool:
        """Consume the code if still unconsumed; return False if it was already consumed."""
        ...

    def insert_session(self, *, transaction: TransactionContext, record: OAuthSessionRecord) -> OAuthSessionRow: ...

    def session_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, session_id: str
    ) -> OAuthSessionRow | None: ...

    def update_session_refresh(
        self, *, transaction: TransactionContext, tenant_id: str, session_id: str, refreshed_at: str
    ) -> None: ...

    def revoke_session(
        self, *, transaction: TransactionContext, tenant_id: str, session_id: str, revoked_at: str
    ) -> None: ...

    def compromise_refresh_token_family(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        session_id: str,
        compromised_at: str,
        reason: str,
    ) -> None: ...

    def insert_refresh_token(
        self, *, transaction: TransactionContext, record: OAuthRefreshTokenRecord
    ) -> OAuthRefreshTokenRow: ...

    def refresh_token_by_hash(
        self, *, transaction: TransactionContext, tenant_id: str, token_hash: str
    ) -> OAuthRefreshTokenRow | None: ...

    def rotate_refresh_token(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        token_id: str,
        replacement_token_id: str,
        used_at: str,
    ) -> bool:
        """Rotate the presented refresh token; return False if it was no longer active."""
        ...

    def revoke_refresh_token(
        self, *, transaction: TransactionContext, tenant_id: str, token_id: str, revoked_at: str
    ) -> None: ...


class OAuthAccessTokenClaims(TypedDict):
    tenant_id: str
    actor_user_id: str
    roles: list[str]
    application_id: str
    client_id: str
    scopes: list[str]
    session_id: str


class OAuthIssuedAccessToken(TypedDict):
    accessToken: str
    tokenType: str
    expiresIn: int
    claims: RuntimeJsonObject


class OAuthTokenIssuer(Protocol):
    """Issue access tokens that strict JWT/OIDC auth can verify."""

    def issue_access_token(self, claims: OAuthAccessTokenClaims, *, ttl_seconds: int) -> OAuthIssuedAccessToken: ...

    def public_jwks(self) -> RuntimeJsonObject: ...
