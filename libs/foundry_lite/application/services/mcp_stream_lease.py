"""Shared durable lease values for one active SSE stream per MCP session."""

from __future__ import annotations

from datetime import datetime, timedelta

from foundry_lite.application.ports import OsdkMcpSessionRow, OsdkMcpStreamLease
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.domain.errors import ConflictDetected

_STREAM_LEASE_TTL_SECONDS = 30


def new_mcp_stream_lease() -> tuple[str, OsdkMcpStreamLease]:
    claimed_at = _now()
    expires_at = (_parse_timestamp(claimed_at) + timedelta(seconds=_STREAM_LEASE_TTL_SECONDS)).isoformat()
    return claimed_at, OsdkMcpStreamLease(
        lease_id=_new_id("osdk_mcp_stream_lease"),
        expires_at=expires_at,
    )


def mcp_stream_conflict(row: OsdkMcpSessionRow) -> ConflictDetected:
    return ConflictDetected(
        "MCP session already has an active SSE stream",
        details={
            "resource": "mcp_session_stream",
            "retryAt": row["stream_lease_expires_at"],
        },
    )


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)
