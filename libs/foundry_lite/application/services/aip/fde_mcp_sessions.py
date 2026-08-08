"""Durable Builder MCP session ownership and lazy-discovery evidence."""

from __future__ import annotations

import re
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
from foundry_lite.application.services.mcp_stream_lease import mcp_stream_conflict, new_mcp_stream_lease
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed

JsonObject = Mapping[str, object]
LAZY_DISCOVERY_MARKER = "__foundry_lite_builder_lazy_discovery__"
_SESSION_PATTERN = re.compile(r"[A-Za-z0-9._:-]{8,255}")


class FdeMcpSessionLedger:
    """Persist Builder session lifecycle without depending on Ontology MCP enablement."""

    repository: OsdkApplicationRepository

    def __init__(self, engine: TransactionManager, repository: OsdkApplicationRepository) -> None:
        self.engine = engine
        self.repository = repository

    def open(self, ctx: RequestContext, application_id: str, session_id: str) -> OsdkMcpSessionRow:
        _validate_session_id(session_id)
        now = _now()
        with self.engine.begin() as conn:
            existing = self.repository.insert_mcp_session_or_get_existing(
                transaction=conn,
                record=_session_record(ctx, application_id, session_id, now),
            )
            row = existing or self.repository.mcp_session_by_id(
                transaction=conn, tenant_id=ctx.tenant_id, session_id=session_id
            )
            active = _required_owner(ctx, application_id, row)
            if existing is None:
                self._append(conn, ctx, active, "notifications/foundry-lite/session_ready", {})
            return active

    def require_active(self, ctx: RequestContext, application_id: str, session_id: str) -> OsdkMcpSessionRow:
        with self.engine.begin() as conn:
            row = self.repository.mcp_session_by_id(transaction=conn, tenant_id=ctx.tenant_id, session_id=session_id)
            return _required_owner(ctx, application_id, row)

    def events(
        self,
        ctx: RequestContext,
        application_id: str,
        session_id: str,
        *,
        after_sequence: int = 0,
    ) -> list[OsdkMcpSessionEventRow]:
        with self.engine.begin() as conn:
            row = self.repository.mcp_session_by_id(transaction=conn, tenant_id=ctx.tenant_id, session_id=session_id)
            _required_owner(ctx, application_id, row)
            return self.repository.mcp_session_events_after(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                session_id=session_id,
                after_sequence=max(0, after_sequence),
            )

    def claim_stream(self, ctx: RequestContext, application_id: str, session_id: str) -> OsdkMcpStreamLease:
        claimed_at, lease = new_mcp_stream_lease()
        with self.engine.begin() as conn:
            row = self.repository.mcp_session_by_id(transaction=conn, tenant_id=ctx.tenant_id, session_id=session_id)
            _required_owner(ctx, application_id, row)
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
                raise mcp_stream_conflict(_required_owner(ctx, application_id, current))
        return lease

    def release_stream(self, ctx: RequestContext, application_id: str, session_id: str, lease_id: str) -> bool:
        with self.engine.begin() as conn:
            row = self.repository.mcp_session_by_id(transaction=conn, tenant_id=ctx.tenant_id, session_id=session_id)
            _required_owner(ctx, application_id, row, allow_terminated=True)
            return self.repository.release_mcp_session_stream_lease(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                session_id=session_id,
                lease_id=lease_id,
                released_at=_now(),
            )

    def close(self, ctx: RequestContext, application_id: str, session_id: str) -> OsdkMcpSessionRow:
        with self.engine.begin() as conn:
            row = self.repository.mcp_session_by_id(transaction=conn, tenant_id=ctx.tenant_id, session_id=session_id)
            active = _required_owner(ctx, application_id, row)
            self._append(conn, ctx, active, "notifications/foundry-lite/session_closed", {})
            terminated = self.repository.terminate_mcp_session(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                session_id=session_id,
                terminated_at=_now(),
            )
            if terminated is None:
                raise _session_not_found()
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
        with self.engine.begin() as conn:
            row = self.repository.mcp_session_by_id(transaction=conn, tenant_id=ctx.tenant_id, session_id=session_id)
            return self._append(conn, ctx, _required_owner(ctx, application_id, row), event_type, payload)

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
            raise _session_not_found()
        return event


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
) -> OsdkMcpSessionRow:
    if row is None or row["status"] not in ({"active", "terminated"} if allow_terminated else {"active"}):
        raise _session_not_found()
    owner = (row["tenant_id"], row["app_id"], row["client_id"], row["actor_user_id"])
    expected = (ctx.tenant_id, application_id, ctx.client_id, ctx.actor_user_id)
    if owner != expected:
        raise _session_not_found()
    return row


def _validate_session_id(session_id: str) -> None:
    if _SESSION_PATTERN.fullmatch(session_id) is None:
        raise ValidationFailed("Builder MCP session id is invalid")


def _session_not_found() -> NotFound:
    return NotFound("Builder MCP session not found", details={"resource": "mcp_session"})
