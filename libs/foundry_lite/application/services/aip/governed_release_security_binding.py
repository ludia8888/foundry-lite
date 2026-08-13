"""Immutable OAuth and request identity binding for Governed Release."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from foundry_lite.application.services.aip.fde_tool_result import hash_json
from foundry_lite.application.services.aip.governed_release_authorization import GOVERNED_RELEASE_SCOPE
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed

JsonObject = Mapping[str, object]


@dataclass(frozen=True)
class GovernedReleaseBinding:
    """Immutable identity and request facts authorized by the embedded widget."""

    tenant_id: str
    actor_user_id: str
    application_id: str
    client_id: str
    oauth_session_hash: str
    oauth_session_authority: str
    authorization_server_issuer: str
    oauth_grant_type: str
    oauth_resource: str
    is_human_oauth: bool
    required_oauth_scope: str
    oauth_token_issued_at: int
    oauth_token_expires_at: int
    session_id: str
    tool_name: str
    arguments_hash: str
    idempotency_key: str
    required_permission: str
    origin: str
    release_kind: str
    proposal_id: str | None

    @property
    def payload(self) -> dict[str, object]:
        """Return the complete request evidence, including its transport session."""

        return {
            "tenantId": self.tenant_id,
            "actorUserId": self.actor_user_id,
            "applicationId": self.application_id,
            "clientId": self.client_id,
            "oauthSessionHash": self.oauth_session_hash,
            "oauthSessionAuthority": self.oauth_session_authority,
            "authorizationServerIssuer": self.authorization_server_issuer,
            "oauthGrantType": self.oauth_grant_type,
            "oauthResource": self.oauth_resource,
            "isHuman": self.is_human_oauth,
            "requiredScope": self.required_oauth_scope,
            "oauthTokenIssuedAt": self.oauth_token_issued_at,
            "oauthTokenExpiresAt": self.oauth_token_expires_at,
            "sessionId": self.session_id,
            "toolName": self.tool_name,
            "argumentsHash": self.arguments_hash,
            "idempotencyKey": self.idempotency_key,
            "requiredPermission": self.required_permission,
            "origin": self.origin,
            "releaseKind": self.release_kind,
            "proposalId": self.proposal_id,
        }

    @property
    def authorization_payload(self) -> dict[str, object]:
        """Return stable authorization facts shared by prepare and action calls.

        MCP Apps hosts may issue the widget preparation and the confirmed tool call
        through different Streamable HTTP sessions and freshly rotated access tokens.
        The transport session and verified token window remain in ``payload`` and the
        durable run record for audit, but neither identifies the human OAuth grant.
        The stable OAuth session hash, issuer, audience, client, actor, and scope still
        bind the one-time confirmation to the exact authorization grant.
        """

        volatile_transport_facts = {
            "sessionId",
            "oauthTokenIssuedAt",
            "oauthTokenExpiresAt",
        }
        return {key: value for key, value in self.payload.items() if key not in volatile_transport_facts}

    @property
    def fingerprint(self) -> str:
        return hash_json(self.authorization_payload)


def release_binding(
    ctx: RequestContext,
    *,
    application_id: str,
    session_id: str,
    tool_name: str,
    arguments: JsonObject,
    required_permission: str,
    origin: str | None,
) -> GovernedReleaseBinding:
    require_human_app_principal(ctx, application_id)
    release_kind, proposal_id = _release_coordinates(tool_name, arguments)
    return GovernedReleaseBinding(
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.actor_user_id,
        application_id=application_id,
        client_id=str(ctx.client_id),
        oauth_session_hash=str(ctx.oauth_session_hash),
        oauth_session_authority=str(ctx.oauth_session_authority),
        authorization_server_issuer=str(ctx.authorization_server_issuer),
        oauth_grant_type=str(ctx.oauth_grant_type),
        oauth_resource=str(ctx.oauth_resource),
        is_human_oauth=ctx.is_human_oauth is True,
        required_oauth_scope=GOVERNED_RELEASE_SCOPE,
        oauth_token_issued_at=cast(int, ctx.oauth_token_issued_at),
        oauth_token_expires_at=cast(int, ctx.oauth_token_expires_at),
        session_id=session_id,
        tool_name=tool_name,
        arguments_hash=hash_json(arguments),
        idempotency_key=_idempotency_key(arguments),
        required_permission=required_permission,
        origin=origin or "no-origin",
        release_kind=release_kind,
        proposal_id=proposal_id,
    )


def _release_coordinates(tool_name: str, arguments: JsonObject) -> tuple[str, str | None]:
    if tool_name == "verify_release_completion":
        return "combined", None
    kind = arguments.get("releaseKind")
    if kind not in {"ontology", "pipeline"}:
        raise ValidationFailed("releaseKind must be ontology or pipeline")
    proposal_id = arguments.get("proposalId")
    if tool_name == "create_release_branch":
        return str(kind), None
    if not isinstance(proposal_id, str) or not proposal_id.strip():
        raise ValidationFailed("proposalId is required for governed release actions")
    return str(kind), proposal_id.strip()


def require_human_app_principal(ctx: RequestContext, application_id: str) -> None:
    if _is_machine_principal(ctx) or not _has_human_oauth_app(ctx, application_id):
        raise PermissionDenied(
            "Governed Release MCP requires an authorization-code human application principal",
            details={"reason": "human_oauth_app_principal_required"},
        )


def _is_machine_principal(ctx: RequestContext) -> bool:
    return ctx.actor_user_id.startswith(("service-principal:", "service-account:")) or (
        "osdk_service_principal" in ctx.roles
    )


def _has_human_oauth_app(ctx: RequestContext, application_id: str) -> bool:
    return all(
        (
            ctx.application_id == application_id,
            ctx.client_id,
            ctx.oauth_session_id,
            ctx.oauth_session_hash,
            ctx.oauth_session_authority in {"local", "issuer"},
            ctx.authorization_server_issuer,
            ctx.oauth_grant_type == "authorization_code",
            ctx.oauth_resource,
            ctx.is_human_oauth is True,
            ctx.token_scopes,
            _has_valid_token_window(ctx),
        )
    )


def _has_valid_token_window(ctx: RequestContext) -> bool:
    issued_at = ctx.oauth_token_issued_at
    expires_at = ctx.oauth_token_expires_at
    return isinstance(issued_at, int) and isinstance(expires_at, int) and expires_at > issued_at


def _idempotency_key(arguments: JsonObject) -> str:
    value = arguments.get("idempotencyKey")
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed("idempotencyKey is required for governed release actions")
    return value.strip()


__all__ = [
    "GovernedReleaseBinding",
    "release_binding",
    "require_human_app_principal",
]
