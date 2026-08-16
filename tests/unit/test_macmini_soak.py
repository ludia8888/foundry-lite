from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.operations.run_macmini_soak import (
    _parse_probe,
    _probe_availability,
    _summarize,
    _unexpected_pod_replacements,
)


def test_soak_probe_contract_requires_named_http_url() -> None:
    probe = _parse_probe("ready=https://qa.example/readyz")
    assert (probe.name, probe.url) == ("ready", "https://qa.example/readyz")
    with pytest.raises(ValueError, match="probe_invalid"):
        _parse_probe("https://qa.example/readyz")


def test_soak_summary_requires_999_and_zero_restarts(tmp_path: Path) -> None:
    samples = tmp_path / "samples.ndjson"
    sample = {
        "sampledAt": "2026-08-17T00:00:00+00:00",
        "http": [{"name": "ready", "isAvailable": True}],
        "pods": {"items": [{"name": "api", "restarts": 0, "ownerKind": "ReplicaSet", "ownerName": "api-rs"}]},
        "businessProbe": {"status": "passed"},
        "nodeMetrics": {"status": "ok", "memoryPercent": 40},
        "disk": {"status": "ok", "usedPercent": 30},
    }
    samples.write_text(json.dumps(sample) + "\n", encoding="utf-8")
    summary = _summarize(samples, 86400)
    assert summary["status"] == "passed"
    assert summary["multiNodeAvailability"] == "notProven"


def test_soak_summary_fails_on_unexpected_restart(tmp_path: Path) -> None:
    samples = tmp_path / "samples.ndjson"
    sample = {
        "sampledAt": "2026-08-17T00:00:00+00:00",
        "http": [{"name": "ready", "isAvailable": True}],
        "pods": {"items": [{"name": "api", "restarts": 1, "ownerKind": "ReplicaSet", "ownerName": "api-rs"}]},
        "businessProbe": None,
        "nodeMetrics": {"status": "ok", "memoryPercent": 40},
        "disk": {"status": "ok", "usedPercent": 30},
    }
    samples.write_text(json.dumps(sample) + "\n", encoding="utf-8")
    assert _summarize(samples, 60)["status"] == "failed"


def test_soak_availability_is_calculated_per_probe() -> None:
    results = [
        {"name": "health", "isAvailable": True},
        {"name": "osdk", "isAvailable": False},
    ]

    assert _probe_availability(results) == {"health": 1.0, "osdk": 0.0}


def test_declared_fault_between_samples_resets_pod_replacement_baseline() -> None:
    before = datetime(2026, 8, 17, tzinfo=UTC)
    after = before + timedelta(minutes=1)
    samples = [
        _replacement_sample(before, "api-old"),
        _replacement_sample(after, "api-new"),
    ]
    windows = ((before + timedelta(seconds=10), before + timedelta(seconds=20)),)

    assert _unexpected_pod_replacements(samples, windows) == 0
    assert _unexpected_pod_replacements(samples, ()) == 1


def _replacement_sample(sampled_at: datetime, pod_name: str) -> dict[str, object]:
    return {
        "sampledAt": sampled_at.isoformat(),
        "pods": {
            "items": [
                {
                    "name": pod_name,
                    "ownerKind": "ReplicaSet",
                    "ownerName": "api-rs",
                    "restarts": 0,
                }
            ]
        },
    }
