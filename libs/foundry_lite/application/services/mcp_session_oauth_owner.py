"""Hash-only OAuth ownership evidence for durable MCP sessions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import OsdkMcpSessionEventRow, RuntimeJsonObject
from foundry_lite.domain.context import RequestContext

_READY_EVENT_TYPES = frozenset(
    {
        "notifications/foundry-lite/session_ready",
        "notifications/session.ready",
    }
)


def mcp_session_owner_payload(
    ctx: RequestContext,
    payload: Mapping[str, object] | None = None,
) -> RuntimeJsonObject:
    """Return session-ready evidence without a raw OAuth token or session id."""

    result = dict(payload or {})
    result.update(
        {
            "oauthSessionHash": ctx.oauth_session_hash,
            "oauthSessionAuthority": ctx.oauth_session_authority,
        }
    )
    return result


def mcp_session_owner_matches(
    ctx: RequestContext,
    events: Sequence[OsdkMcpSessionEventRow],
) -> bool:
    """Require the current request to reuse the exact OAuth session owner."""

    ready = next((event for event in events if event["event_type"] in _READY_EVENT_TYPES), None)
    if ready is None:
        return False
    payload = ready.get("payload_json")
    if not isinstance(payload, Mapping):
        return False
    return (
        payload.get("oauthSessionHash") == ctx.oauth_session_hash
        and payload.get("oauthSessionAuthority") == ctx.oauth_session_authority
    )


__all__ = ["mcp_session_owner_matches", "mcp_session_owner_payload"]
