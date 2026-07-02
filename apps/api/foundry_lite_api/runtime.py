"""Application-wide singletons shared by the API composition root and routers.

Import order matters: observability must be configured before the FoundryLite
engine is created, matching the original single-module startup sequence.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.infrastructure.auth import AuthProvider, auth_provider_from_env
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite.observability.tracing import configure_observability


@dataclass
class _ApiWindowRateLimiter:
    buckets: dict[tuple[str, ...], list[float]]

    def __init__(self) -> None:
        self.buckets = {}

    def retry_after_seconds(self, key: tuple[str, ...], *, limit: int, window_seconds: float) -> int | None:
        now = time.monotonic()
        self._prune_expired_buckets(now, window_seconds)
        recent = [seen_at for seen_at in self.buckets.get(key, []) if now - seen_at < window_seconds]
        if len(recent) >= limit:
            return max(1, int(window_seconds - (now - recent[0])))
        recent.append(now)
        self.buckets[key] = recent
        return None

    def _prune_expired_buckets(self, now: float, window_seconds: float) -> None:
        # Bucket keys include the URL-path object_type, so they are effectively
        # attacker-controlled; without pruning the dict grows one entry per
        # unique key forever. Live keys are bounded by request rate x window.
        expired = [key for key, seen in self.buckets.items() if not seen or now - seen[-1] >= window_seconds]
        for key in expired:
            del self.buckets[key]


configure_observability("foundry-lite-api")
foundry = FoundryLite(
    dependencies=create_local_core_dependencies(
        db_url=os.getenv("FOUNDRY_LITE_DB_URL"),
        storage_root=os.getenv("FOUNDRY_LITE_HOME", ".foundry-lite"),
        adapter_profile=os.getenv("FOUNDRY_LITE_ADAPTER_PROFILE", "local"),
    )
)
# Sprint 36A: choose auth through a profile guard so production startup cannot
# accidentally use the local header-trust adapter.
auth_provider: AuthProvider = auth_provider_from_env()

ALLOWED_BROWSER_ORIGINS = ("http://127.0.0.1:4173", "http://localhost:4173")

WEBSOCKET_SUBSCRIPTION_CONNECT_LIMIT = int(os.getenv("FOUNDRY_LITE_WS_SUBSCRIPTION_CONNECT_LIMIT", "100"))
WEBSOCKET_SUBSCRIPTION_CONNECT_WINDOW_SECONDS = float(
    os.getenv("FOUNDRY_LITE_WS_SUBSCRIPTION_CONNECT_WINDOW_SECONDS", "60")
)
websocket_subscription_rate_limiter = _ApiWindowRateLimiter()
