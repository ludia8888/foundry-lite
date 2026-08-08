"""Internal JSON-RPC identity helpers that preserve the public wire id."""

from __future__ import annotations

import base64

from foundry_lite.domain.errors import ValidationFailed

JsonRpcRequestId = str | int


def internal_mcp_request_id(value: object) -> str:
    """Return a type-tagged, delimiter-safe identity without changing the wire value."""

    if type(value) is int:
        return f"integer:{value}"
    if type(value) is str:
        encoded = base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")
        return f"string:{encoded}"
    raise ValidationFailed("MCP request id must be a non-null string or integer")


__all__ = ["JsonRpcRequestId", "internal_mcp_request_id"]
