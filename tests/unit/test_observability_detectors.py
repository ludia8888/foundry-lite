from __future__ import annotations

from copy import deepcopy
from typing import cast

from foundry_lite.application.ports import ObservabilityDetectorConfig, ObservabilityIncident, RuntimeRunSnapshot
from foundry_lite.application.services.observability_detectors import build_observability_report


def test_missing_data_triggers_flow_interruption() -> None:
    snapshot = _snapshot(sync_runs=[_run("sync_1", committed_version_id="raw_v1", created_at="2026-06-19T00:00:00Z")])

    report = build_observability_report(
        snapshot,
        configs=[_config("raw-orders-flow", "flow_interruption", resourceId="raw_v1", expectedCadenceSeconds=300)],
        observed_at="2026-06-19T00:15:00Z",
    )

    incident = report["activeIncidents"][0]
    assert incident["detectorType"] == "flow_interruption"
    assert incident["evidence"]["failedRunCount"] == 0
    assert incident["evidence"]["secondsSinceLastSuccess"] == 900.0
    assert incident["evidenceLinks"][0]["operationPath"] == "/api/operations/runs/sync/sync_1"


def test_lag_alert_uses_event_and_processing_time_separately() -> None:
    snapshot = _snapshot(
        sync_runs=[
            _run(
                "sync_lag",
                event_time="2026-06-19T00:00:00Z",
                processed_at="2026-06-19T00:12:00Z",
            )
        ]
    )

    report = build_observability_report(
        snapshot,
        configs=[_config("source-lag", "lag", thresholdSeconds=600)],
        observed_at="2026-06-19T00:12:30Z",
    )

    evidence = report["activeIncidents"][0]["evidence"]
    assert evidence["sourceEventTime"] == "2026-06-19T00:00:00Z"
    assert evidence["processingTime"] == "2026-06-19T00:12:00Z"
    assert evidence["eventTimeLagSeconds"] == 720.0


def test_skew_detector_ignores_expected_seasonality() -> None:
    snapshot = _snapshot(
        sync_runs=[
            _run("seasonal", partition_key="holiday", partition_size_bytes=50_000),
            _run("normal-a", partition_key="a", partition_size_bytes=100),
            _run("normal-b", partition_key="b", partition_size_bytes=100),
            _run("hot", partition_key="c", partition_size_bytes=1_000),
        ]
    )

    report = build_observability_report(
        snapshot,
        configs=[
            _config(
                "partition-skew",
                "skew",
                thresholdRatio=5,
                seasonalityBaseline={"expectedSeasonalPartitions": ["holiday"]},
            )
        ],
        observed_at="2026-06-19T00:00:00Z",
    )

    evidence = report["activeIncidents"][0]["evidence"]
    assert evidence["largestPartition"] == "c"
    assert evidence["ignoredSeasonalPartitions"] == ["holiday"]
    assert evidence["skewRatio"] == 10.0


def test_sla_alert_carries_run_and_dataset_refs() -> None:
    snapshot = _snapshot(
        transform_runs=[
            _run(
                "transform_slow",
                output_version_id="clean_v1",
                started_at="2026-06-19T00:00:00Z",
                finished_at="2026-06-19T00:20:00Z",
            )
        ]
    )

    report = build_observability_report(
        snapshot,
        configs=[_config("clean-dataset-slo", "slo", runType="transform", thresholdSeconds=600)],
        observed_at="2026-06-19T00:21:00Z",
    )

    incident = report["activeIncidents"][0]
    assert incident["evidence"]["datasetVersionId"] == "clean_v1"
    assert incident["evidence"]["references"] == {"output_version_id": "clean_v1"}
    assert incident["evidenceLinks"][0]["operationPath"] == "/api/operations/runs/transform/transform_slow"


def test_alert_dedup_prevents_alarm_storm() -> None:
    snapshot = _snapshot(sync_runs=[_run("sync_1", committed_version_id="raw_v1", created_at="2026-06-19T00:00:00Z")])
    config = _config(
        "raw-orders-flow",
        "flow_interruption",
        resourceId="raw_v1",
        expectedCadenceSeconds=300,
        cooldownSeconds=600,
    )
    first = build_observability_report(snapshot, configs=[config], observed_at="2026-06-19T00:10:00Z")

    second = build_observability_report(
        snapshot,
        configs=[config],
        previous_incidents=cast(list[ObservabilityIncident], first["activeIncidents"]),
        observed_at="2026-06-19T00:12:00Z",
    )

    assert second["activeIncidents"] == []
    assert second["suppressedIncidents"][0]["status"] == "suppressed"
    assert second["suppressedIncidents"][0]["evidence"]["suppressionReason"] == "cooldown"


def test_detector_failure_does_not_mutate_source_of_truth() -> None:
    snapshot = _snapshot(
        sync_runs=[
            _run(
                "sync_1",
                event_time="2026-06-19T00:00:00Z",
                processed_at="2026-06-19T00:05:00Z",
            )
        ]
    )
    original = deepcopy(snapshot)

    report = build_observability_report(
        snapshot,
        configs=[_config("bad-lag-config", "lag")],
        observed_at="2026-06-19T00:01:00Z",
    )

    assert snapshot == original
    assert report["activeIncidents"][0]["detectorType"] == "detector_failure"
    assert report["activeIncidents"][0]["message"] == "detector evaluation failed without mutating source-of-truth"


def _config(
    detector_id: str,
    detector_type: str,
    **overrides: object,
) -> ObservabilityDetectorConfig:
    payload = {
        "detectorId": detector_id,
        "detectorType": detector_type,
        "configVersion": "s56-v1",
        "owner": "data-platform",
        "severity": "warning",
        "runType": "sync",
        "resourceType": "dataset",
    }
    payload.update(overrides)
    return cast(ObservabilityDetectorConfig, payload)


def _run(run_id: str, **fields: object) -> dict[str, object]:
    return {"id": run_id, "status": "succeeded", **fields}


def _snapshot(
    *,
    sync_runs: list[dict[str, object]] | None = None,
    transform_runs: list[dict[str, object]] | None = None,
) -> RuntimeRunSnapshot:
    return {
        "syncRuns": sync_runs or [],
        "transformRuns": transform_runs or [],
        "indexRuns": [],
        "actionRuns": [],
        "actionWritebacks": [],
        "materializationRuns": [],
        "outboxEvents": [],
        "deadLetterEvents": [],
        "auditEvents": [],
        "objectEdits": [],
    }
