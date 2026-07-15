from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import cast

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.errors import ConflictDetected
from foundry_lite_worker.source_streaming import SourceStreamingServiceConfig, SourceStreamingServiceResult
from foundry_lite_worker.source_streaming_supervisor import run_source_streaming_supervisor


def test_supervisor_waits_between_lifecycle_runs_until_process_shutdown() -> None:
    stop_event = Event()
    calls = 0

    def runner(*_args: object, **_kwargs: object) -> SourceStreamingServiceResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConflictDetected("streaming sync lifecycle run is not active")
        if calls == 2:
            return _result("stop_requested")
        stop_event.set()
        return _result("shutdown_requested")

    result = run_source_streaming_supervisor(
        SourceStreamingServiceConfig(sync_name="live_sync", storage_root=Path("unused")),
        foundry=cast(FoundryLite, object()),
        stop_event=stop_event,
        poll_interval_seconds=0,
        runner=runner,
    )

    assert calls == 3
    assert result.lifecycle_runs == 2
    assert result.stop_reason == "shutdown_requested"
    assert result.last_service_result is not None
    assert result.last_service_result.stop_reason == "shutdown_requested"


def _result(reason: str) -> SourceStreamingServiceResult:
    return SourceStreamingServiceResult(
        workflow_run_id=f"workflow-{reason}",
        stop_reason=reason,
        iterations=1,
        archived_batches=0,
        rows_archived=0,
        last_version_id=None,
    )
