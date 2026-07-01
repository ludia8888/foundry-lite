from __future__ import annotations

from typing import Any, cast

from foundry_lite.application.ports import (
    OAuthAuthorizationCodeRecord,
    OAuthSessionRecord,
    OAuthSessionRepository,
)
from foundry_lite.application.ports.oauth_session_repository import OAuthRefreshTokenRecord, OAuthTokenIssuer
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.auth import LocalOAuthTokenIssuer
from foundry_lite.infrastructure.repositories import SqlAlchemyOAuthSessionRepository
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def test_oauth_session_repository_contract_round_trips_code_session_and_refresh_token() -> None:
    engine = _engine()
    repository: OAuthSessionRepository = SqlAlchemyOAuthSessionRepository(engine)

    with engine.begin() as conn:
        code = repository.insert_authorization_code(transaction=conn, record=_code_record())
        session = repository.insert_session(transaction=conn, record=_session_record())
        refresh = repository.insert_refresh_token(transaction=conn, record=_refresh_record())

        fetched_code = repository.authorization_code_by_hash(
            transaction=conn, tenant_id="tenant-a", code_hash="sha256:code"
        )
        repository.mark_authorization_code_consumed(
            transaction=conn,
            tenant_id="tenant-a",
            code_id="code-1",
            consumed_at="2026-07-01T00:01:00+00:00",
        )
        fetched_session = repository.session_by_id(transaction=conn, tenant_id="tenant-a", session_id="session-1")
        repository.rotate_refresh_token(
            transaction=conn,
            tenant_id="tenant-a",
            token_id="refresh-1",
            replacement_token_id="refresh-2",
            used_at="2026-07-01T00:02:00+00:00",
        )
        rotated = repository.refresh_token_by_hash(transaction=conn, tenant_id="tenant-a", token_hash="sha256:refresh")
        repository.revoke_session(
            transaction=conn,
            tenant_id="tenant-a",
            session_id="session-1",
            revoked_at="2026-07-01T00:03:00+00:00",
        )
        revoked = repository.session_by_id(transaction=conn, tenant_id="tenant-a", session_id="session-1")

    assert code["id"] == "code-1"
    assert session["id"] == "session-1"
    assert refresh["id"] == "refresh-1"
    assert fetched_code is not None and fetched_code["scopes"] == ["osdk:object:Order:read"]
    assert fetched_session is not None and fetched_session["roles"] == ["admin"]
    assert rotated is not None and rotated["status"] == "rotated"
    assert rotated["replaced_by_token_id"] == "refresh-2"
    assert revoked is not None and revoked["status"] == "revoked"


def test_oauth_token_issuer_contract_emits_public_jwks_and_osdk_claims(tmp_path) -> None:
    issuer: OAuthTokenIssuer = LocalOAuthTokenIssuer.from_key_path(tmp_path / "oauth-private-key.pem")

    token = issuer.issue_access_token(
        {
            "tenant_id": "tenant-a",
            "actor_user_id": "user-a",
            "roles": ["admin"],
            "application_id": "osdk_app_1",
            "client_id": "orders-web",
            "scopes": ["osdk:object:Order:read"],
            "session_id": "session-1",
        },
        ttl_seconds=900,
    )
    jwks = issuer.public_jwks()

    assert token["tokenType"] == "Bearer"
    assert token["expiresIn"] == 900
    assert token["claims"]["osdk_app_id"] == "osdk_app_1"
    assert token["claims"]["client_id"] == "orders-web"
    assert token["claims"]["scope"] == "osdk:object:Order:read"
    assert cast(list[dict[str, Any]], jwks["keys"])[0]["kid"] == "foundry-lite-local-osdk-oauth"


def _engine() -> Engine:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    return engine


def _code_record() -> OAuthAuthorizationCodeRecord:
    return OAuthAuthorizationCodeRecord(
        code_id="code-1",
        tenant_id="tenant-a",
        code_hash="sha256:code",
        app_id="osdk_app_1",
        client_id="orders-web",
        actor_user_id="user-a",
        roles=("admin",),
        redirect_uri="https://orders.example.test/callback",
        code_challenge="challenge",
        code_challenge_method="S256",
        scopes=("osdk:object:Order:read",),
        expires_at="2026-07-01T00:05:00+00:00",
        created_at="2026-07-01T00:00:00+00:00",
    )


def _session_record() -> OAuthSessionRecord:
    return OAuthSessionRecord(
        session_id="session-1",
        tenant_id="tenant-a",
        app_id="osdk_app_1",
        client_id="orders-web",
        actor_user_id="user-a",
        roles=("admin",),
        scopes=("osdk:object:Order:read",),
        status="active",
        created_at="2026-07-01T00:00:00+00:00",
        expires_at="2026-07-01T01:00:00+00:00",
    )


def _refresh_record() -> OAuthRefreshTokenRecord:
    return OAuthRefreshTokenRecord(
        token_id="refresh-1",
        tenant_id="tenant-a",
        session_id="session-1",
        token_hash="sha256:refresh",
        status="active",
        expires_at="2026-07-01T01:00:00+00:00",
        created_at="2026-07-01T00:00:00+00:00",
    )
