from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.operations.run_macmini_soak import (
    _business_probe,
    _parse_probe,
    _probe_availability,
    _safe_business_receipt,
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
    assert summary["businessProbeExecutions"] == 1
    assert summary["multiNodeAvailability"] == "notProven"


def test_soak_summary_fails_on_restart_increment_after_baseline(tmp_path: Path) -> None:
    samples = tmp_path / "samples.ndjson"
    before = _soak_sample("2026-08-17T00:00:00+00:00", restarts=0)
    after = _soak_sample("2026-08-17T00:01:00+00:00", restarts=1)
    samples.write_text(json.dumps(before) + "\n" + json.dumps(after) + "\n", encoding="utf-8")

    summary = _summarize(samples, 60)

    assert summary["status"] == "failed"
    assert summary["unexpectedRestartIncrementCount"] == 1


def test_soak_summary_treats_existing_restart_count_as_baseline(tmp_path: Path) -> None:
    samples = tmp_path / "samples.ndjson"
    before = _soak_sample("2026-08-17T00:00:00+00:00", restarts=4)
    after = _soak_sample("2026-08-17T00:01:00+00:00", restarts=4)
    samples.write_text(json.dumps(before) + "\n" + json.dumps(after) + "\n", encoding="utf-8")

    summary = _summarize(samples, 60)

    assert summary["status"] == "passed"
    assert summary["maximumObservedRestartCount"] == 4
    assert summary["unexpectedRestartIncrementCount"] == 0


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


def test_safe_business_receipt_keeps_only_proof_fields() -> None:
    receipt = _valid_business_receipt()
    receipt["rawToken"] = "must-not-leak"

    safe = _safe_business_receipt(json.dumps(receipt).encode())

    assert safe is not None
    assert safe["actionRunId"] == "action-run-1"
    assert "rawToken" not in safe


def test_safe_business_receipt_rejects_missing_product_evidence() -> None:
    receipt = _valid_business_receipt()
    receipt["idempotentReplay"] = False

    assert _safe_business_receipt(json.dumps(receipt).encode()) is None


def test_business_probe_requires_valid_json_receipt_even_on_zero_exit() -> None:
    command = json.dumps([sys.executable, "-c", "print('not-json')"])

    result = _business_probe(command, 10.0)

    assert result["status"] == "failed"
    assert result["returnCode"] == 0
    assert result["receipt"] is None


def _valid_business_receipt() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "status": "passed",
        "observedAt": "2026-08-17T00:00:00+00:00",
        "actionRunId": "action-run-1",
        "idempotentReplay": True,
        "materializationVersionId": "dataset-version-1",
        "materializationRowCount": 1,
        "objectVersion": 2,
        "datasetPreviewMatched": True,
        "objectQueryMatched": True,
    }


def _soak_sample(sampled_at: str, *, restarts: int) -> dict[str, object]:
    return {
        "sampledAt": sampled_at,
        "http": [{"name": "ready", "isAvailable": True}],
        "pods": {
            "items": [
                {
                    "name": "api",
                    "restarts": restarts,
                    "ownerKind": "ReplicaSet",
                    "ownerName": "api-rs",
                    "oomCount": 0,
                }
            ]
        },
        "businessProbe": {"status": "passed"},
        "nodeMetrics": {"status": "ok", "memoryPercent": 40},
        "disk": {"status": "ok", "usedPercent": 30},
    }


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
