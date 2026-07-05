"""Lazy API runtime shared by the API composition root and routers."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TypeVar, cast

from foundry_lite.application.dependencies import RuntimeProfile
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.infrastructure.auth import AuthProvider, auth_provider_from_env
from foundry_lite.infrastructure.local_runtime import create_runtime_core_dependencies
from foundry_lite.observability.tracing import configure_observability

_TargetT = TypeVar("_TargetT")


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


@dataclass(frozen=True)
class ApiRuntime:
    """Runtime objects that should be created during app startup, not module import."""

    foundry: FoundryLite
    auth_provider: AuthProvider


class _RuntimeProxy[TargetT]:
    """Compatibility proxy for existing routers that import ``runtime.foundry``."""

    def __init__(self, target: Callable[[], _TargetT]) -> None:
        self._target = target

    def __getattr__(self, name: str) -> object:
        return getattr(self._target(), name)


_api_runtime: ApiRuntime | None = None
_api_runtime_lock = threading.Lock()
_observability_configured = False

ALLOWED_BROWSER_ORIGINS = ("http://127.0.0.1:4173", "http://localhost:4173")

WEBSOCKET_SUBSCRIPTION_CONNECT_LIMIT = int(os.getenv("FOUNDRY_LITE_WS_SUBSCRIPTION_CONNECT_LIMIT", "100"))
WEBSOCKET_SUBSCRIPTION_CONNECT_WINDOW_SECONDS = float(
    os.getenv("FOUNDRY_LITE_WS_SUBSCRIPTION_CONNECT_WINDOW_SECONDS", "60")
)
websocket_subscription_rate_limiter = _ApiWindowRateLimiter()


def initialize_api_runtime(environ: Mapping[str, str] | None = None) -> ApiRuntime:
    """Create the API runtime once, using the environment visible at startup time."""

    source = os.environ if environ is None else environ
    global _api_runtime
    with _api_runtime_lock:
        if _api_runtime is None:
            _configure_observability_once()
            dependencies = create_runtime_core_dependencies(
                profile=RuntimeProfile.from_value(source.get("FOUNDRY_LITE_RUNTIME_PROFILE")),
                db_url=source.get("FOUNDRY_LITE_DB_URL"),
                storage_root=source.get("FOUNDRY_LITE_HOME", ".foundry-lite"),
                adapter_profile=source.get("FOUNDRY_LITE_ADAPTER_PROFILE", "local"),
            )
            _api_runtime = ApiRuntime(
                foundry=FoundryLite(dependencies=dependencies),
                auth_provider=auth_provider_from_env(source),
            )
        return _api_runtime


def get_api_runtime() -> ApiRuntime:
    """Return the initialized runtime, creating it lazily for compatibility callers."""

    return initialize_api_runtime()


def get_foundry() -> FoundryLite:
    return get_api_runtime().foundry


def get_auth_provider() -> AuthProvider:
    return get_api_runtime().auth_provider


def is_api_runtime_initialized() -> bool:
    return _api_runtime is not None


def reset_api_runtime_for_tests() -> None:
    """Dispose and clear the lazy runtime so tests can vary environment safely."""

    global _api_runtime
    with _api_runtime_lock:
        if _api_runtime is not None:
            dispose = getattr(_api_runtime.foundry.engine, "dispose", None)
            if callable(dispose):
                dispose()
        _api_runtime = None


def _configure_observability_once() -> None:
    global _observability_configured
    if _observability_configured:
        return
    configure_observability("foundry-lite-api")
    _observability_configured = True


foundry = cast(FoundryLite, _RuntimeProxy(get_foundry))
auth_provider = cast(AuthProvider, _RuntimeProxy(get_auth_provider))
