from __future__ import annotations

import json
import signal
from pathlib import Path
from threading import Event

import pytest
from foundry_lite_worker import source_streaming_cli as cli
from foundry_lite_worker.source_streaming import SourceStreamingServiceResult
from foundry_lite_worker.source_streaming_supervisor import SourceStreamingSupervisorResult


def test_streaming_cli_config_parses_runtime_environment() -> None:
    config = cli.config_from_env(
        {
            "FOUNDRY_LITE_STREAMING_SYNC_NAME": "live_sync",
            "FOUNDRY_LITE_HOME": "/tmp/foundry-streaming",
            "FOUNDRY_LITE_DB_URL": "sqlite:///streaming.db",
            "FOUNDRY_LITE_ADAPTER_PROFILE": "fake",
            "FOUNDRY_LITE_TENANT_ID": "tenant-live",
            "FOUNDRY_LITE_STREAMING_ACTOR_USER_ID": "worker-live",
            "FOUNDRY_LITE_STREAMING_REQUEST_ID": "req-live",
            "FOUNDRY_LITE_STREAMING_WORKER_ID": "worker-1",
            "FOUNDRY_LITE_STREAMING_LEASE_TTL_SECONDS": "45",
            "FOUNDRY_LITE_STREAMING_RETRY_BASE_SECONDS": "0.5",
            "FOUNDRY_LITE_STREAMING_RETRY_MAX_SECONDS": "8.5",
            "FOUNDRY_LITE_STREAMING_MAX_ITERATIONS": "7",
        }
    )

    assert config.sync_name == "live_sync"
    assert config.storage_root == Path("/tmp/foundry-streaming")
    assert config.db_url == "sqlite:///streaming.db"
    assert config.adapter_profile == "fake"
    assert config.tenant_id == "tenant-live"
    assert config.actor_user_id == "worker-live"
    assert config.request_id == "req-live"
    assert config.worker_id == "worker-1"
    assert config.lease_ttl_seconds == 45
    assert config.retry_base_seconds == 0.5
    assert config.retry_max_seconds == 8.5
    assert config.max_iterations == 7
    assert cli._env_optional_int({"MAX": "  "}, "MAX") is None


def test_streaming_cli_once_prints_durable_service_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    captured: list[object] = []

    def run_once(config, *, stop_event):
        captured.extend((config, stop_event))
        return _service_result("max_iterations")

    monkeypatch.setattr(cli, "run_source_streaming_service", run_once)
    monkeypatch.setattr(cli, "_install_signal_handlers", lambda _event: None)

    exit_code = cli.main(
        [
            "--sync-name",
            "live_sync",
            "--storage-root",
            str(tmp_path),
            "--worker-id",
            "worker-cli",
            "--lease-ttl-seconds",
            "15",
            "--max-iterations",
            "1",
            "--once",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    config = captured[0]
    assert exit_code == 0
    assert config.sync_name == "live_sync"
    assert config.worker_id == "worker-cli"
    assert config.lease_ttl_seconds == 15
    assert config.max_iterations == 1
    assert isinstance(captured[1], Event)
    assert payload == {
        "archivedBatches": 2,
        "iterations": 3,
        "lastVersionId": "version-live",
        "rowsArchived": 4,
        "status": "STOPPED",
        "stopReason": "max_iterations",
        "workflowRunId": "workflow-live",
    }


def test_streaming_cli_supervisor_and_error_payloads(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "_install_signal_handlers", lambda _event: None)
    monkeypatch.setattr(
        cli,
        "run_source_streaming_supervisor",
        lambda *_args, **_kwargs: SourceStreamingSupervisorResult(2, "shutdown_requested", _service_result("stop")),
    )

    assert cli.main(["--sync-name", "live_sync"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "supervisor"
    assert payload["lifecycleRuns"] == 2
    assert payload["lastWorkflowRunId"] == "workflow-live"
    assert payload["lastVersionId"] == "version-live"

    monkeypatch.setattr(
        cli,
        "run_source_streaming_supervisor",
        lambda *_args, **_kwargs: SourceStreamingSupervisorResult(0, "shutdown_requested", None),
    )
    assert cli.main([]) == 0
    assert json.loads(capsys.readouterr().out)["lastWorkflowRunId"] is None

    monkeypatch.setattr(
        cli,
        "run_source_streaming_service",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad")),
    )
    assert cli.main(["--once"]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "code": "WORKER_FAILURE",
        "message": "source streaming worker failed with ValueError",
    }


def test_streaming_cli_installs_shutdown_signal_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    handlers: dict[signal.Signals, object] = {}
    monkeypatch.setattr(signal, "signal", lambda kind, handler: handlers.__setitem__(kind, handler))
    stop_event = Event()

    cli._install_signal_handlers(stop_event)

    assert set(handlers) == {signal.SIGTERM, signal.SIGINT}
    handler = handlers[signal.SIGTERM]
    assert callable(handler)
    handler(signal.SIGTERM, object())
    assert stop_event.is_set()


def _service_result(reason: str) -> SourceStreamingServiceResult:
    return SourceStreamingServiceResult(
        workflow_run_id="workflow-live",
        stop_reason=reason,
        iterations=3,
        archived_batches=2,
        rows_archived=4,
        last_version_id="version-live",
    )
