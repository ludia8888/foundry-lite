"""Confidential OSDK client-credentials grant for service principals."""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import cast

from foundry_lite.application.ports import OAuthAccessTokenClaims, OAuthSessionRecord, RuntimeJsonObject
from foundry_lite.application.ports.osdk_security_repository import OsdkClientSecretVersionRow
from foundry_lite.application.ports.secret_provider import SecretValue
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.osdk_oauth_session_support import _OAuthAuditBoundary, _OAuthRateLimiter
from foundry_lite.application.services.osdk_service_principal_authorization import OSDK_SERVICE_PRINCIPAL_ROLE
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, PermissionDenied

_DEFAULT_ACCESS_TTL_SECONDS = 120
_MAX_ACCESS_TTL_SECONDS = 900


class OsdkOAuthClientCredentialsService(CoreService):
    """Mint short-lived, app-restricted tokens without storing raw secrets."""

    required_dependencies = (
        "engine",
        "osdk_application_repository",
        "oauth_session_repository",
        "oauth_token_issuer",
        "secret_provider",
    )
    required_collaborators = ("runtime_service",)
    runtime_service: _OAuthAuditBoundary

    def __init__(self, **dependencies: object) -> None:
        super().__init__(**dependencies)
        self._rate_limiter = _OAuthRateLimiter()

    def exchange(
        self,
        *,
        client_id: str,
        client_secret: str,
        scopes: Sequence[str] = (),
        resource: str | None = None,
        ctx: RequestContext | None = None,
    ) -> RuntimeJsonObject:
        request_ctx = ctx or RequestContext()
        self._rate_limiter.check(request_ctx, "client_credentials", client_id)
        with self.engine.begin() as conn:
            client = self._require_machine_client(conn, request_ctx, client_id)
            secret_row, resolved_secret = self._verified_secret(conn, request_ctx, client, client_secret)
            granted_scopes = self._authorized_scopes(conn, request_ctx, client, scopes)
            self.osdk_application_repository.mark_client_secret_used(
                transaction=conn,
                tenant_id=request_ctx.tenant_id,
                secret_id=secret_row["id"],
                used_at=_now(),
            )
            service_ctx = _service_context(request_ctx, client, granted_scopes)
            session = self._create_session(conn, service_ctx, client)
            self._audit_issued(conn, service_ctx, session, resolved_secret.version)
        claims = _service_claims(session, resource=resource)
        access = self.oauth_token_issuer.issue_access_token(claims, ttl_seconds=_access_ttl(client))
        return {**access, "sessionId": session["id"], "grantType": "client_credentials"}

    def verify_client_credentials(self, client_id: str, client_secret: str, ctx: RequestContext) -> None:
        """Authenticate a confidential client without minting or mutating token state."""

        self._rate_limiter.check(ctx, "refresh_client_auth", client_id)
        with self.engine.begin() as conn:
            client = self._require_machine_client(conn, ctx, client_id)
            self._verified_secret(conn, ctx, client, client_secret)

    def _verified_secret(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        client: Mapping[str, object],
        client_secret: str,
    ) -> tuple[OsdkClientSecretVersionRow, SecretValue]:
        row = self.osdk_application_repository.current_client_secret(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            client_row_id=cast(str, client["id"]),
        )
        resolved = self._resolved_secret(row)
        if row is None or resolved is None or not secrets.compare_digest(resolved.value, client_secret):
            raise PermissionDenied("OSDK OAuth client authentication is invalid")
        return row, resolved

    def _resolved_secret(self, secret_row: Mapping[str, object] | None) -> SecretValue | None:
        if secret_row is None:
            return None
        try:
            return self.secret_provider.get_secret(
                cast(str, secret_row["vault_secret_name"]),
                version=cast(str, secret_row["vault_secret_version"]),
            )
        except FoundryLiteError:
            return None

    def _require_machine_client(
        self, conn: TransactionContext, ctx: RequestContext, client_id: str
    ) -> Mapping[str, object]:
        client = self.osdk_application_repository.active_client_for_update(
            transaction=conn, tenant_id=ctx.tenant_id, client_id=client_id
        )
        if client is None or _string_sequence(client.get("redirect_uris")):
            raise PermissionDenied("OSDK OAuth client authentication is invalid")
        app = self.osdk_application_repository.application_by_id(
            transaction=conn, tenant_id=ctx.tenant_id, app_id=client["app_id"]
        )
        if app is None or app["status"] != "active":
            raise PermissionDenied("OSDK OAuth client authentication is invalid")
        return client

    def _authorized_scopes(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        client: Mapping[str, object],
        requested_scopes: Sequence[str],
    ) -> tuple[str, ...]:
        resources = self.osdk_application_repository.resources_for_application(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            app_id=cast(str, client["app_id"]),
        )
        resource_scopes = _resource_scopes(resources)
        client_scopes = set(_string_sequence(client.get("allowed_scopes"))) or resource_scopes
        requested = tuple(dict.fromkeys(requested_scopes)) or tuple(sorted(client_scopes))
        if not set(requested).issubset(client_scopes & resource_scopes):
            raise PermissionDenied("OSDK OAuth requested scope is not granted")
        return requested

    def _create_session(
        self, conn: TransactionContext, ctx: RequestContext, client: Mapping[str, object]
    ) -> Mapping[str, object]:
        now = _now()
        record = OAuthSessionRecord(
            session_id=_new_id("osdk_service_session"),
            tenant_id=ctx.tenant_id,
            app_id=cast(str, client["app_id"]),
            client_id=cast(str, client["client_id"]),
            actor_user_id=ctx.actor_user_id,
            roles=ctx.roles,
            scopes=ctx.token_scopes,
            status="active",
            created_at=now,
            expires_at=(datetime.fromisoformat(now) + timedelta(seconds=_access_ttl(client))).isoformat(),
        )
        return self.oauth_session_repository.insert_session(transaction=conn, record=record)

    def _audit_issued(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        session: Mapping[str, object],
        secret_version: str,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="osdk.oauth.client_credentials.issued",
            resource_type="osdk_oauth_session",
            resource_id=cast(str, session["id"]),
            action="osdk.oauth.client_credentials.issued",
            after_ref={
                "sessionId": session["id"],
                "appId": session["app_id"],
                "clientId": session["client_id"],
                "scopes": cast(Sequence[object], session["scopes"]),
                "secretVersion": secret_version,
            },
        )


def client_credentials_secret_name(tenant_id: str, client_id: str) -> str:
    """Return the stable vault coordinate operators use to provision a client."""

    return _secret_name(tenant_id, client_id)


def _secret_name(tenant_id: str, client_id: str) -> str:
    return f"osdk_client_credentials/{tenant_id}/{client_id}"


def _service_context(
    request_ctx: RequestContext, client: Mapping[str, object], scopes: tuple[str, ...]
) -> RequestContext:
    client_id = cast(str, client["client_id"])
    return RequestContext(
        tenant_id=request_ctx.tenant_id,
        actor_user_id=f"service-principal:{client_id}",
        request_id=request_ctx.request_id,
        roles=(OSDK_SERVICE_PRINCIPAL_ROLE,),
        application_id=cast(str, client["app_id"]),
        client_id=client_id,
        token_scopes=scopes,
    )


def _service_claims(session: Mapping[str, object], *, resource: str | None = None) -> OAuthAccessTokenClaims:
    claims: OAuthAccessTokenClaims = {
        "tenant_id": cast(str, session["tenant_id"]),
        "actor_user_id": cast(str, session["actor_user_id"]),
        "roles": list(_string_sequence(session["roles"])),
        "application_id": cast(str, session["app_id"]),
        "client_id": cast(str, session["client_id"]),
        "scopes": list(_string_sequence(session["scopes"])),
        "session_id": cast(str, session["id"]),
    }
    if resource is not None:
        claims["resource"] = resource
    return claims


def _resource_scopes(resources: Sequence[Mapping[str, object]]) -> set[str]:
    result: set[str] = set()
    for resource in resources:
        result.update(_string_sequence(resource.get("scopes")))
    return result


def _string_sequence(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    return tuple(str(item) for item in value if str(item))


def _access_ttl(client: Mapping[str, object]) -> int:
    value = client.get("access_token_ttl_seconds")
    requested = value if isinstance(value, int) and value > 0 else _DEFAULT_ACCESS_TTL_SECONDS
    return min(requested, _MAX_ACCESS_TTL_SECONDS)
