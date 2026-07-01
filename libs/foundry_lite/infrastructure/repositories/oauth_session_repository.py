"""SQLAlchemy repository adapter for oauth session repository persistence."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, insert, select
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.oauth_session_repository import (
    OAuthAuthorizationCodeRecord,
    OAuthAuthorizationCodeRow,
    OAuthRefreshTokenRecord,
    OAuthRefreshTokenRow,
    OAuthSessionRecord,
    OAuthSessionRow,
)
from foundry_lite.application.state_transitions import (
    OSDK_OAUTH_REFRESH_REVOKED,
    OSDK_OAUTH_REFRESH_ROTATED,
    OSDK_OAUTH_SESSION_COMPROMISED,
    OSDK_OAUTH_SESSION_REVOKED,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update, cas_status_update_many


class SqlAlchemyOAuthSessionRepository:
    """SQLAlchemy local OAuth code/session/refresh-token store."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def insert_authorization_code(
        self, *, transaction: Any, record: OAuthAuthorizationCodeRecord
    ) -> OAuthAuthorizationCodeRow:
        values = _authorization_code_values(record)
        values.pop("tenant_id", None)
        transaction.execute(insert(db.osdk_oauth_authorization_codes).values(tenant_id=record.tenant_id, **values))
        row = self.authorization_code_by_hash(
            transaction=transaction, tenant_id=record.tenant_id, code_hash=record.code_hash
        )
        if row is None:
            raise RuntimeError("inserted OAuth authorization code could not be read back")
        return row

    def authorization_code_by_hash(
        self, *, transaction: Any, tenant_id: str, code_hash: str
    ) -> OAuthAuthorizationCodeRow | None:
        row = (
            transaction.execute(
                select(db.osdk_oauth_authorization_codes).where(
                    and_(
                        db.osdk_oauth_authorization_codes.c.tenant_id == tenant_id,
                        db.osdk_oauth_authorization_codes.c.code_hash == code_hash,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(OAuthAuthorizationCodeRow, dict(row)) if row else None

    def mark_authorization_code_consumed(
        self, *, transaction: Any, tenant_id: str, code_id: str, consumed_at: str
    ) -> None:
        transaction.execute(
            db.osdk_oauth_authorization_codes.update()
            .where(
                and_(
                    db.osdk_oauth_authorization_codes.c.tenant_id == tenant_id,
                    db.osdk_oauth_authorization_codes.c.id == code_id,
                )
            )
            .values(consumed_at=consumed_at)
        )

    def insert_session(self, *, transaction: Any, record: OAuthSessionRecord) -> OAuthSessionRow:
        values = _session_values(record)
        values.pop("tenant_id", None)
        transaction.execute(insert(db.osdk_oauth_sessions).values(tenant_id=record.tenant_id, **values))
        row = self.session_by_id(transaction=transaction, tenant_id=record.tenant_id, session_id=record.session_id)
        if row is None:
            raise RuntimeError("inserted OAuth session could not be read back")
        return row

    def session_by_id(self, *, transaction: Any, tenant_id: str, session_id: str) -> OAuthSessionRow | None:
        row = (
            transaction.execute(
                select(db.osdk_oauth_sessions).where(
                    and_(db.osdk_oauth_sessions.c.tenant_id == tenant_id, db.osdk_oauth_sessions.c.id == session_id)
                )
            )
            .mappings()
            .first()
        )
        return cast(OAuthSessionRow, dict(row)) if row else None

    def update_session_refresh(self, *, transaction: Any, tenant_id: str, session_id: str, refreshed_at: str) -> None:
        transaction.execute(
            db.osdk_oauth_sessions.update()
            .where(and_(db.osdk_oauth_sessions.c.tenant_id == tenant_id, db.osdk_oauth_sessions.c.id == session_id))
            .values(last_refreshed_at=refreshed_at)
        )

    def revoke_session(self, *, transaction: Any, tenant_id: str, session_id: str, revoked_at: str) -> None:
        cas_status_update(
            transaction,
            db.osdk_oauth_sessions,
            tenant_id=tenant_id,
            row_id=session_id,
            transition=OSDK_OAUTH_SESSION_REVOKED,
            values={"revoked_at": revoked_at},
        )

    def compromise_refresh_token_family(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        session_id: str,
        compromised_at: str,
        reason: str,
    ) -> None:
        cas_status_update(
            transaction,
            db.osdk_oauth_sessions,
            tenant_id=tenant_id,
            row_id=session_id,
            transition=OSDK_OAUTH_SESSION_COMPROMISED,
            values={"compromised_at": compromised_at, "compromise_reason": reason},
        )
        cas_status_update_many(
            transaction,
            db.osdk_oauth_refresh_tokens,
            tenant_id=tenant_id,
            transition=OSDK_OAUTH_REFRESH_REVOKED,
            values={"revoked_at": compromised_at},
            conditions=(db.osdk_oauth_refresh_tokens.c.session_id == session_id,),
        )

    def insert_refresh_token(self, *, transaction: Any, record: OAuthRefreshTokenRecord) -> OAuthRefreshTokenRow:
        values = _refresh_token_values(record)
        values.pop("tenant_id", None)
        transaction.execute(insert(db.osdk_oauth_refresh_tokens).values(tenant_id=record.tenant_id, **values))
        row = self.refresh_token_by_hash(
            transaction=transaction, tenant_id=record.tenant_id, token_hash=record.token_hash
        )
        if row is None:
            raise RuntimeError("inserted OAuth refresh token could not be read back")
        return row

    def refresh_token_by_hash(
        self, *, transaction: Any, tenant_id: str, token_hash: str
    ) -> OAuthRefreshTokenRow | None:
        row = (
            transaction.execute(
                select(db.osdk_oauth_refresh_tokens).where(
                    and_(
                        db.osdk_oauth_refresh_tokens.c.tenant_id == tenant_id,
                        db.osdk_oauth_refresh_tokens.c.token_hash == token_hash,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(OAuthRefreshTokenRow, dict(row)) if row else None

    def rotate_refresh_token(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        token_id: str,
        replacement_token_id: str,
        used_at: str,
    ) -> None:
        cas_status_update(
            transaction,
            db.osdk_oauth_refresh_tokens,
            tenant_id=tenant_id,
            row_id=token_id,
            transition=OSDK_OAUTH_REFRESH_ROTATED,
            values={"replaced_by_token_id": replacement_token_id, "used_at": used_at},
        )

    def revoke_refresh_token(self, *, transaction: Any, tenant_id: str, token_id: str, revoked_at: str) -> None:
        cas_status_update(
            transaction,
            db.osdk_oauth_refresh_tokens,
            tenant_id=tenant_id,
            row_id=token_id,
            transition=OSDK_OAUTH_REFRESH_REVOKED,
            values={"revoked_at": revoked_at},
        )


def _authorization_code_values(record: OAuthAuthorizationCodeRecord) -> dict[str, object]:
    return {
        "id": record.code_id,
        "tenant_id": record.tenant_id,
        "code_hash": record.code_hash,
        "app_id": record.app_id,
        "client_id": record.client_id,
        "actor_user_id": record.actor_user_id,
        "roles": list(record.roles),
        "redirect_uri": record.redirect_uri,
        "code_challenge": record.code_challenge,
        "code_challenge_method": record.code_challenge_method,
        "scopes": list(record.scopes),
        "expires_at": record.expires_at,
        "consumed_at": None,
        "created_at": record.created_at,
    }


def _session_values(record: OAuthSessionRecord) -> dict[str, object]:
    return {
        "id": record.session_id,
        "tenant_id": record.tenant_id,
        "app_id": record.app_id,
        "client_id": record.client_id,
        "actor_user_id": record.actor_user_id,
        "roles": list(record.roles),
        "scopes": list(record.scopes),
        "status": record.status,
        "created_at": record.created_at,
        "last_refreshed_at": None,
        "expires_at": record.expires_at,
        "revoked_at": None,
        "compromised_at": None,
        "compromise_reason": None,
    }


def _refresh_token_values(record: OAuthRefreshTokenRecord) -> dict[str, object]:
    return {
        "id": record.token_id,
        "tenant_id": record.tenant_id,
        "session_id": record.session_id,
        "token_hash": record.token_hash,
        "status": record.status,
        "expires_at": record.expires_at,
        "replaced_by_token_id": None,  # nosec B105 - column name only; raw refresh tokens are never stored.
        "created_at": record.created_at,
        "used_at": None,
        "revoked_at": None,
    }
