"""MCP plane session identifiers cannot cross gateway boundaries."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from foundry_lite.application.services.aip.fde_mcp_sessions import FdeMcpSessionLedger
from foundry_lite.application.services.mcp_session_namespace import (
    McpSessionPlane,
    require_mcp_session_namespace,
)
from foundry_lite.application.services.ontology_mcp_gateway import OntologyMcpGateway
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

_SESSIONS: dict[McpSessionPlane, str] = {
    "builder": "mcp-builder-session-0001",
    "ontology": "ontology-mcp-session-0001",
    "release": "mcp-release-session-0001",
}


@pytest.mark.parametrize("target_plane", tuple(_SESSIONS))
@pytest.mark.parametrize("owner_plane", tuple(_SESSIONS))
def test_session_namespace_matrix_is_plane_exclusive(
    target_plane: McpSessionPlane,
    owner_plane: McpSessionPlane,
) -> None:
    session_id = _SESSIONS[owner_plane]
    if target_plane == owner_plane:
        require_mcp_session_namespace(session_id, target_plane)
        return
    with pytest.raises(ValidationFailed, match=f"{target_plane}-plane session"):
        require_mcp_session_namespace(session_id, target_plane)


@pytest.mark.parametrize("plane", ["builder", "release"])
@pytest.mark.parametrize("operation", ["events", "close"])
def test_builder_family_session_service_rejects_foreign_read_and_close_before_storage(
    plane: McpSessionPlane,
    operation: str,
) -> None:
    engine = MagicMock()
    ledger = FdeMcpSessionLedger(engine, MagicMock(), plane=plane)
    foreign_plane = "release" if plane == "builder" else "builder"

    with pytest.raises(ValidationFailed):
        getattr(ledger, operation)(RequestContext(), "app-1", _SESSIONS[foreign_plane])

    engine.begin.assert_not_called()


@pytest.mark.parametrize("foreign_plane", ["builder", "release"])
@pytest.mark.parametrize("operation", ["session_events", "close_session"])
def test_ontology_gateway_rejects_foreign_read_and_close_before_delegation(
    foreign_plane: McpSessionPlane,
    operation: str,
) -> None:
    applications = MagicMock()
    access_sessions = MagicMock()
    gateway = OntologyMcpGateway(
        applications=applications,
        objects=MagicMock(),
        unified_search=MagicMock(),
        actions=MagicMock(),
        functions=MagicMock(),
        approvals=MagicMock(),
        access_sessions=access_sessions,
        rate_limits=MagicMock(),
        business_systems=MagicMock(),
    )

    with pytest.raises(ValidationFailed):
        getattr(gateway, operation)(RequestContext(), "app-1", _SESSIONS[foreign_plane])

    access_sessions.require_active.assert_not_called()
    applications.assert_not_called()
