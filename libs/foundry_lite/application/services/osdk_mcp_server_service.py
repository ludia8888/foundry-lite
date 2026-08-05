"""Developer Console configuration and tenant MCP Hub for Ontology MCP."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports import RuntimeJsonObject, TransactionContext
from foundry_lite.application.ports.osdk_application_repository import (
    OsdkMcpServerRecord,
    OsdkMcpServerRow,
    OsdkMcpSessionEventRow,
    OsdkMcpSessionRecord,
    OsdkMcpSessionRow,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.osdk_application_idempotency import OsdkApplicationIdempotencyService
from foundry_lite.application.services.osdk_application_records import _require_idempotency_key
from foundry_lite.application.services.osdk_application_scope import OsdkApplicationScopeService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, PermissionDenied, ValidationFailed

_MCP_STATUSES = frozenset({"enabled", "disabled"})
_MAX_DESCRIPTION_LENGTH = 8_000
_SESSION_ID_PATTERN = re.compile(r"ontology-mcp-[A-Za-z0-9_-]{8,240}")


class OsdkMcpServerService(CoreService):
    """Own safe-by-default MCP publication and discoverability state."""

    required_dependencies = ("engine", "policy", "osdk_application_repository")
    required_collaborators = ("osdk_application_idempotency_service", "osdk_application_scope_service")
    osdk_application_idempotency_service: OsdkApplicationIdempotencyService
    osdk_application_scope_service: OsdkApplicationScopeService

    def configure(
        self,
        app_id: str,
        *,
        status: str,
        description_markdown: str,
        allowed_origins: Sequence[str] = (),
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> OsdkMcpServerRow:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "developer_console:manage")
        _require_idempotency_key(idempotency_key)
        normalized = _configuration_request(app_id, status, description_markdown, allowed_origins)
        with self.engine.begin() as conn:
            response = self.osdk_application_idempotency_service._idempotent_response(
                conn,
                ctx,
                "osdk.mcp_server.configure",
                idempotency_key,
                normalized,
                lambda: self._configure_json(conn, ctx, normalized),
            )
        return cast(OsdkMcpServerRow, response)

    def get(self, app_id: str, *, ctx: RequestContext | None = None) -> OsdkMcpServerRow:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "developer_console:read")
        with self.engine.begin() as conn:
            self.osdk_application_scope_service._require_application(conn, ctx, app_id)
            return self._required_server(conn, ctx, app_id)

    def list_hub(self, *, ctx: RequestContext | None = None) -> list[RuntimeJsonObject]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "developer_console:read")
        with self.engine.begin() as conn:
            servers = self.osdk_application_repository.list_mcp_servers(transaction=conn, tenant_id=ctx.tenant_id)
            return [self._hub_item(conn, ctx, server) for server in servers]

    def require_enabled(self, ctx: RequestContext, app_id: str, *, origin: str | None = None) -> None:
        if ctx.application_id != app_id:
            raise PermissionDenied("Ontology MCP application does not match the calling principal")
        with self.engine.begin() as conn:
            server = self.osdk_application_repository.mcp_server_for_application(
                transaction=conn, tenant_id=ctx.tenant_id, app_id=app_id
            )
        if server is None or server["status"] != "enabled":
            raise PermissionDenied("Ontology MCP server is not enabled")
        if origin is not None and origin not in server["allowed_origins"]:
            raise PermissionDenied("Ontology MCP Origin is not granted by the application")

    def open_session(
        self, ctx: RequestContext, app_id: str, session_id: str, *, origin: str | None = None
    ) -> OsdkMcpSessionRow:
        self.require_enabled(ctx, app_id, origin=origin)
        _require_session_identity(ctx, session_id)
        now = _now()
        record = _session_record(ctx, app_id, session_id, now)
        with self.engine.begin() as conn:
            existing = self.osdk_application_repository.insert_mcp_session_or_get_existing(
                transaction=conn, record=record
            )
            row = existing or self._required_session(conn, ctx, session_id)
            _require_session_owner(ctx, app_id, row)
            self.osdk_application_repository.touch_mcp_server_activity(
                transaction=conn, tenant_id=ctx.tenant_id, app_id=app_id, observed_at=now
            )
            if existing is None:
                self._append_session_event(conn, ctx, row, "session.ready", {"applicationId": app_id})
                self.osdk_application_scope_service._audit(conn, ctx, "osdk.mcp_session.opened", row)
            return row

    def record_session_event(
        self,
        ctx: RequestContext,
        app_id: str,
        session_id: str,
        *,
        event_type: str,
        payload: RuntimeJsonObject,
    ) -> OsdkMcpSessionEventRow:
        with self.engine.begin() as conn:
            row = self._required_session(conn, ctx, session_id)
            _require_session_owner(ctx, app_id, row)
            return self._append_session_event(conn, ctx, row, event_type, payload)

    def list_session_events(
        self, ctx: RequestContext, app_id: str, session_id: str, *, after_sequence: int = 0
    ) -> list[OsdkMcpSessionEventRow]:
        with self.engine.begin() as conn:
            row = self._required_session(conn, ctx, session_id)
            _require_session_owner(ctx, app_id, row)
            return self.osdk_application_repository.mcp_session_events_after(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                session_id=session_id,
                after_sequence=max(0, after_sequence),
            )

    def close_session(self, ctx: RequestContext, app_id: str, session_id: str) -> OsdkMcpSessionRow:
        with self.engine.begin() as conn:
            before = self._required_session(conn, ctx, session_id)
            _require_session_owner(ctx, app_id, before, allow_terminated=True)
            row = self.osdk_application_repository.terminate_mcp_session(
                transaction=conn, tenant_id=ctx.tenant_id, session_id=session_id, terminated_at=_now()
            )
            if row is None:
                raise PermissionDenied("Ontology MCP session is invalid")
            if before["status"] == "active":
                self.osdk_application_scope_service._audit(conn, ctx, "osdk.mcp_session.terminated", row, before, row)
            return row

    def _configure_json(
        self, conn: TransactionContext, ctx: RequestContext, request: RuntimeJsonObject
    ) -> RuntimeJsonObject:
        app_id = cast(str, request["appId"])
        self.osdk_application_scope_service._require_application(conn, ctx, app_id)
        existing = self.osdk_application_repository.mcp_server_for_application(
            transaction=conn, tenant_id=ctx.tenant_id, app_id=app_id
        )
        now = _now()
        record = _server_record(ctx, request, existing, now)
        updated = self.osdk_application_repository.upsert_mcp_server(transaction=conn, record=record)
        self.osdk_application_scope_service._audit(
            conn,
            ctx,
            "osdk.mcp_server.configured",
            updated,
            existing,
            updated,
        )
        return cast(RuntimeJsonObject, updated)

    def _required_server(self, conn: TransactionContext, ctx: RequestContext, app_id: str) -> OsdkMcpServerRow:
        row = self.osdk_application_repository.mcp_server_for_application(
            transaction=conn, tenant_id=ctx.tenant_id, app_id=app_id
        )
        if row is None:
            raise NotFound("Ontology MCP server configuration not found", details={"applicationId": app_id})
        return row

    def _hub_item(self, conn: TransactionContext, ctx: RequestContext, server: OsdkMcpServerRow) -> RuntimeJsonObject:
        app = self.osdk_application_scope_service._require_application(conn, ctx, server["app_id"])
        resources = self.osdk_application_repository.resources_for_application(
            transaction=conn, tenant_id=ctx.tenant_id, app_id=server["app_id"]
        )
        return {
            "applicationId": server["app_id"],
            "applicationApiName": app["app_api_name"],
            "displayName": app["display_name"],
            "status": server["status"],
            "descriptionMarkdown": server["description_markdown"],
            "endpointPath": f"/mcp/ontology/{server['app_id']}",
            "resourceCount": len(resources),
            "authModes": ["authorization_code_pkce", "client_credentials"],
            "allowedOrigins": server["allowed_origins"],
            "lastActivityAt": server["last_activity_at"],
            "updatedAt": server["updated_at"],
        }

    def _required_session(self, conn: TransactionContext, ctx: RequestContext, session_id: str) -> OsdkMcpSessionRow:
        row = self.osdk_application_repository.mcp_session_by_id(
            transaction=conn, tenant_id=ctx.tenant_id, session_id=session_id
        )
        if row is None:
            raise PermissionDenied("Ontology MCP session is invalid")
        return row

    def _append_session_event(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        session: OsdkMcpSessionRow,
        event_type: str,
        payload: RuntimeJsonObject,
    ) -> OsdkMcpSessionEventRow:
        row = self.osdk_application_repository.append_mcp_session_event(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            session_id=session["id"],
            event_type=event_type,
            payload=payload,
            created_at=_now(),
        )
        if row is None:
            raise PermissionDenied("Ontology MCP session is not active")
        return row


def _configuration_request(
    app_id: str, status: str, description_markdown: str, allowed_origins: Sequence[str]
) -> RuntimeJsonObject:
    if status not in _MCP_STATUSES:
        raise ValidationFailed("Ontology MCP status must be enabled or disabled")
    description = description_markdown.strip()
    if not description or len(description) > _MAX_DESCRIPTION_LENGTH:
        raise ValidationFailed("Ontology MCP description must be 1-8000 characters")
    origins = tuple(dict.fromkeys(origin.strip() for origin in allowed_origins if origin.strip()))
    if any(not _safe_origin(origin) for origin in origins):
        raise ValidationFailed("Ontology MCP allowed origins must use https or localhost http")
    return {"appId": app_id, "status": status, "descriptionMarkdown": description, "allowedOrigins": list(origins)}


def _server_record(
    ctx: RequestContext, request: Mapping[str, object], existing: OsdkMcpServerRow | None, now: str
) -> OsdkMcpServerRecord:
    return OsdkMcpServerRecord(
        server_id=existing["id"] if existing else _new_id("osdk_mcp_server"),
        tenant_id=ctx.tenant_id,
        app_id=cast(str, request["appId"]),
        status=cast(str, request["status"]),
        description_markdown=cast(str, request["descriptionMarkdown"]),
        allowed_origins=tuple(str(value) for value in cast(Sequence[object], request["allowedOrigins"])),
        last_activity_at=existing["last_activity_at"] if existing else None,
        updated_by_user_id=ctx.actor_user_id,
        created_at=existing["created_at"] if existing else now,
        updated_at=now,
    )


def _session_record(ctx: RequestContext, app_id: str, session_id: str, now: str) -> OsdkMcpSessionRecord:
    return OsdkMcpSessionRecord(
        session_id=session_id,
        tenant_id=ctx.tenant_id,
        app_id=app_id,
        client_id=ctx.client_id or "",
        actor_user_id=ctx.actor_user_id,
        status="active",
        created_at=now,
        last_seen_at=now,
    )


def _require_session_identity(ctx: RequestContext, session_id: str) -> None:
    if not ctx.client_id or _SESSION_ID_PATTERN.fullmatch(session_id) is None:
        raise PermissionDenied("Ontology MCP session identity is invalid")


def _require_session_owner(
    ctx: RequestContext, app_id: str, row: OsdkMcpSessionRow, *, allow_terminated: bool = False
) -> None:
    identity_matches = (
        row["tenant_id"] == ctx.tenant_id
        and row["app_id"] == app_id
        and row["client_id"] == ctx.client_id
        and row["actor_user_id"] == ctx.actor_user_id
    )
    status_allowed = row["status"] == "active" or (allow_terminated and row["status"] == "terminated")
    if not identity_matches or not status_allowed:
        raise PermissionDenied("Ontology MCP session is invalid")


def _safe_origin(origin: str) -> bool:
    return (
        origin.startswith("https://") or origin.startswith("http://localhost") or origin.startswith("http://127.0.0.1")
    )
