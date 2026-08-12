"""Thin facade for the separate Governed Release MCP boundary."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import OsdkMcpStreamLease
from foundry_lite.application.services.aip.governed_release_live_attestation_service import (
    GovernedReleaseLiveAttestationService,
)
from foundry_lite.application.services.aip.governed_release_mcp import GovernedReleaseMcpGateway
from foundry_lite.application.services.aip.governed_release_mcp_types import GovernedReleaseMcpToolCall
from foundry_lite.domain.context import RequestContext


class GovernedReleaseWorkspace:
    """Keep release MCP transport methods outside the already-large AIP facade."""

    def __init__(
        self,
        gateway: GovernedReleaseMcpGateway,
        live_attestations: GovernedReleaseLiveAttestationService,
    ) -> None:
        self._gateway = gateway
        self._live_attestations = live_attestations

    def release_live_readiness(
        self,
        application_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object]:
        return self._live_attestations.live_readiness(ctx or RequestContext(), application_id)

    def consume_release_mcp_endpoint_rate_limit(
        self,
        application_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> None:
        self._gateway.consume_endpoint_rate_limit(ctx or RequestContext(), application_id)

    def release_mcp_tools(
        self,
        application_id: str,
        *,
        session_id: str | None = None,
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object]:
        return self._gateway.list_tools(ctx or RequestContext(), application_id, session_id=session_id)

    def run_release_mcp_tool(
        self,
        request: GovernedReleaseMcpToolCall,
        *,
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object]:
        return self._gateway.execute_tool(ctx or RequestContext(), request)

    def open_release_mcp_session(
        self,
        application_id: str,
        session_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object]:
        return self._gateway.open_session(ctx or RequestContext(), application_id, session_id)

    def release_mcp_session_events(
        self,
        application_id: str,
        session_id: str,
        *,
        after_sequence: int = 0,
        ctx: RequestContext | None = None,
    ) -> list[Mapping[str, object]]:
        return [
            dict(event)
            for event in self._gateway.session_events(
                ctx or RequestContext(),
                application_id,
                session_id,
                after_sequence=after_sequence,
            )
        ]

    def claim_release_mcp_session_stream(
        self,
        application_id: str,
        session_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> OsdkMcpStreamLease:
        return self._gateway.claim_session_stream(ctx or RequestContext(), application_id, session_id)

    def release_release_mcp_session_stream(
        self,
        application_id: str,
        session_id: str,
        lease_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> bool:
        return self._gateway.release_session_stream(
            ctx or RequestContext(),
            application_id,
            session_id,
            lease_id,
        )

    def close_release_mcp_session(
        self,
        application_id: str,
        session_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object]:
        return self._gateway.close_session(ctx or RequestContext(), application_id, session_id)


__all__ = ["GovernedReleaseWorkspace"]
