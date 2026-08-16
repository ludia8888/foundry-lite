"""Resident supervisor that reconciles a streaming Source desired state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.errors import ConflictDetected, FoundryLiteError, ValidationFailed

from foundry_lite_worker.source_streaming import (
    SourceStreamingServiceConfig,
    SourceStreamingServiceResult,
    build_source_streaming_foundry,
    run_source_streaming_service,
)

SourceStreamingRunner = Callable[..., SourceStreamingServiceResult]
_WAITING_MESSAGES = frozenset(
    {
        "streaming sync has no requested lifecycle run",
        "streaming sync lifecycle run is not active",
        "streaming sync worker lease is owned by another worker",
    }
)


@dataclass(frozen=True)
class SourceStreamingSupervisorResult:
    lifecycle_runs: int
    stop_reason: str
    last_service_result: SourceStreamingServiceResult | None


def run_source_streaming_supervisor(
    config: SourceStreamingServiceConfig,
    *,
    foundry: FoundryLite | None = None,
    stop_event: Event | None = None,
    poll_interval_seconds: float = 1.0,
    runner: SourceStreamingRunner = run_source_streaming_service,
) -> SourceStreamingSupervisorResult:
    runtime = foundry or build_source_streaming_foundry(config)
    is_runtime_owned = foundry is None
    try:
        return _run_supervisor_loop(
            config,
            runtime=runtime,
            stop_event=stop_event,
            poll_interval_seconds=poll_interval_seconds,
            runner=runner,
        )
    finally:
        if is_runtime_owned:
            runtime.close()


def _run_supervisor_loop(
    config: SourceStreamingServiceConfig,
    *,
    runtime: FoundryLite,
    stop_event: Event | None,
    poll_interval_seconds: float,
    runner: SourceStreamingRunner,
) -> SourceStreamingSupervisorResult:
    requested_stop = stop_event or Event()
    lifecycle_runs = 0
    last_result: SourceStreamingServiceResult | None = None
    while not requested_stop.is_set():
        try:
            last_result = runner(config, foundry=runtime, stop_event=requested_stop)
        except FoundryLiteError as exc:
            if not _is_waiting_state(exc):
                raise
        else:
            lifecycle_runs += 1
            if last_result.stop_reason == "shutdown_requested":
                break
        requested_stop.wait(poll_interval_seconds)
    return SourceStreamingSupervisorResult(
        lifecycle_runs=lifecycle_runs,
        stop_reason="shutdown_requested",
        last_service_result=last_result,
    )


def _is_waiting_state(exc: FoundryLiteError) -> bool:
    return isinstance(exc, (ConflictDetected, ValidationFailed)) and exc.message in _WAITING_MESSAGES
