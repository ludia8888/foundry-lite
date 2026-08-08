"""Application port for durable MCP invocation rate-limit windows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from foundry_lite.application.ports.transaction_context import TransactionContext

McpPlane = Literal["builder", "ontology"]
McpRateLimitScope = Literal["endpoint", "tool"]


@dataclass(frozen=True)
class McpRateLimitRequest:
    """One authenticated admission attempt in a fixed window."""

    tenant_id: str
    plane: McpPlane
    application_id: str
    client_id: str
    actor_user_id: str
    limit_scope: McpRateLimitScope
    limit: int
    window_seconds: int
    request_id: str
    observed_at_epoch: float
    observed_at: str


@dataclass(frozen=True)
class McpRateLimitDecision:
    """Durable result returned by the atomic counter adapter."""

    is_allowed: bool
    evidence_id: str
    request_count: int
    denied_count: int
    limit: int
    window_seconds: int
    window_started_at_epoch: int
    window_expires_at_epoch: int
    retry_after_seconds: int


class McpRateLimiter(Protocol):
    """Atomically consume one fixed-window admission attempt."""

    def consume(
        self,
        *,
        transaction: TransactionContext,
        request: McpRateLimitRequest,
    ) -> McpRateLimitDecision: ...


__all__ = [
    "McpPlane",
    "McpRateLimitDecision",
    "McpRateLimitRequest",
    "McpRateLimitScope",
    "McpRateLimiter",
]
