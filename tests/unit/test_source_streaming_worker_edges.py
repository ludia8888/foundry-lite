from __future__ import annotations

from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import cast

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed
from foundry_lite.infrastructure.adapters import LocalStreamAdapter
from foundry_lite_worker import source_streaming as worker
from foundry_lite_worker.kraken_kafka_bridge import KrakenBridgeSnapshot


def test_streaming_worker_iteration_stop_and_failure_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    config = worker.SourceStreamingServiceConfig(sync_name="live_sync", storage_root=Path("unused"))
    state = _state()
    lease = worker._WorkerLease("workflow-live", "worker", "token")
    foundry = cast(FoundryLite, object())

    monkeypatch.setattr(worker, "_workflow_was_stopped", lambda *_args: True)
    assert worker._run_iteration(foundry, config, lease, state, Event(), None).stop_reason == "stop_requested"

    monkeypatch.setattr(worker, "_workflow_was_stopped", lambda *_args: False)
    monkeypatch.setattr(worker, "_run_managed_batch", lambda *_args: (_ for _ in ()).throw(worker._StopRequested()))
    assert worker._run_iteration(foundry, config, lease, state, Event(), None).stop_reason == "stop_requested"

    recovered = worker._IterationOutcome(worker._state_after_failure(state), lease, "recovered")
    monkeypatch.setattr(worker, "_run_managed_batch", lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(worker, "_recover_failed_iteration", lambda *_args: recovered)
    assert worker._run_iteration(foundry, config, lease, state, Event(), None) == recovered

    failed_run = {"status": "failed", "error": {"code": "BROKER_OFFLINE"}}
    monkeypatch.setattr(worker, "_run_managed_batch", lambda *_args: failed_run)
    monkeypatch.setattr(worker, "_refresh_lease", lambda *_args: (_ for _ in ()).throw(worker._StopRequested()))
    assert worker._run_iteration(foundry, config, lease, state, Event(), None).stop_reason == "stop_requested"

    monkeypatch.setattr(worker, "_refresh_lease", lambda *_args: lease)
    stopped = Event()
    stopped.set()
    assert worker._run_iteration(foundry, config, lease, state, stopped, None).stop_reason == "shutdown_requested"


def test_streaming_worker_recovery_and_loop_return_stop_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    config = worker.SourceStreamingServiceConfig(sync_name="live_sync", storage_root=Path("unused"))
    state = _state()
    lease = worker._WorkerLease("workflow-live", "worker", "token")
    foundry = cast(FoundryLite, object())

    monkeypatch.setattr(worker, "_refresh_lease", lambda *_args: (_ for _ in ()).throw(worker._StopRequested()))
    outcome = worker._recover_failed_iteration(foundry, config, lease, state, Event(), None, RuntimeError("offline"))
    assert outcome.stop_reason == "stop_requested"
    assert outcome.state.consecutive_failures == 1

    monkeypatch.setattr(worker, "_refresh_lease", lambda *_args: lease)
    stopped = Event()
    stopped.set()
    outcome = worker._recover_failed_iteration(foundry, config, lease, state, stopped, None, RuntimeError("offline"))
    assert outcome.stop_reason == "shutdown_requested"

    monkeypatch.setattr(
        worker,
        "_run_iteration",
        lambda *_args: worker._IterationOutcome(worker._state_after_failure(state), lease, "lease_lost"),
    )
    result = worker._run_loop(foundry, config, {}, lease, state, Event(), None)
    assert result.stop_reason == "lease_lost"
    assert result.iterations == 1


def test_streaming_worker_workflow_lookup_and_refresh_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    config = worker.SourceStreamingServiceConfig(sync_name="live_sync", storage_root=Path("unused"))
    foundry = cast(FoundryLite, object())
    workflow_row = worker._workflow_row

    with pytest.raises(ValidationFailed, match="no requested lifecycle run"):
        worker._linked_workflow(foundry, config, {})

    monkeypatch.setattr(worker, "_workflow_row", lambda *_args: {"status": "succeeded"})
    with pytest.raises(ConflictDetected, match="not active"):
        worker._linked_workflow(foundry, config, {"lastWorkflowRunId": "workflow-live"})

    class _Engine:
        def begin(self):
            return _Transaction()

    missing = SimpleNamespace(
        engine=_Engine(),
        runtime_repository=SimpleNamespace(workflow_run_by_id=lambda **_kwargs: None),
    )
    monkeypatch.setattr(worker, "_workflow_row", workflow_row)
    with pytest.raises(ValidationFailed, match="was not found"):
        worker._workflow_row(cast(FoundryLite, missing), "tenant", "workflow-missing")

    monkeypatch.setattr(worker, "_workflow_row", lambda *_args: {"status": "cancelled"})
    with pytest.raises(worker._StopRequested):
        worker._refresh_lease(
            foundry,
            config,
            worker._WorkerLease("workflow-live", "worker", "token"),
            _state(),
            None,
            None,
        )


def test_streaming_worker_builds_and_stops_kraken_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    starts: list[str] = []

    class _Thread:
        def __init__(self, *, target, kwargs, name, daemon) -> None:
            assert callable(target)
            assert kwargs["config"].stream_name == "kraken-trades"
            assert name == "kraken-kafka-live_sync"
            assert daemon is True

        def start(self) -> None:
            starts.append("started")

        def join(self, *, timeout: float) -> None:
            assert timeout == 5.0
            starts.append("joined")

    monkeypatch.setattr(worker, "Thread", _Thread)
    config = worker.SourceStreamingServiceConfig(sync_name="live_sync", storage_root=Path("unused"))
    bridge = worker._start_bridge(
        config,
        {"configSummary": {"upstreamProvider": "kraken_websocket_v2", "streamName": "kraken-trades"}},
        {"configSummary": {}},
        config.request_context(),
        kraken=cast(object, SimpleNamespace()),
        kafka=cast(object, LocalStreamAdapter()),
    )

    assert bridge is not None
    assert starts == ["started"]
    worker._stop_bridge(bridge)
    assert bridge.stop_event.is_set()
    assert starts == ["started", "joined"]
    assert worker._start_bridge(config, {}, {}, config.request_context(), kraken=None, kafka=None) is None

    kraken = worker._kraken_adapter({"upstreamWebsocketUrl": "wss://example.test/v2", "upstreamSymbol": "ETH/USD"})
    kafka = worker._kafka_adapter(
        config,
        {"topic": "trades", "bootstrapServers": "broker:9092", "consumerGroup": "foundry", "partition": 2},
        "kraken-trades",
    )
    assert kraken.config.symbol == "ETH/USD"
    assert kafka.config.subscriptions[0].partition == 2


def test_streaming_worker_telemetry_reports_bridge_degradation() -> None:
    snapshot = KrakenBridgeSnapshot(
        connection_state="RECONNECTING",
        exchange_status="maintenance",
        connection_id=None,
        last_message_at=None,
        last_trade_at=None,
        published_records=3,
        reconnect_count=2,
        consecutive_failures=1,
        input_rate_per_second=1.5,
        last_error={"code": "OFFLINE"},
    )
    config = worker.SourceStreamingServiceConfig(sync_name="live_sync", storage_root=Path("unused"))
    payload = worker._telemetry_payload(config, _state(), worker.datetime.now(worker.UTC), snapshot, None)

    assert worker._lifecycle_state(_state(), snapshot) == "DEGRADED"
    assert payload["inputRecords"] == 3
    assert payload["upstream"]["connectionState"] == "RECONNECTING"
    assert worker._run_error({"error": {"code": "OFFLINE"}}) == {"code": "OFFLINE"}
    assert worker._run_error({}) is None


def _state() -> worker._LoopState:
    return worker._LoopState(
        iterations=0,
        archived_batches=0,
        rows_archived=0,
        last_version_id=None,
        consecutive_failures=0,
        started_at="2026-07-16T00:00:00Z",
        last_source_run_id=None,
        checkpoint={},
        last_checkpoint_at=None,
        checkpoint_duration_ms=None,
        output_rate_per_second=None,
        kafka={},
    )


class _Transaction:
    def __enter__(self):
        return object()

    def __exit__(self, *_args: object) -> None:
        return None
