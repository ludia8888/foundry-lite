"""Support helpers for OSDK OAuth session service workflows."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from foundry_lite.application.ports import TransactionContext
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import RateLimited

OAUTH_RATE_LIMIT_CAPACITY_ENV = "FOUNDRY_LITE_OAUTH_RATE_LIMIT_CAPACITY"
OAUTH_RATE_LIMIT_WINDOW_ENV = "FOUNDRY_LITE_OAUTH_RATE_LIMIT_WINDOW_SECONDS"

# Sized for an MCP host, not a human at a login form. A remote host re-exchanges its token
# around each tool-call cycle, so a per-minute budget tuned to interactive sign-ins starves a
# working session: the host reports "couldn't connect your account" and the whole connector
# looks broken. The exchanges being counted all present a credential (one-time PKCE code,
# rotating refresh token, or client secret), so this budget bounds abuse rather than being the
# thing that stops a guess.
_RATE_LIMIT_CAPACITY = int(os.getenv(OAUTH_RATE_LIMIT_CAPACITY_ENV, "60"))
_RATE_LIMIT_WINDOW_SECONDS = float(os.getenv(OAUTH_RATE_LIMIT_WINDOW_ENV, "60"))


class _OAuthAuditBoundary(Protocol):
    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str | None,
        action: str,
        decision: str = "allow",
        policy_decision: Mapping[str, object] | None = None,
        before_ref: Mapping[str, object] | None = None,
        after_ref: Mapping[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> None: ...


@dataclass
class _OAuthRateLimiter:
    buckets: dict[tuple[str, str, str, str], list[float]] = field(default_factory=dict)

    def check(self, ctx: RequestContext, action: str, client_id: str) -> None:
        now = time.monotonic()
        key = (ctx.tenant_id, ctx.actor_user_id, client_id, action)
        recent = [seen_at for seen_at in self.buckets.get(key, []) if now - seen_at < _RATE_LIMIT_WINDOW_SECONDS]
        if len(recent) >= _RATE_LIMIT_CAPACITY:
            retry_after = max(1, int(_RATE_LIMIT_WINDOW_SECONDS - (now - recent[0])))
            raise RateLimited("OSDK OAuth rate limit exceeded", details={"retryAfterSeconds": retry_after})
        recent.append(now)
        self.buckets[key] = recent
