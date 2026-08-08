"""Durable admission policy shared by Builder and Ontology MCP planes."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

from foundry_lite.application.ports import (
    AuditEventRecord,
    OutboxEventRecord,
    RuntimeRepository,
    TransactionManager,
)
from foundry_lite.application.ports.mcp_rate_limiter import (
    McpPlane,
    McpRateLimitDecision,
    McpRateLimiter,
    McpRateLimitRequest,
    McpRateLimitScope,
)
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.primitives import _new_id
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation, RateLimited

_NO_OAUTH_CLIENT = "interactive-client"


@dataclass(frozen=True)
class McpRateLimitConfig:
    endpoint_limit: int = 120
    tool_limit: int = 60
    window_seconds: int = 60

    def __post_init__(self) -> None:
        if self.endpoint_limit <= 0 or self.tool_limit <= 0:
            raise ValueError("MCP rate limits must be greater than zero")
        if self.window_seconds <= 0:
            raise ValueError("MCP rate-limit window must be greater than zero")

    def limit_for(self, limit_scope: McpRateLimitScope) -> int:
        return self.endpoint_limit if limit_scope == "endpoint" else self.tool_limit


class McpRateLimitService(CoreService):
    """Apply stable identity limits and preserve every denial as audit evidence.

    The tool scope is one aggregate for all tools in a plane. Tool names and MCP
    session IDs are deliberately absent from the key, so callers cannot rotate
    attacker-controlled values to obtain fresh buckets.
    """

    required_dependencies = ("engine", "mcp_rate_limiter", "runtime_repository")
    required_collaborators = ()
    engine: TransactionManager
    config: McpRateLimitConfig
    mcp_rate_limiter: McpRateLimiter
    runtime_repository: RuntimeRepository

    def __init__(
        self,
        *,
        engine: TransactionManager,
        mcp_rate_limiter: McpRateLimiter,
        runtime_repository: RuntimeRepository,
        config: McpRateLimitConfig | None = None,
    ) -> None:
        super().__init__(
            engine=engine,
            mcp_rate_limiter=mcp_rate_limiter,
            runtime_repository=runtime_repository,
        )
        self.config = config or McpRateLimitConfig()

    def consume_endpoint(
        self,
        ctx: RequestContext,
        *,
        plane: McpPlane,
        application_id: str,
    ) -> McpRateLimitDecision:
        return self._consume(ctx, plane=plane, application_id=application_id, limit_scope="endpoint")

    def consume_tool(
        self,
        ctx: RequestContext,
        *,
        plane: McpPlane,
        application_id: str,
    ) -> McpRateLimitDecision:
        return self._consume(ctx, plane=plane, application_id=application_id, limit_scope="tool")

    def _consume(
        self,
        ctx: RequestContext,
        *,
        plane: McpPlane,
        application_id: str,
        limit_scope: McpRateLimitScope,
    ) -> McpRateLimitDecision:
        observed_at_epoch = time.time()
        request = _request(
            ctx,
            plane=plane,
            application_id=application_id,
            limit_scope=limit_scope,
            limit=self.config.limit_for(limit_scope),
            window_seconds=self.config.window_seconds,
            observed_at_epoch=observed_at_epoch,
        )
        with self.engine.begin() as conn:
            decision = self.mcp_rate_limiter.consume(transaction=conn, request=request)
            if not decision.is_allowed:
                self._record_denial_evidence(conn, ctx, request, decision)
        if not decision.is_allowed:
            raise _rate_limited(request, decision)
        return decision

    def _record_denial_evidence(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        request: McpRateLimitRequest,
        decision: McpRateLimitDecision,
    ) -> None:
        self.runtime_repository.insert_audit_event(
            transaction=conn,
            record=_denial_audit_record(ctx, request, decision),
        )
        inserted = self.runtime_repository.insert_outbox_event(
            transaction=conn,
            record=_denial_outbox_record(ctx, request, decision),
        )
        if not inserted:
            raise InvariantViolation("MCP rate-limit denial outbox evidence already exists")


def _request(
    ctx: RequestContext,
    *,
    plane: McpPlane,
    application_id: str,
    limit_scope: McpRateLimitScope,
    limit: int,
    window_seconds: int,
    observed_at_epoch: float,
) -> McpRateLimitRequest:
    return McpRateLimitRequest(
        tenant_id=ctx.tenant_id,
        plane=plane,
        application_id=application_id,
        client_id=ctx.client_id or _NO_OAUTH_CLIENT,
        actor_user_id=ctx.actor_user_id,
        limit_scope=limit_scope,
        limit=limit,
        window_seconds=window_seconds,
        request_id=ctx.request_id,
        observed_at_epoch=observed_at_epoch,
        observed_at=datetime.fromtimestamp(observed_at_epoch, UTC).isoformat(),
    )


def _denial_audit_record(
    ctx: RequestContext,
    request: McpRateLimitRequest,
    decision: McpRateLimitDecision,
) -> AuditEventRecord:
    return AuditEventRecord(
        event_id=_new_id("audit"),
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.actor_user_id,
        event_type="mcp.rate_limit.denied",
        resource_type="mcp_rate_limit_window",
        resource_id=decision.evidence_id,
        action=f"mcp:{request.plane}:{request.limit_scope}",
        decision="deny",
        policy_decision=_policy_evidence(request, decision),
        before_ref={},
        after_ref=_denial_evidence(request, decision),
        correlation_id=ctx.request_id,
        request_id=ctx.request_id,
        metadata={
            "plane": request.plane,
            "limitScope": request.limit_scope,
            "applicationId": request.application_id,
            "clientId": request.client_id,
        },
        created_at=request.observed_at,
    )


def _policy_evidence(
    request: McpRateLimitRequest,
    decision: McpRateLimitDecision,
) -> dict[str, object]:
    return {
        "limit": decision.limit,
        "windowSeconds": decision.window_seconds,
        "windowStartedAtEpoch": decision.window_started_at_epoch,
        "windowExpiresAtEpoch": decision.window_expires_at_epoch,
        "scope": request.limit_scope,
    }


def _denial_outbox_record(
    ctx: RequestContext,
    request: McpRateLimitRequest,
    decision: McpRateLimitDecision,
) -> OutboxEventRecord:
    return OutboxEventRecord(
        event_id=_new_id("outbox"),
        tenant_id=ctx.tenant_id,
        event_type="mcp.rate_limit.denied",
        aggregate_type="mcp_rate_limit_window",
        aggregate_id=decision.evidence_id,
        payload={
            "applicationId": request.application_id,
            "plane": request.plane,
            "limitScope": request.limit_scope,
            **_denial_evidence(request, decision),
        },
        status="pending",
        attempts=0,
        idempotency_key=f"mcp-rate-limit-denied:{decision.evidence_id}:{decision.denied_count}",
        correlation_id=ctx.request_id,
        created_at=request.observed_at,
        published_at=None,
    )


def _denial_evidence(
    request: McpRateLimitRequest,
    decision: McpRateLimitDecision,
) -> dict[str, object]:
    return {
        "evidenceId": decision.evidence_id,
        "requestCount": decision.request_count,
        "deniedCount": decision.denied_count,
        "retryAfterSeconds": decision.retry_after_seconds,
        "requestId": request.request_id,
    }


def _rate_limited(request: McpRateLimitRequest, decision: McpRateLimitDecision) -> RateLimited:
    return RateLimited(
        f"{request.plane.title()} MCP {request.limit_scope} rate limit exceeded",
        details={
            "retryAfterSeconds": decision.retry_after_seconds,
            "requestId": request.request_id,
            "rateLimitScope": request.limit_scope,
            "plane": request.plane,
            "applicationId": request.application_id,
            "limit": decision.limit,
            "windowSeconds": decision.window_seconds,
            "evidenceId": decision.evidence_id,
        },
    )


__all__ = ["McpRateLimitConfig", "McpRateLimitService"]
