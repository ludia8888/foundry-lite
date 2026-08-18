"""Verify security and time-boundary contracts inside the exact deployed runtime image."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from cryptography.hazmat.primitives.asymmetric import rsa
from foundry_lite.application.ports.oauth_session_repository import (
    OAuthAccessTokenClaims,
)
from foundry_lite.application.services.object_store.query_cursor import (
    CURSOR_SIGNING_KEY_ENV,
    CURSOR_SIGNING_KEY_ID_ENV,
    CURSOR_SIGNING_KEYS_JSON_ENV,
    CURSOR_TTL_SECONDS_ENV,
    decode_object_query_cursor,
    encode_object_query_cursor,
)
from foundry_lite.application.services.pipeline_run_recovery import (
    is_stale_pipeline_execution,
)
from foundry_lite.application.services.runtime_run_cursors import (
    OPERATIONS_CURSOR_SIGNING_KEY_ENV,
    OPERATIONS_CURSOR_SIGNING_KEY_ID_ENV,
    OPERATIONS_CURSOR_SIGNING_KEYS_JSON_ENV,
    OPERATIONS_CURSOR_TTL_SECONDS_ENV,
    decode_runtime_run_cursor,
    encode_runtime_run_cursor,
)
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed
from foundry_lite.infrastructure.auth import (
    JwtOidcAuthConfig,
    JwtOidcAuthProvider,
    LocalOAuthTokenIssuer,
)


def verify() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "status": "passed",
        "localJwksRotationGraceAndRetirement": _jwks_rotation(),
        "expiredAccessTokenRejected": _expired_token(),
        "revokedSessionTokenRejected": _revoked_session_token(),
        "executionLeaseExpiryDetected": _lease_expiration(),
        "objectCursorExpiryAndRotation": _object_cursor(),
        "operationsCursorExpiryAndRotation": _operations_cursor(),
        "externalIssuerNetworkPath": "notProven",
        "rawTokensStored": False,
        "rawKeysStored": False,
    }


def _jwks_rotation() -> bool:
    with tempfile.TemporaryDirectory(prefix="security-time-") as temporary:
        issuer = LocalOAuthTokenIssuer.from_key_path(Path(temporary) / "oauth.pem")
        old = issuer.issue_access_token(_claims(), ttl_seconds=900)
        rotated = issuer.rotated(
            rsa.generate_private_key(public_exponent=65537, key_size=2048),
            key_id="kid-new",
        )
        new = rotated.issue_access_token(_claims("user-new"), ttl_seconds=900)
        jwks_state: dict[str, object] = dict(issuer.public_jwks())
        clock = [1_000.0]
        verifier = JwtOidcAuthProvider(
            JwtOidcAuthConfig(
                issuer=issuer.issuer,
                audience=issuer.audience,
                jwks=jwks_state,
                jwks_refresh_interval_seconds=0,
                retired_key_grace_seconds=10,
            ),
            jwks_loader=lambda: jwks_state,
            clock=lambda: clock[0],
        )
        if verifier.authenticate({"Authorization": f"Bearer {old['accessToken']}"}).actor_user_id != "user-osdk":
            return False
        keys = cast(list[object], rotated.public_jwks()["keys"])
        jwks_state = {"keys": [keys[0]]}
        clock[0] += 1
        if verifier.authenticate({"Authorization": f"Bearer {new['accessToken']}"}).actor_user_id != "user-new":
            return False
        verifier.authenticate({"Authorization": f"Bearer {old['accessToken']}"})
        clock[0] += 11
        return _permission_denied(verifier, old["accessToken"])


def _expired_token() -> bool:
    with tempfile.TemporaryDirectory(prefix="expired-token-") as temporary:
        issuer = LocalOAuthTokenIssuer.from_key_path(Path(temporary) / "oauth.pem")
        token = issuer.issue_access_token(_claims(), ttl_seconds=-1)
        verifier = JwtOidcAuthProvider(
            JwtOidcAuthConfig(
                issuer=issuer.issuer,
                audience=issuer.audience,
                jwks=issuer.public_jwks(),
            )
        )
        return _permission_denied(verifier, token["accessToken"])


def _revoked_session_token() -> bool:
    with tempfile.TemporaryDirectory(prefix="revoked-session-") as temporary:
        issuer = LocalOAuthTokenIssuer.from_key_path(Path(temporary) / "oauth.pem")
        token = issuer.issue_access_token(_claims(), ttl_seconds=900)
        claims = cast(dict[str, object], token["claims"])
        token_id = cast(str, claims["jti"])
        verifier = JwtOidcAuthProvider(
            JwtOidcAuthConfig(
                issuer=issuer.issuer,
                audience=issuer.audience,
                jwks=issuer.public_jwks(),
                revoked_token_ids=frozenset({token_id}),
            )
        )
        return _permission_denied(verifier, token["accessToken"])


def _permission_denied(verifier: JwtOidcAuthProvider, token: str) -> bool:
    try:
        verifier.authenticate({"Authorization": f"Bearer {token}"})
    except PermissionDenied:
        return True
    return False


def _lease_expiration() -> bool:
    now = datetime(2026, 8, 18, tzinfo=UTC)
    expired_lease_token = secrets.token_urlsafe(16)
    current_lease_token = secrets.token_urlsafe(16)
    expired = {
        "status": "running",
        "execution_lease_token": expired_lease_token,
        "execution_lease_expires_at": (now - timedelta(seconds=1)).isoformat(),
    }
    current = {
        "status": "running",
        "execution_lease_token": current_lease_token,
        "execution_lease_expires_at": (now + timedelta(seconds=1)).isoformat(),
    }
    return is_stale_pipeline_execution(expired, now=now) and not is_stale_pipeline_execution(current, now=now)


def _object_cursor() -> bool:
    old = {
        "FOUNDRY_LITE_RUNTIME_PROFILE": "production",
        CURSOR_SIGNING_KEY_ID_ENV: "object-old",
        CURSOR_SIGNING_KEY_ENV: "object-old-secret",
        CURSOR_TTL_SECONDS_ENV: "10",
    }
    order = [{"property": "amount", "direction": "desc"}]
    row = {"object_id": "object-1", "properties": {"amount": 10}}
    with _temporary_env(old):
        cursor = encode_object_query_cursor(
            row,  # type: ignore[arg-type]
            order,  # type: ignore[arg-type]
            None,
            "index-1",
            actor_user_id="user-a",
            tenant_id="tenant-a",
            now_epoch=100,
        )
        if not _object_cursor_expired(cursor, order):
            return False
        os.environ[CURSOR_SIGNING_KEY_ID_ENV] = "object-new"
        os.environ[CURSOR_SIGNING_KEY_ENV] = "object-new-secret"
        os.environ[CURSOR_SIGNING_KEYS_JSON_ENV] = json.dumps(
            {"object-old": "object-old-secret", "object-new": "object-new-secret"}
        )
        return bool(
            decode_object_query_cursor(
                cursor,
                order,  # type: ignore[arg-type]
                None,
                "index-1",
                actor_user_id="user-a",
                tenant_id="tenant-a",
                now_epoch=110,
            )
        )


def _object_cursor_expired(cursor: str, order: list[dict[str, str]]) -> bool:
    try:
        decode_object_query_cursor(
            cursor,
            order,  # type: ignore[arg-type]
            None,
            "index-1",
            actor_user_id="user-a",
            tenant_id="tenant-a",
            now_epoch=111,
        )
    except ValidationFailed:
        return True
    return False


def _operations_cursor() -> bool:
    old = {
        "FOUNDRY_LITE_RUNTIME_PROFILE": "production",
        OPERATIONS_CURSOR_SIGNING_KEY_ID_ENV: "operations-old",
        OPERATIONS_CURSOR_SIGNING_KEY_ENV: "operations-old-secret",
        OPERATIONS_CURSOR_TTL_SECONDS_ENV: "10",
    }
    row = {"id": "run-1", "completed_at": "2026-08-18T00:00:00Z"}
    with _temporary_env(old):
        cursor = encode_runtime_run_cursor(
            row,
            actor_user_id="user-a",
            run_type="action",
            status="succeeded",
            since=None,
            tenant_id="tenant-a",
            until=None,
            now_epoch=100,
        )
        if not _operations_cursor_expired(cursor):
            return False
        os.environ[OPERATIONS_CURSOR_SIGNING_KEY_ID_ENV] = "operations-new"
        os.environ[OPERATIONS_CURSOR_SIGNING_KEY_ENV] = "operations-new-secret"
        os.environ[OPERATIONS_CURSOR_SIGNING_KEYS_JSON_ENV] = json.dumps(
            {
                "operations-old": "operations-old-secret",
                "operations-new": "operations-new-secret",
            }
        )
        return bool(
            decode_runtime_run_cursor(
                cursor,
                actor_user_id="user-a",
                run_type="action",
                status="succeeded",
                since=None,
                tenant_id="tenant-a",
                until=None,
                now_epoch=110,
            )
        )


def _operations_cursor_expired(cursor: str) -> bool:
    try:
        decode_runtime_run_cursor(
            cursor,
            actor_user_id="user-a",
            run_type="action",
            status="succeeded",
            since=None,
            tenant_id="tenant-a",
            until=None,
            now_epoch=111,
        )
    except ValidationFailed:
        return True
    return False


@contextmanager
def _temporary_env(values: dict[str, str]) -> Iterator[None]:
    before = dict(os.environ)
    os.environ.update(values)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(before)


def _claims(actor_user_id: str = "user-osdk") -> OAuthAccessTokenClaims:
    return {
        "tenant_id": "tenant-osdk",
        "actor_user_id": actor_user_id,
        "roles": ["admin", "data_engineer"],
        "application_id": "osdk-app-orders",
        "client_id": "orders-web-client",
        "scopes": ["osdk:object:Order:read"],
        "session_id": "session-1",
    }


def main() -> int:
    receipt = verify()
    if not all(value is True for key, value in receipt.items() if key not in _metadata_fields()):
        receipt["status"] = "failed"
    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))
    return 0 if receipt["status"] == "passed" else 1


def _metadata_fields() -> frozenset[str]:
    return frozenset(
        {
            "schemaVersion",
            "status",
            "externalIssuerNetworkPath",
            "rawTokensStored",
            "rawKeysStored",
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
