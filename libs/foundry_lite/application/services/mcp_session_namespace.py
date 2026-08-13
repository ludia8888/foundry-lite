"""Plane-owned MCP session identifier validation."""

from __future__ import annotations

import re
from typing import Literal

from foundry_lite.domain.errors import ValidationFailed

McpSessionPlane = Literal["builder", "ontology", "release"]

_BUILDER_SESSION_PATTERN = re.compile(r"mcp-[A-Za-z0-9._:-]{4,251}")
_ONTOLOGY_SESSION_PATTERN = re.compile(r"ontology-mcp-[A-Za-z0-9_-]{8,240}")
_RELEASE_SESSION_PATTERN = re.compile(r"mcp-release-[A-Za-z0-9._:-]{1,243}")


def require_mcp_session_namespace(session_id: str, plane: McpSessionPlane) -> None:
    """Reject a syntactically valid session owned by a different MCP plane."""

    if _is_plane_session(session_id, plane):
        return
    label = "Governed Release" if plane == "release" else plane.title()
    raise ValidationFailed(
        f"{label} MCP requires a {plane}-plane session id",
        details={
            "resource": "mcp_session",
            "reason": f"{plane}_session_namespace_required",
        },
    )


def _is_plane_session(session_id: str, plane: McpSessionPlane) -> bool:
    """Return whether a session identifier belongs to the requested MCP plane."""

    if plane == "ontology":
        return _ONTOLOGY_SESSION_PATTERN.fullmatch(session_id) is not None
    if plane == "release":
        return _RELEASE_SESSION_PATTERN.fullmatch(session_id) is not None
    return _BUILDER_SESSION_PATTERN.fullmatch(session_id) is not None and not session_id.startswith("mcp-release-")


__all__ = ["McpSessionPlane", "require_mcp_session_namespace"]
