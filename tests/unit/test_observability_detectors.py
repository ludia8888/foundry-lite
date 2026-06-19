from __future__ import annotations

from copy import deepcopy
from typing import cast

import pytest
from foundry_lite.application.ports import ObservabilityDetectorConfig, ObservabilityIncident, RuntimeRunSnapshot
from foundry_lite.application.services.observability_detectors import (
    _contains_resource,
    _latest_row,
    _required_float,
    _required_str,
    _row_timestamp,
    _seasonal_partitions,
    build_observability_report,
)
from foundry_lite.domain.errors import ValidationFailed


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


def test_quiet_detectors_cover_no_incident_paths() -> None:
    snapshot = _snapshot(
        sync_runs=[
            _run(
                "sync_recent",
                committed_version_id="raw_v1",
                created_at="2026-06-19T00:09:00Z",
                dataset_ref={"version": "raw_v1"},
            )
        ],
        index_runs=[_run("index_ok", event_time_lag_seconds=30)],
        action_runs=[_run("action_small", partition_key="one", partition_size_bytes=1)],
        materialization_runs=[_run("mat_fast", duration_seconds=15)],
    )

    report = build_observability_report(
        snapshot,
        configs=[
            _config("recent-flow", "flow_interruption", resourceId="raw_v1", expectedCadenceSeconds=300),
            _config("low-lag", "lag", runType="index", thresholdSeconds=60),
            _config("no-skew", "skew", runType="action", thresholdRatio=10),
            _config("fast-slo", "slo", runType="materialization", thresholdSeconds=60),
        ],
        observed_at="2026-06-19T00:10:00Z",
    )

    assert report["activeIncidents"] == []
    assert report["configurationVersion"] == "s56-v1"
    assert report["summary"] == {"active": 0, "suppressed": 0, "evaluatedDetectors": 4}


def test_flow_interruption_supports_all_runtime_groups() -> None:
    old = "2026-06-19T00:00:00Z"
    snapshot = _snapshot(
        action_writebacks=[_run("wb_1", created_at=old)],
        outbox_events=[_run("outbox_1", created_at=old)],
        dead_letter_events=[_run("dlq_1", created_at=old)],
        audit_events=[_run("audit_1", created_at=old)],
    )

    report = build_observability_report(
        snapshot,
        configs=[
            _config("writeback-flow", "flow_interruption", runType="action_writeback", expectedCadenceSeconds=60),
            _config("outbox-flow", "flow_interruption", runType="outbox", expectedCadenceSeconds=60),
            _config("dlq-flow", "flow_interruption", runType="dead_letter", expectedCadenceSeconds=60),
            _config("audit-flow", "flow_interruption", runType="audit", expectedCadenceSeconds=60, severity="notice"),
        ],
        observed_at="2026-06-19T00:10:00Z",
    )

    assert [incident["evidenceLinks"][0]["runType"] for incident in report["activeIncidents"]] == [
        "action_writeback",
        "outbox",
        "dead_letter",
        "audit",
    ]
    assert report["activeIncidents"][-1]["severity"] == "warning"


def test_empty_and_invalid_observability_configs_are_reported_safely() -> None:
    empty = build_observability_report(_snapshot(), configs=[], observed_at="2026-06-19T00:00:00Z")
    invalid = build_observability_report(
        _snapshot(),
        configs=[
            _config("unsupported-detector", "unknown"),
            _config("unsupported-run", "lag", runType="unknown"),
        ],
        observed_at="2026-06-19T00:00:00Z",
    )

    assert empty["configurationVersion"] == "none"
    assert empty["summary"] == {"active": 0, "suppressed": 0, "evaluatedDetectors": 0}
    assert [incident["detectorType"] for incident in invalid["activeIncidents"]] == [
        "detector_failure",
        "detector_failure",
    ]
    assert invalid["activeIncidents"][0]["threshold"] == {}


def test_detector_edge_paths_without_measurement_or_cooldown_match() -> None:
    snapshot = _snapshot(
        sync_runs=[_run("sync_without_event_time")],
        transform_runs=[_run("transform_without_duration")],
        index_runs=[_run("index_old", created_at="2026-06-19T00:00:00Z")],
        action_runs=[
            _run("small-a", partition_key="a", partition_size_bytes=100),
            _run("small-b", partition_key="b", partition_size_bytes=110),
        ],
    )

    report = build_observability_report(
        snapshot,
        configs=[
            _config("lag-without-measurement", "lag", thresholdSeconds=60),
            _config("low-skew", "skew", runType="action", thresholdRatio=10),
            _config("slo-without-duration", "slo", runType="transform", thresholdSeconds=60),
            _config(
                "index-flow",
                "flow_interruption",
                runType="index",
                expectedCadenceSeconds=60,
                cooldownSeconds=600,
            ),
        ],
        previous_incidents=[
            {"dedupeKey": "different-detector"},
            {"dedupeKey": "index-flow:flow_interruption:dataset:*"},
        ],
        observed_at="2026-06-19T00:10:00Z",
    )

    assert [incident["detectorId"] for incident in report["activeIncidents"]] == ["index-flow"]
    assert report["suppressedIncidents"] == []
    assert report["summary"] == {"active": 1, "suppressed": 0, "evaluatedDetectors": 4}


def test_observability_helper_edges_are_explicit() -> None:
    assert _contains_resource({"items": ["other", {"id": "target"}]}, "target") is True
    assert _contains_resource({"items": ["other", {"id": "missing"}]}, "target") is False
    assert _latest_row([_run("untimed")]) is None
    assert _row_timestamp(None) is None
    assert _row_timestamp(_run("untimed")) is None
    assert (
        _seasonal_partitions(
            _config(
                "bad-seasonality-baseline",
                "skew",
                seasonalityBaseline={"expectedSeasonalPartitions": "holiday"},
            )
        )
        == set()
    )
    with pytest.raises(ValidationFailed, match="missing required string"):
        _required_str({"detectorId": ""}, "detectorId")
    with pytest.raises(ValidationFailed, match="missing required number"):
        _required_float({"thresholdRatio": "5"}, "thresholdRatio")


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
    index_runs: list[dict[str, object]] | None = None,
    action_runs: list[dict[str, object]] | None = None,
    action_writebacks: list[dict[str, object]] | None = None,
    materialization_runs: list[dict[str, object]] | None = None,
    outbox_events: list[dict[str, object]] | None = None,
    dead_letter_events: list[dict[str, object]] | None = None,
    audit_events: list[dict[str, object]] | None = None,
) -> RuntimeRunSnapshot:
    return {
        "syncRuns": sync_runs or [],
        "transformRuns": transform_runs or [],
        "indexRuns": index_runs or [],
        "actionRuns": action_runs or [],
        "actionWritebacks": action_writebacks or [],
        "materializationRuns": materialization_runs or [],
        "outboxEvents": outbox_events or [],
        "deadLetterEvents": dead_letter_events or [],
        "auditEvents": audit_events or [],
        "objectEdits": [],
    }
