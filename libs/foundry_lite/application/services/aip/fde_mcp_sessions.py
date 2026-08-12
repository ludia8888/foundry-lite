"""Durable Builder MCP session ownership and lazy-discovery evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from foundry_lite.application.ports import (
    OsdkApplicationRepository,
    OsdkMcpSessionEventRow,
    OsdkMcpSessionRecord,
    OsdkMcpSessionRow,
    OsdkMcpStreamLease,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.ports.osdk_security_repository import OsdkMcpToolActivationRecord
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.mcp_session_namespace import (
    McpSessionPlane,
    require_mcp_session_namespace,
)
from foundry_lite.application.services.mcp_session_oauth_owner import (
    mcp_session_owner_matches,
    mcp_session_owner_payload,
)
from foundry_lite.application.services.mcp_stream_lease import mcp_stream_conflict, new_mcp_stream_lease
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound

JsonObject = Mapping[str, object]
LAZY_DISCOVERY_MARKER = "__foundry_lite_builder_lazy_discovery__"


class FdeMcpSessionLedger:
    """Persist a Builder-family session lifecycle in its assigned plane."""

    repository: OsdkApplicationRepository
    plane: McpSessionPlane

    def __init__(
        self,
        engine: TransactionManager,
        repository: OsdkApplicationRepository,
        *,
        plane: McpSessionPlane = "builder",
    ) -> None:
        self.engine = engine
        self.repository = repository
        self.plane = plane

    def open(self, ctx: RequestContext, application_id: str, session_id: str) -> OsdkMcpSessionRow:
        self._require_session_id(session_id)
        now = _now()
        with self.engine.begin() as conn:
            existing = self.repository.insert_mcp_session_or_get_existing(
                transaction=conn,
                record=_session_record(ctx, application_id, session_id, now),
            )
            row = existing or self.repository.mcp_session_by_id(
                transaction=conn, tenant_id=ctx.tenant_id, session_id=session_id
            )
            active = _required_owner(ctx, application_id, row, plane=self.plane)
            if existing is None:
                self._append(
                    conn,
                    ctx,
                    active,
                    "notifications/foundry-lite/session_ready",
                    mcp_session_owner_payload(ctx),
                )
            else:
                self._require_oauth_owner(conn, ctx, active)
            return active

    def require_active(self, ctx: RequestContext, application_id: str, session_id: str) -> OsdkMcpSessionRow:
        self._require_session_id(session_id)
        with self.engine.begin() as conn:
            row = self.repository.mcp_session_by_id(transaction=conn, tenant_id=ctx.tenant_id, session_id=session_id)
            return self._required_owner(conn, ctx, application_id, row)

    def events(
        self,
        ctx: RequestContext,
        application_id: str,
        session_id: str,
        *,
        after_sequence: int = 0,
    ) -> list[OsdkMcpSessionEventRow]:
        self._require_session_id(session_id)
        with self.engine.begin() as conn:
            row = self.repository.mcp_session_by_id(transaction=conn, tenant_id=ctx.tenant_id, session_id=session_id)
            self._required_owner(conn, ctx, application_id, row)
            return self.repository.mcp_session_events_after(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                session_id=session_id,
                after_sequence=max(0, after_sequence),
            )

    def claim_stream(self, ctx: RequestContext, application_id: str, session_id: str) -> OsdkMcpStreamLease:
        self._require_session_id(session_id)
        claimed_at, lease = new_mcp_stream_lease()
        with self.engine.begin() as conn:
            row = self.repository.mcp_session_by_id(transaction=conn, tenant_id=ctx.tenant_id, session_id=session_id)
            self._required_owner(conn, ctx, application_id, row)
            claimed = self.repository.claim_mcp_session_stream_lease(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                session_id=session_id,
                lease_id=lease.lease_id,
                claimed_at=claimed_at,
                lease_expires_at=lease.expires_at,
            )
            if claimed is None:
                current = self.repository.mcp_session_by_id(
                    transaction=conn, tenant_id=ctx.tenant_id, session_id=session_id
                )
                raise mcp_stream_conflict(self._required_owner(conn, ctx, application_id, current))
        return lease

    def release_stream(self, ctx: RequestContext, application_id: str, session_id: str, lease_id: str) -> bool:
        self._require_session_id(session_id)
        with self.engine.begin() as conn:
            row = self.repository.mcp_session_by_id(transaction=conn, tenant_id=ctx.tenant_id, session_id=session_id)
            self._required_owner(conn, ctx, application_id, row, allow_terminated=True)
            return self.repository.release_mcp_session_stream_lease(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                session_id=session_id,
                lease_id=lease_id,
                released_at=_now(),
            )

    def close(self, ctx: RequestContext, application_id: str, session_id: str) -> OsdkMcpSessionRow:
        self._require_session_id(session_id)
        with self.engine.begin() as conn:
            row = self.repository.mcp_session_by_id(transaction=conn, tenant_id=ctx.tenant_id, session_id=session_id)
            active = self._required_owner(conn, ctx, application_id, row)
            self._append(conn, ctx, active, "notifications/foundry-lite/session_closed", {})
            terminated = self.repository.terminate_mcp_session(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                session_id=session_id,
                terminated_at=_now(),
            )
            if terminated is None:
                raise _session_not_found(self.plane)
            return terminated

    def activated_tool_ids(self, ctx: RequestContext, application_id: str, session_id: str) -> set[str]:
        self.require_active(ctx, application_id, session_id)
        with self.engine.begin() as conn:
            rows = self.repository.mcp_tool_activations(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                app_id=application_id,
                session_id=session_id,
                client_id=ctx.client_id or "",
                actor_user_id=ctx.actor_user_id,
            )
        return {str(row["tool_id"]) for row in rows}

    def mark_lazy(self, ctx: RequestContext, application_id: str, session_id: str) -> None:
        self.require_active(ctx, application_id, session_id)
        with self.engine.begin() as conn:
            self.repository.activate_mcp_tool(
                transaction=conn,
                record=_activation_record(ctx, application_id, session_id, LAZY_DISCOVERY_MARKER, "lazy"),
            )

    def append_event(
        self,
        ctx: RequestContext,
        application_id: str,
        session_id: str,
        event_type: str,
        payload: JsonObject,
    ) -> OsdkMcpSessionEventRow:
        self._require_session_id(session_id)
        with self.engine.begin() as conn:
            row = self.repository.mcp_session_by_id(transaction=conn, tenant_id=ctx.tenant_id, session_id=session_id)
            owner = self._required_owner(conn, ctx, application_id, row)
            return self._append(conn, ctx, owner, event_type, payload)

    def record_tool_completed(
        self,
        ctx: RequestContext,
        application_id: str,
        session_id: str,
        tool_id: str,
        run_id: str,
    ) -> None:
        self.append_event(
            ctx,
            application_id,
            session_id,
            "notifications/foundry-lite/tool_completed",
            {"toolId": tool_id, "aiRunId": run_id},
        )

    def _append(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        session: OsdkMcpSessionRow,
        event_type: str,
        payload: JsonObject,
    ) -> OsdkMcpSessionEventRow:
        event = self.repository.append_mcp_session_event(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            session_id=session["id"],
            event_type=event_type,
            payload=cast(dict[str, object], dict(payload)),
            created_at=_now(),
        )
        if event is None:
            raise _session_not_found(self.plane)
        return event

    def _require_session_id(self, session_id: str) -> None:
        require_mcp_session_namespace(session_id, self.plane)

    def _required_owner(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        application_id: str,
        row: OsdkMcpSessionRow | None,
        *,
        allow_terminated: bool = False,
    ) -> OsdkMcpSessionRow:
        owner = _required_owner(
            ctx,
            application_id,
            row,
            allow_terminated=allow_terminated,
            plane=self.plane,
        )
        self._require_oauth_owner(conn, ctx, owner)
        return owner

    def _require_oauth_owner(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        session: OsdkMcpSessionRow,
    ) -> None:
        events = self.repository.mcp_session_events_after(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            session_id=session["id"],
            after_sequence=0,
        )
        if not mcp_session_owner_matches(ctx, events):
            raise _session_not_found(self.plane)


def activation_record(
    ctx: RequestContext,
    application_id: str,
    session_id: str,
    tool_id: str,
    query_hash: str,
) -> OsdkMcpToolActivationRecord:
    return _activation_record(ctx, application_id, session_id, tool_id, query_hash)


def _activation_record(
    ctx: RequestContext,
    application_id: str,
    session_id: str,
    tool_id: str,
    query_hash: str,
) -> OsdkMcpToolActivationRecord:
    return OsdkMcpToolActivationRecord(
        activation_id=_new_id("osdk_mcp_tool_activation"),
        tenant_id=ctx.tenant_id,
        app_id=application_id,
        session_id=session_id,
        client_id=ctx.client_id or "",
        actor_user_id=ctx.actor_user_id,
        tool_id=tool_id,
        query_hash=query_hash,
        activated_at=_now(),
    )


def _session_record(ctx: RequestContext, application_id: str, session_id: str, now: str) -> OsdkMcpSessionRecord:
    return OsdkMcpSessionRecord(
        session_id=session_id,
        tenant_id=ctx.tenant_id,
        app_id=application_id,
        client_id=ctx.client_id or "",
        actor_user_id=ctx.actor_user_id,
        status="active",
        created_at=now,
        last_seen_at=now,
    )


def _required_owner(
    ctx: RequestContext,
    application_id: str,
    row: OsdkMcpSessionRow | None,
    *,
    allow_terminated: bool = False,
    plane: McpSessionPlane = "builder",
) -> OsdkMcpSessionRow:
    if row is None or row["status"] not in ({"active", "terminated"} if allow_terminated else {"active"}):
        raise _session_not_found(plane)
    owner = (row["tenant_id"], row["app_id"], row["client_id"], row["actor_user_id"])
    expected = (ctx.tenant_id, application_id, ctx.client_id, ctx.actor_user_id)
    if owner != expected:
        raise _session_not_found(plane)
    return row


def _session_not_found(plane: McpSessionPlane) -> NotFound:
    label = "Governed Release" if plane == "release" else plane.title()
    return NotFound(f"{label} MCP session not found", details={"resource": "mcp_session"})
