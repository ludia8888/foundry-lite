from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite_worker import source_scheduler
from foundry_lite_worker.source_scheduler import (
    SourceSchedulerWorkerConfig,
    SourceSchedulerWorkerResult,
    run_source_scheduler_daemon,
    run_source_scheduler_once,
)


class _FakeSources:
    def __init__(self, ticks: list[dict[str, object]]) -> None:
        self._ticks = ticks
        self.contexts: list[object] = []
        self.max_runs: list[int] = []

    def run_due_managed_syncs(self, **kwargs: object) -> dict[str, object]:
        self.contexts.append(kwargs["ctx"])
        self.max_runs.append(kwargs["max_runs"])
        return self._ticks.pop(0)


def test_source_scheduler_worker_daemon_stops_after_empty_tick_and_writes_evidence(tmp_path) -> None:
    evidence_path = tmp_path / "scheduler-evidence.json"
    config = SourceSchedulerWorkerConfig(
        storage_root=tmp_path / "runtime",
        max_ticks=3,
        max_empty_ticks=1,
        tick_interval_seconds=0,
        is_continuous=True,
        evidence_path=evidence_path,
    )

    result = run_source_scheduler_daemon(config)

    assert result.stop_reason == "empty_ticks"
    assert result.iterations == 1
    assert result.started == 0
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert payload["stopReason"] == "empty_ticks"
    assert payload["evaluated"] == 0


def test_source_scheduler_worker_daemon_can_run_until_stop_callback(tmp_path) -> None:
    checks = 0
    config = SourceSchedulerWorkerConfig(
        storage_root=tmp_path / "runtime",
        max_ticks=0,
        max_empty_ticks=0,
        tick_interval_seconds=0,
        is_continuous=True,
    )

    def should_stop() -> bool:
        nonlocal checks
        checks += 1
        return checks > 3

    result = run_source_scheduler_daemon(config, should_stop=should_stop)

    assert result.stop_reason == "stop_requested"
    assert result.iterations == 3
    assert result.empty_ticks == 3


def test_source_scheduler_worker_once_summarizes_started_runs_and_evidence(monkeypatch, tmp_path) -> None:
    evidence_path = tmp_path / "evidence" / "scheduler.json"
    fake_sources = _FakeSources(
        [
            {
                "evaluated": 2,
                "due": [{"syncName": "orders"}, {"syncName": "customers"}],
                "started": [
                    {"run": {"runId": "run-orders"}},
                    {"run": {"runId": "run-customers"}},
                    {"run": {"notRunId": "ignored"}},
                ],
            }
        ]
    )
    monkeypatch.setattr(
        source_scheduler,
        "_build_foundry",
        lambda _config: SimpleNamespace(sources=fake_sources),
    )

    result = run_source_scheduler_once(
        SourceSchedulerWorkerConfig(
            storage_root=tmp_path,
            max_runs_per_tick=7,
            evidence_path=evidence_path,
            tenant_id="tenant-a",
            actor_user_id="actor-a",
            request_id="req-a",
        )
    )

    assert result.stop_reason == "max_ticks"
    assert result.empty_ticks == 0
    assert result.evaluated == 2
    assert result.due == 2
    assert result.started == 3
    assert result.run_ids == ("run-orders", "run-customers")
    assert fake_sources.max_runs == [7]
    assert fake_sources.contexts[0].request_id == "req-a:tick-1"
    assert json.loads(evidence_path.read_text(encoding="utf-8"))["runIds"] == ["run-orders", "run-customers"]


def test_source_scheduler_worker_daemon_honors_max_ticks_and_sleep(monkeypatch, tmp_path) -> None:
    fake_sources = _FakeSources(
        [
            {"evaluated": 1, "due": [], "started": [{"run": {"runId": "run-1"}}]},
            {"evaluated": 1, "due": "not-a-list", "started": "not-a-list"},
        ]
    )
    sleeps: list[float] = []
    monkeypatch.setattr(source_scheduler, "_build_foundry", lambda _config: SimpleNamespace(sources=fake_sources))
    monkeypatch.setattr(source_scheduler.time, "sleep", sleeps.append)

    result = run_source_scheduler_daemon(
        SourceSchedulerWorkerConfig(
            storage_root=tmp_path,
            max_ticks=2,
            max_empty_ticks=0,
            tick_interval_seconds=0.25,
            is_continuous=True,
        )
    )

    assert result.stop_reason == "max_ticks"
    assert result.iterations == 2
    assert result.empty_ticks == 1
    assert result.evaluated == 2
    assert result.due == 0
    assert result.run_ids == ("run-1",)
    assert sleeps == [0.25]


def test_source_scheduler_worker_config_from_env_and_cli_success(monkeypatch, capsys, tmp_path) -> None:
    env = {
        "FOUNDRY_LITE_HOME": str(tmp_path / "home"),
        "FOUNDRY_LITE_DB_URL": "sqlite:///scheduler.db",
        "FOUNDRY_LITE_ADAPTER_PROFILE": "local",
        "FOUNDRY_LITE_SOURCE_SCHEDULER_MAX_RUNS": "9",
        "FOUNDRY_LITE_SOURCE_SCHEDULER_MAX_TICKS": "3",
        "FOUNDRY_LITE_SOURCE_SCHEDULER_MAX_EMPTY_TICKS": "2",
        "FOUNDRY_LITE_SOURCE_SCHEDULER_INTERVAL_SECONDS": "1.5",
        "FOUNDRY_LITE_SOURCE_SCHEDULER_CONTINUOUS": "yes",
        "FOUNDRY_LITE_TENANT_ID": "tenant-env",
        "FOUNDRY_LITE_WORKER_ACTOR_USER_ID": "actor-env",
        "FOUNDRY_LITE_WORKER_REQUEST_ID": "req-env",
        "FOUNDRY_LITE_SOURCE_SCHEDULER_EVIDENCE_PATH": str(tmp_path / "env-evidence.json"),
    }
    config = source_scheduler.config_from_env(env)
    assert config.storage_root == tmp_path / "home"
    assert config.db_url == "sqlite:///scheduler.db"
    assert config.max_runs_per_tick == 9
    assert config.is_continuous is True
    assert source_scheduler.config_from_env({"FOUNDRY_LITE_SOURCE_SCHEDULER_CONTINUOUS": "off"}).is_continuous is False

    def fake_once(config: SourceSchedulerWorkerConfig) -> SourceSchedulerWorkerResult:
        assert config.storage_root == tmp_path / "cli-home"
        assert config.max_runs_per_tick == 4
        assert config.is_continuous is False
        return SourceSchedulerWorkerResult("STOPPED", "max_ticks", 1, 0, 1, 1, 1, ("run-cli",))

    monkeypatch.setattr(source_scheduler, "run_source_scheduler_once", fake_once)

    exit_code = source_scheduler.main(
        [
            "--storage-root",
            str(tmp_path / "cli-home"),
            "--max-runs",
            "4",
            "--max-ticks",
            "1",
            "--interval-seconds",
            "0",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["runIds"] == ["run-cli"]


def test_source_scheduler_worker_cli_and_failure_payload_edges(monkeypatch, capsys, tmp_path) -> None:
    def fail_with_value_error(_config: SourceSchedulerWorkerConfig) -> SourceSchedulerWorkerResult:
        raise ValueError("bad scheduler config")

    monkeypatch.setattr(source_scheduler, "run_source_scheduler_once", fail_with_value_error)
    assert source_scheduler.main(["--storage-root", str(tmp_path), "--max-runs", "1"]) == 1
    value_payload = json.loads(capsys.readouterr().out)
    assert value_payload["type"] == "CONFIGURATION_ERROR"
    assert value_payload["details"]["adapter"] == "source_scheduler_worker"

    config = SourceSchedulerWorkerConfig(storage_root=tmp_path, tenant_id=" ")
    assert source_scheduler._failure_context(config) is None
    assert source_scheduler._failure_trace(config) == {"adapter": "source_scheduler_worker"}
    assert source_scheduler._failure_payload(ValidationFailed("boom"), None)["type"] == "VALIDATION_FAILED"

    with pytest.raises(ValueError, match="tenant_id"):
        config.request_context(tick_number=1)
    with pytest.raises(ValueError, match="positive"):
        source_scheduler._positive(0)
    with pytest.raises(ValueError, match="max_ticks"):
        source_scheduler._positive_or_unbounded(-1, "max_ticks")
