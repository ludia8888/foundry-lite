"""Application service helpers for osdk oauth session service workflows."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import cast

from foundry_lite.application.ports import (
    OAuthAccessTokenClaims,
    OAuthAuthorizationCodeRecord,
    OAuthAuthorizationCodeRow,
    OAuthRefreshTokenRecord,
    OAuthSessionRecord,
    RuntimeJsonObject,
    TransactionContext,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.osdk_oauth_session_refs import safe_code_ref, safe_session_ref
from foundry_lite.application.services.osdk_oauth_session_support import _OAuthAuditBoundary, _OAuthRateLimiter
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, PermissionDenied, ValidationFailed

_DEFAULT_CODE_TTL_SECONDS = 300
# Access tokens are stateless: the JWT verifier cannot consult the session store, so a
# revoked/logged-out session's access token stays valid until it expires. Keep the fallback
# TTL short to bound that window when a client does not configure its own TTL.
_DEFAULT_ACCESS_TTL_SECONDS = 120
_DEFAULT_REFRESH_TTL_SECONDS = 2_592_000
_PKCE_METHODS = frozenset({"S256"})
_CODE_CHALLENGE_MIN_LENGTH = 43
_CODE_CHALLENGE_MAX_LENGTH = 128
_CODE_CHALLENGE_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
_REFRESH_REUSE_REASON = "rotated_refresh_token_reuse"


class OsdkOAuthSessionService(CoreService):
    required_dependencies = (
        "engine",
        "osdk_application_repository",
        "oauth_session_repository",
        "oauth_token_issuer",
    )
    required_collaborators = ("runtime_service",)
    runtime_service: _OAuthAuditBoundary

    def __init__(self, **dependencies: object) -> None:
        super().__init__(**dependencies)
        self._rate_limiter = _OAuthRateLimiter()

    def authorize(
        self,
        *,
        ctx: RequestContext | None = None,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str = "S256",
        scopes: Sequence[str] = (),
        state: str | None = None,
    ) -> RuntimeJsonObject:
        ctx = ctx or RequestContext()
        method = _pkce_method(code_challenge_method)
        _require_valid_code_challenge(code_challenge)
        raw_code = _raw_token("oauth_code")
        now = _now()
        with self.engine.begin() as conn:
            self._rate_limiter.check(ctx, "authorize", client_id)
            client = self._require_active_client(conn, ctx, client_id, redirect_uri)
            scope_tuple = self._authorized_scopes(conn, ctx, cast(str, client["app_id"]), client, scopes)
            record = _authorization_code_record(
                ctx, client, raw_code, redirect_uri, code_challenge, method, scope_tuple
            )
            row = self.oauth_session_repository.insert_authorization_code(transaction=conn, record=record)
            self._audit(conn, ctx, "osdk.oauth.authorization_code.created", row, safe_code_ref(row, state))
        return _authorization_response(raw_code, redirect_uri, state, now, _expires_at(now, _DEFAULT_CODE_TTL_SECONDS))

    def exchange_authorization_code(
        self,
        *,
        ctx: RequestContext | None = None,
        client_id: str,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> RuntimeJsonObject:
        ctx = ctx or RequestContext()
        now = _now()
        invalid_code = False
        client: Mapping[str, object] | None = None
        session: Mapping[str, object] | None = None
        refresh: RuntimeJsonObject | None = None
        with self.engine.begin() as conn:
            self._rate_limiter.check(ctx, "token", client_id)
            code_row = self._exchangeable_code(conn, ctx, code, now)
            if code_row is None:
                invalid_code = True
            else:
                client = self._require_active_client(conn, ctx, client_id, redirect_uri)
                self._validate_code_exchange(code_row, client_id, redirect_uri, code_verifier)
                session = self._create_session(conn, code_row, client, now)
                refresh = self._create_refresh_token(conn, ctx, cast(str, session["id"]), client, now)
                consumed = self.oauth_session_repository.mark_authorization_code_consumed(
                    transaction=conn, tenant_id=ctx.tenant_id, code_id=code_row["id"], consumed_at=now
                )
                if not consumed:
                    # A concurrent exchange already consumed this single-use code:
                    # the earlier SELECT read it as unconsumed before the winner
                    # committed. Abort so this transaction's session and refresh
                    # token are rolled back rather than becoming a second grant.
                    raise ConflictDetected("OSDK OAuth authorization code was already consumed")
                self._audit(conn, ctx, "osdk.oauth.session.created", session, safe_session_ref(session))
        if invalid_code or session is None or client is None or refresh is None:
            raise PermissionDenied("OSDK OAuth authorization code is invalid")
        access = self.oauth_token_issuer.issue_access_token(_claims(session), ttl_seconds=_access_ttl(client))
        return _token_response(access, refresh)

    def refresh_access_token(self, *, ctx: RequestContext | None = None, refresh_token: str) -> RuntimeJsonObject:
        ctx = ctx or RequestContext()
        now = _now()
        invalid_refresh = False
        session: Mapping[str, object] | None = None
        new_refresh: RuntimeJsonObject | None = None
        with self.engine.begin() as conn:
            old_token = self._refresh_token_for_use(conn, ctx, refresh_token, now)
            if old_token is None:
                invalid_refresh = True
            else:
                session = self._require_session(conn, ctx, cast(str, old_token["session_id"]), now)
                self._rate_limiter.check(ctx, "refresh", cast(str, session["client_id"]))
                new_refresh = self._create_refresh_token(conn, ctx, cast(str, session["id"]), session, now)
                rotated = self.oauth_session_repository.rotate_refresh_token(
                    transaction=conn,
                    tenant_id=ctx.tenant_id,
                    token_id=cast(str, old_token["id"]),
                    replacement_token_id=cast(str, new_refresh["refreshTokenId"]),
                    used_at=now,
                )
                if not rotated:
                    # A concurrent refresh already rotated this token: the plain
                    # SELECT above read it as active before the winner committed.
                    # Abort so the just-inserted replacement token is rolled back
                    # rather than left as a second live refresh token for the
                    # session, which would also evade reuse detection.
                    raise ConflictDetected("OSDK OAuth refresh token was already rotated")
                self.oauth_session_repository.update_session_refresh(
                    transaction=conn, tenant_id=ctx.tenant_id, session_id=cast(str, session["id"]), refreshed_at=now
                )
                self._audit(conn, ctx, "osdk.oauth.refresh_rotated", session, safe_session_ref(session))
        if invalid_refresh or session is None or new_refresh is None:
            raise PermissionDenied("OSDK OAuth refresh token is invalid")
        claims = _claims(session, roles=_reevaluated_roles(session, ctx))
        access = self.oauth_token_issuer.issue_access_token(claims, ttl_seconds=_access_ttl(session))
        return _token_response(access, new_refresh)

    def revoke(self, *, ctx: RequestContext | None = None, refresh_token: str) -> RuntimeJsonObject:
        ctx = ctx or RequestContext()
        now = _now()
        invalid_refresh = False
        session: Mapping[str, object] | None = None
        with self.engine.begin() as conn:
            token = self._refresh_token_for_use(conn, ctx, refresh_token, now)
            if token is None:
                invalid_refresh = True
            else:
                session = self._require_session(conn, ctx, cast(str, token["session_id"]), now)
                self._rate_limiter.check(ctx, "revoke", cast(str, session["client_id"]))
                self.oauth_session_repository.revoke_refresh_token(
                    transaction=conn, tenant_id=ctx.tenant_id, token_id=cast(str, token["id"]), revoked_at=now
                )
                self.oauth_session_repository.revoke_session(
                    transaction=conn, tenant_id=ctx.tenant_id, session_id=cast(str, session["id"]), revoked_at=now
                )
                self._audit(conn, ctx, "osdk.oauth.session.revoked", session, safe_session_ref(session))
        if invalid_refresh or session is None:
            raise PermissionDenied("OSDK OAuth refresh token is invalid")
        return {"status": "revoked", "sessionId": session["id"]}

    def _require_active_client(
        self, conn: TransactionContext, ctx: RequestContext, client_id: str, redirect_uri: str
    ) -> Mapping[str, object]:
        client = self.osdk_application_repository.active_client(
            transaction=conn, tenant_id=ctx.tenant_id, client_id=client_id
        )
        if client is None:
            raise PermissionDenied("OSDK OAuth client is not active")
        app = self.osdk_application_repository.application_by_id(
            transaction=conn, tenant_id=ctx.tenant_id, app_id=client["app_id"]
        )
        if app is None or app["status"] != "active":
            raise PermissionDenied("OSDK OAuth application is not active")
        _require_redirect_uri(client, redirect_uri)
        return client

    def _authorized_scopes(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        app_id: str,
        client: Mapping[str, object],
        requested_scopes: Sequence[str],
    ) -> tuple[str, ...]:
        resource_scopes = _app_resource_scopes(
            self.osdk_application_repository.resources_for_application(
                transaction=conn, tenant_id=ctx.tenant_id, app_id=app_id
            )
        )
        client_scopes = _client_allowed_scopes(client) or resource_scopes
        requested = tuple(requested_scopes) or tuple(sorted(client_scopes))
        if not set(requested).issubset(client_scopes) or not set(requested).issubset(resource_scopes):
            raise PermissionDenied("OSDK OAuth requested scope is not granted")
        return requested

    def _exchangeable_code(
        self, conn: TransactionContext, ctx: RequestContext, raw_code: str, now: str
    ) -> OAuthAuthorizationCodeRow | None:
        row = self.oauth_session_repository.authorization_code_by_hash(
            transaction=conn, tenant_id=ctx.tenant_id, code_hash=_hash_secret(raw_code)
        )
        if row is not None and row["consumed_at"] is not None:
            self._audit_denied_code_replay(conn, ctx, row)
        if row is None or row["consumed_at"] is not None or _is_expired(row["expires_at"], now):
            return None
        return row

    def _validate_code_exchange(
        self, row: OAuthAuthorizationCodeRow, client_id: str, redirect_uri: str, code_verifier: str
    ) -> None:
        if row["client_id"] != client_id or row["redirect_uri"] != redirect_uri:
            raise PermissionDenied("OSDK OAuth authorization code does not match client")
        if not _pkce_matches(row["code_challenge_method"], row["code_challenge"], code_verifier):
            raise PermissionDenied("OSDK OAuth PKCE verification failed")

    def _create_session(
        self, conn: TransactionContext, code_row: OAuthAuthorizationCodeRow, client: Mapping[str, object], now: str
    ) -> Mapping[str, object]:
        record = OAuthSessionRecord(
            session_id=_new_id("osdk_oauth_session"),
            tenant_id=code_row["tenant_id"],
            app_id=code_row["app_id"],
            client_id=code_row["client_id"],
            actor_user_id=code_row["actor_user_id"],
            roles=tuple(code_row["roles"]),
            scopes=tuple(code_row["scopes"]),
            status="active",
            created_at=now,
            expires_at=_expires_at(now, _refresh_ttl(client)),
        )
        return self.oauth_session_repository.insert_session(transaction=conn, record=record)

    def _create_refresh_token(
        self, conn: TransactionContext, ctx: RequestContext, session_id: str, source: Mapping[str, object], now: str
    ) -> RuntimeJsonObject:
        raw_token = _raw_token("refresh")
        record = OAuthRefreshTokenRecord(
            token_id=_new_id("osdk_refresh"),
            tenant_id=ctx.tenant_id,
            session_id=session_id,
            token_hash=_hash_secret(raw_token),
            status="active",
            expires_at=_expires_at(now, _refresh_ttl(source)),
            created_at=now,
        )
        row = self.oauth_session_repository.insert_refresh_token(transaction=conn, record=record)
        return {"refreshToken": raw_token, "refreshTokenId": row["id"], "refreshExpiresAt": row["expires_at"]}

    def _refresh_token_for_use(
        self, conn: TransactionContext, ctx: RequestContext, raw_token: str, now: str
    ) -> Mapping[str, object] | None:
        row = self.oauth_session_repository.refresh_token_by_hash(
            transaction=conn, tenant_id=ctx.tenant_id, token_hash=_hash_secret(raw_token)
        )
        if row is not None and row["status"] == "rotated":
            self._compromise_refresh_family(conn, ctx, row["session_id"], now)
        if row is None or row["status"] != "active" or _is_expired(row["expires_at"], now):
            return None
        return row

    def _require_session(
        self, conn: TransactionContext, ctx: RequestContext, session_id: str, now: str
    ) -> Mapping[str, object]:
        row = self.oauth_session_repository.session_by_id(
            transaction=conn, tenant_id=ctx.tenant_id, session_id=session_id
        )
        if row is None or row["status"] != "active" or _is_expired(row["expires_at"], now):
            raise PermissionDenied("OSDK OAuth session is invalid")
        return row

    def _compromise_refresh_family(
        self, conn: TransactionContext, ctx: RequestContext, session_id: str, compromised_at: str
    ) -> None:
        self.oauth_session_repository.compromise_refresh_token_family(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            session_id=session_id,
            compromised_at=compromised_at,
            reason=_REFRESH_REUSE_REASON,
        )
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="osdk.oauth.refresh_family_compromised",
            resource_type="osdk_oauth_session",
            resource_id=session_id,
            action="osdk.oauth.refresh_family_compromised",
            decision="deny",
            after_ref={"sessionId": session_id, "reason": _REFRESH_REUSE_REASON},
        )

    def _audit_denied_code_replay(
        self, conn: TransactionContext, ctx: RequestContext, row: OAuthAuthorizationCodeRow
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="osdk.oauth.authorization_code_replay_detected",
            resource_type="osdk_oauth_session",
            resource_id=row["id"],
            action="osdk.oauth.authorization_code_replay_detected",
            decision="deny",
            after_ref={"authorizationCodeId": row["id"], "clientId": row["client_id"]},
        )

    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event_type: str,
        row: Mapping[str, object],
        ref: Mapping[str, object],
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type=event_type,
            resource_type="osdk_oauth_session",
            resource_id=cast(str, row["id"]),
            action=event_type,
            after_ref=ref,
        )


def _authorization_code_record(
    ctx: RequestContext,
    client: Mapping[str, object],
    raw_code: str,
    redirect_uri: str,
    code_challenge: str,
    method: str,
    scopes: tuple[str, ...],
) -> OAuthAuthorizationCodeRecord:
    now = _now()
    return OAuthAuthorizationCodeRecord(
        code_id=_new_id("osdk_code"),
        tenant_id=ctx.tenant_id,
        code_hash=_hash_secret(raw_code),
        app_id=cast(str, client["app_id"]),
        client_id=cast(str, client["client_id"]),
        actor_user_id=ctx.actor_user_id,
        roles=ctx.roles,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=method,
        scopes=scopes,
        expires_at=_expires_at(now, _DEFAULT_CODE_TTL_SECONDS),
        created_at=now,
    )


def _token_response(access: RuntimeJsonObject, refresh: RuntimeJsonObject) -> RuntimeJsonObject:
    return {
        **access,
        "refreshToken": refresh["refreshToken"],
        "refreshExpiresAt": refresh["refreshExpiresAt"],
        "sessionId": cast(RuntimeJsonObject, access["claims"])["sid"],
    }


def _claims(session: Mapping[str, object], roles: Sequence[str] | None = None) -> OAuthAccessTokenClaims:
    session_roles = [str(role) for role in cast(Sequence[object], session["roles"])]
    return {
        "tenant_id": cast(str, session["tenant_id"]),
        "actor_user_id": cast(str, session["actor_user_id"]),
        "roles": session_roles if roles is None else list(roles),
        "application_id": cast(str, session["app_id"]),
        "client_id": cast(str, session["client_id"]),
        "scopes": [str(scope) for scope in cast(Sequence[object], session["scopes"])],
        "session_id": cast(str, session["id"]),
    }


def _reevaluated_roles(session: Mapping[str, object], ctx: RequestContext) -> list[str]:
    """Re-evaluate roles at refresh time so a revoked role stops minting privileged tokens.

    Roles are the caller's current roles (from ``ctx``) intersected with the roles originally
    granted to the session. The intersection drops roles revoked since authorization while never
    escalating beyond the originally consented set.
    """
    granted = [str(role) for role in cast(Sequence[object], session["roles"])]
    current = set(ctx.roles)
    return [role for role in granted if role in current]


def _require_redirect_uri(client: Mapping[str, object], redirect_uri: str) -> None:
    allowed = tuple(str(item) for item in cast(Sequence[object] | None, client.get("redirect_uris")) or ())
    if not allowed:
        raise PermissionDenied("OSDK OAuth client has no registered redirect URIs")
    if redirect_uri not in allowed:
        raise PermissionDenied("OSDK OAuth redirect URI is not registered")


def _client_allowed_scopes(client: Mapping[str, object]) -> set[str]:
    raw = cast(Sequence[object] | None, client.get("allowed_scopes")) or ()
    return {str(scope) for scope in raw if str(scope)}


def _app_resource_scopes(resources: Sequence[Mapping[str, object]]) -> set[str]:
    scopes: set[str] = set()
    for row in resources:
        scopes.update(str(scope) for scope in cast(Sequence[object], row["scopes"]))
    return scopes


def _pkce_method(method: str) -> str:
    if method not in _PKCE_METHODS:
        raise ValidationFailed("OSDK OAuth code_challenge_method must be S256")
    return method


def _require_valid_code_challenge(code_challenge: str) -> None:
    length = len(code_challenge)
    if (
        length < _CODE_CHALLENGE_MIN_LENGTH
        or length > _CODE_CHALLENGE_MAX_LENGTH
        or _CODE_CHALLENGE_PATTERN.fullmatch(code_challenge) is None
    ):
        raise ValidationFailed("OSDK OAuth code_challenge must be 43-128 base64url characters")


def _pkce_matches(method: str, challenge: str, verifier: str) -> bool:
    if method != "S256":
        return False
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(challenge, encoded)


def _raw_token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def _hash_secret(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _expires_at(now: str, seconds: int) -> str:
    return (datetime.fromisoformat(now) + timedelta(seconds=seconds)).isoformat()


def _is_expired(expires_at: str, now: str) -> bool:
    return datetime.fromisoformat(expires_at) <= datetime.fromisoformat(now)


def _access_ttl(source: Mapping[str, object]) -> int:
    return _positive_int(source.get("access_token_ttl_seconds"), _DEFAULT_ACCESS_TTL_SECONDS)


def _refresh_ttl(source: Mapping[str, object]) -> int:
    return _positive_int(source.get("refresh_token_ttl_seconds"), _DEFAULT_REFRESH_TTL_SECONDS)


def _positive_int(value: object, fallback: int) -> int:
    return value if isinstance(value, int) and value > 0 else fallback


def _authorization_response(
    raw_code: str, redirect_uri: str, state: str | None, created_at: str, expires_at: str
) -> RuntimeJsonObject:
    return {
        "code": raw_code,
        "redirectUri": redirect_uri,
        "redirectTo": _redirect_with_code(redirect_uri, raw_code, state),
        "state": state,
        "createdAt": created_at,
        "expiresAt": expires_at,
    }


def _redirect_with_code(redirect_uri: str, code: str, state: str | None) -> str:
    separator = "&" if "?" in redirect_uri else "?"
    state_part = f"&state={state}" if state else ""
    return f"{redirect_uri}{separator}code={code}{state_part}"
