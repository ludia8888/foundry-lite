from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import DeadLetterRecord, StreamAdapter, StreamArchiveConfig, StreamPublishRequest
from foundry_lite.application.services.dataset.stream_archive import stream_cursor_offset
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed
from foundry_lite.infrastructure.adapters import DuckDBComputeAdapter
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


def test_stream_archive_appends_preview_and_checkpoint_metadata(tmp_path: Path) -> None:
    foundry, dependencies = _core_with_stream(tmp_path)
    ctx = demo_admin_context()
    stream = StreamArchiveConfig(stream_name="shipments", topic="shipment_events")
    foundry.datasets.ensure("raw.shipment_events", ctx=ctx, primary_key=["event_id"])
    _publish_shipment(dependencies.stream_adapter, "S-100", ctx=ctx)
    _publish_shipment(dependencies.stream_adapter, "S-101", ctx=ctx)

    result = foundry.datasets.archive_stream_events("raw.shipment_events", stream=stream, ctx=ctx)

    assert result is not None
    preview = foundry.datasets.preview("raw.shipment_events", ctx=ctx)
    latest = _latest_stream_transaction(foundry, dependencies, ctx, "raw.shipment_events")
    runs = foundry.operations.query_runs(ctx=ctx, run_type="sync", status="COMMITTED")["syncRuns"]
    assert result.row_count == 2
    assert [row["event_id"] for row in preview] == ["shipment_events:0:0", "shipment_events:0:1"]
    assert preview[0]["payload_json"] == '{"shipment_id":"S-100","status":"IN_TRANSIT"}'
    assert latest["metadata"]["streamCursor"]["offset"] == 1
    assert runs[0]["source_type"] == "stream.shipments"


def test_stream_archive_restart_resumes_after_committed_offset(tmp_path: Path) -> None:
    foundry, dependencies = _core_with_stream(tmp_path)
    ctx = demo_admin_context()
    stream = StreamArchiveConfig(stream_name="shipments", topic="shipment_events", limit=2)
    foundry.datasets.ensure("raw.shipment_events", ctx=ctx, primary_key=["event_id"])
    for shipment_id in ("S-100", "S-101", "S-102"):
        _publish_shipment(dependencies.stream_adapter, shipment_id, ctx=ctx)

    first = foundry.datasets.archive_stream_events("raw.shipment_events", stream=stream, ctx=ctx)
    second = foundry.datasets.archive_stream_events("raw.shipment_events", stream=stream, ctx=ctx)

    assert first is not None
    assert second is not None
    latest_preview = foundry.datasets.preview("raw.shipment_events", ctx=ctx)
    latest = _latest_stream_transaction(foundry, dependencies, ctx, "raw.shipment_events")
    assert first.row_count == 2
    assert second.row_count == 1
    assert latest_preview[0]["event_id"] == "shipment_events:0:2"
    assert latest["metadata"]["streamCursor"]["offset"] == 2


def test_stream_offset_not_advanced_when_append_commit_fails(tmp_path: Path) -> None:
    foundry, dependencies = _core_with_stream(tmp_path)
    failing_foundry = FoundryLite(dependencies=replace(dependencies, compute_adapter=_ExplodingRowsComputeAdapter()))
    ctx = demo_admin_context()
    stream = StreamArchiveConfig(stream_name="shipments", topic="shipment_events", limit=1)
    foundry.datasets.ensure("raw.shipment_events", ctx=ctx, primary_key=["event_id"])
    _publish_shipment(dependencies.stream_adapter, "S-100", ctx=ctx)
    _publish_shipment(dependencies.stream_adapter, "S-101", ctx=ctx)

    first = foundry.datasets.archive_stream_events("raw.shipment_events", stream=stream, ctx=ctx)
    with pytest.raises(ValidationFailed, match="stream archive failed"):
        failing_foundry.datasets.archive_stream_events("raw.shipment_events", stream=stream, ctx=ctx)
    retry = foundry.datasets.archive_stream_events("raw.shipment_events", stream=stream, ctx=ctx)

    latest = _latest_stream_transaction(foundry, dependencies, ctx, "raw.shipment_events")
    preview = foundry.datasets.preview("raw.shipment_events", ctx=ctx)
    assert first is not None
    assert retry is not None
    assert retry.row_count == 1
    assert preview[0]["event_id"] == "shipment_events:0:1"
    assert latest["metadata"]["streamCursor"]["offset"] == 1


def test_stream_archive_cursor_requires_matching_schema_strategy() -> None:
    stream = StreamArchiveConfig(
        stream_name="shipments",
        topic="shipment_events",
        schema_strategy="cdc_envelope_json",
    )
    metadata = {
        "streamCursor": {
            "streamName": "shipments",
            "topic": "shipment_events",
            "partition": 0,
            "consumerGroup": "foundry-lite-archive",
            "offset": 7,
            "schemaStrategy": "envelope_json",
        }
    }

    assert stream_cursor_offset(metadata, stream) is None


def test_stream_archive_failure_is_visible_in_operations(tmp_path: Path) -> None:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    foundry = FoundryLite(dependencies=replace(dependencies, compute_adapter=_ExplodingRowsComputeAdapter()))
    ctx = demo_admin_context()
    stream = StreamArchiveConfig(stream_name="shipments", topic="shipment_events")
    foundry.datasets.ensure("raw.shipment_events", ctx=ctx, primary_key=["event_id"])
    _publish_shipment(dependencies.stream_adapter, "S-100", ctx=ctx)

    with pytest.raises(ValidationFailed, match="stream archive failed"):
        foundry.datasets.archive_stream_events("raw.shipment_events", stream=stream, ctx=ctx)

    failed_runs = foundry.operations.query_runs(ctx=ctx, run_type="sync", status="FAILED")["syncRuns"]
    failed_run = next(run for run in failed_runs if run["source_type"] == "stream.shipments")
    assert _run_error_adapter(failed_run) == "stream_archive_writer"


def test_stream_archive_quarantines_poison_cdc_record_and_commits_valid_records(tmp_path: Path) -> None:
    foundry, dependencies = _core_with_stream(tmp_path)
    ctx = demo_admin_context()
    stream = StreamArchiveConfig(
        stream_name="shipment_cdc",
        topic="shipment_cdc_events",
        schema_strategy="cdc_envelope_json",
    )
    foundry.datasets.ensure("raw.shipment_cdc", ctx=ctx, primary_key=["event_id"])
    _publish_cdc_shipment(dependencies.stream_adapter, "S-bad", ctx=ctx, payload=_invalid_after_payload("S-bad"))
    _publish_cdc_shipment(dependencies.stream_adapter, "S-100", ctx=ctx)

    result = foundry.datasets.archive_stream_events("raw.shipment_cdc", stream=stream, ctx=ctx)

    assert result is not None
    preview = foundry.datasets.preview("raw.shipment_cdc", ctx=ctx)
    dead_letters = _dead_letter_records(dependencies, ctx)
    audit_events = foundry.operations.list_runs(ctx=ctx)["auditEvents"]
    latest = _latest_stream_transaction(foundry, dependencies, ctx, "raw.shipment_cdc")
    assert result.row_count == 1
    assert [row["event_id"] for row in preview] == ["shipment_cdc_events:0:1"]
    assert latest["metadata"]["streamCursor"]["offset"] == 1
    assert [row["source_event_id"] for row in dead_letters] == ["shipment_cdc_events:0:0"]
    assert dead_letters[0]["status"] == "QUARANTINED"
    assert dead_letters[0]["error_kind"] == "VALIDATION_FAILED"
    assert dead_letters[0]["replay_status"] == "NOT_REQUESTED"
    assert dead_letters[0]["source_run_id"] is not None
    assert any(event["event_type"] == "dead_letter_record.quarantined" for event in audit_events)


def test_one_poison_record_does_not_stop_valid_records(tmp_path: Path) -> None:
    foundry, dependencies = _core_with_stream(tmp_path)
    ctx = demo_admin_context()
    stream = StreamArchiveConfig(
        stream_name="shipment_cdc",
        topic="shipment_cdc_events",
        schema_strategy="cdc_envelope_json",
    )
    foundry.datasets.ensure("raw.shipment_cdc", ctx=ctx, primary_key=["event_id"])
    _publish_cdc_shipment(dependencies.stream_adapter, "S-bad", ctx=ctx, payload=_invalid_after_payload("S-bad"))
    _publish_cdc_shipment(dependencies.stream_adapter, "S-100", ctx=ctx)

    result = foundry.datasets.archive_stream_events("raw.shipment_cdc", stream=stream, ctx=ctx)

    assert result.row_count == 1
    assert foundry.datasets.preview("raw.shipment_cdc", ctx=ctx)[0]["event_id"] == "shipment_cdc_events:0:1"
    assert _dead_letter_records(dependencies, ctx)[0]["source_event_id"] == "shipment_cdc_events:0:0"


def test_error_ratio_above_threshold_fails_batch(tmp_path: Path) -> None:
    foundry, dependencies = _core_with_stream(tmp_path)
    ctx = demo_admin_context()
    stream = StreamArchiveConfig(
        stream_name="shipment_cdc",
        topic="shipment_cdc_events",
        schema_strategy="cdc_envelope_json",
        max_record_error_ratio=0.25,
    )
    foundry.datasets.ensure("raw.shipment_cdc", ctx=ctx, primary_key=["event_id"])
    _publish_cdc_shipment(dependencies.stream_adapter, "S-bad", ctx=ctx, payload=_invalid_after_payload("S-bad"))
    _publish_cdc_shipment(dependencies.stream_adapter, "S-100", ctx=ctx)

    with pytest.raises(ValidationFailed, match="record error ratio exceeds threshold"):
        foundry.datasets.archive_stream_events("raw.shipment_cdc", stream=stream, ctx=ctx)

    dead_letters = _dead_letter_records(dependencies, ctx)
    failed_runs = foundry.operations.query_runs(ctx=ctx, run_type="sync", status="FAILED")["syncRuns"]
    assert foundry.datasets.list_versions("raw.shipment_cdc", ctx=ctx) == []
    assert len(dead_letters) == 1
    assert dead_letters[0]["error_kind"] == "VALIDATION_FAILED"
    assert failed_runs[0]["error"]["type"] == "VALIDATION_FAILED"


def test_identity_error_fails_closed(tmp_path: Path) -> None:
    foundry, dependencies = _core_with_stream(tmp_path)
    ctx = demo_admin_context()
    stream = StreamArchiveConfig(
        stream_name="shipment_cdc",
        topic="shipment_cdc_events",
        schema_strategy="cdc_envelope_json",
    )
    foundry.datasets.ensure("raw.shipment_cdc", ctx=ctx, primary_key=["event_id"])
    _publish_cdc_shipment(dependencies.stream_adapter, "S-bad", ctx=ctx, payload={"op": "u", "pk": "bad"})
    _publish_cdc_shipment(dependencies.stream_adapter, "S-100", ctx=ctx)

    with pytest.raises(ValidationFailed, match="record error kind is fail-closed"):
        foundry.datasets.archive_stream_events("raw.shipment_cdc", stream=stream, ctx=ctx)

    dead_letters = _dead_letter_records(dependencies, ctx)
    assert foundry.datasets.list_versions("raw.shipment_cdc", ctx=ctx) == []
    assert len(dead_letters) == 1
    assert dead_letters[0]["error_kind"] == "IDENTITY_ERROR"


def test_too_late_event_goes_to_record_dlq(tmp_path: Path) -> None:
    foundry, dependencies = _core_with_stream(tmp_path)
    ctx = demo_admin_context()
    stream = StreamArchiveConfig(
        stream_name="shipments",
        topic="shipment_events",
        event_time_field="published_at",
        source_time_field="source_seen_at",
        allowed_lateness_seconds=3600,
        too_late_after_seconds=7200,
    )
    foundry.datasets.ensure("raw.shipment_events", ctx=ctx, primary_key=["event_id"])
    too_late_at = _iso_seconds_ago(3 * 3600)
    _publish_shipment(
        dependencies.stream_adapter,
        "S-too-late",
        ctx=ctx,
        payload={"shipment_id": "S-too-late", "status": "IN_TRANSIT", "published_at": too_late_at},
    )
    _publish_shipment(
        dependencies.stream_adapter,
        "S-100",
        ctx=ctx,
        payload={"shipment_id": "S-100", "status": "IN_TRANSIT", "published_at": _iso_seconds_ago(60)},
    )

    result = foundry.datasets.archive_stream_events("raw.shipment_events", stream=stream, ctx=ctx)

    dead_letters = _dead_letter_records(dependencies, ctx)
    preview = foundry.datasets.preview("raw.shipment_events", ctx=ctx)
    assert result is not None
    assert result.row_count == 1
    assert [row["source_event_id"] for row in dead_letters] == ["shipment_events:0:0"]
    assert dead_letters[0]["error_kind"] == "TOO_LATE"
    assert dead_letters[0]["event_time"] == too_late_at
    assert dead_letters[0]["metadata"]["event_time_field"] == "published_at"
    assert preview[0]["event_id"] == "shipment_events:0:1"


def test_event_time_and_processing_time_sla_are_separate(tmp_path: Path) -> None:
    foundry, dependencies = _core_with_stream(tmp_path)
    ctx = demo_admin_context()
    stream = StreamArchiveConfig(stream_name="shipments", topic="shipment_events")
    foundry.datasets.ensure("raw.shipment_events", ctx=ctx, primary_key=["event_id"])
    event_time = _iso_seconds_ago(60)
    source_time = _iso_seconds_ago(30)
    _publish_shipment(
        dependencies.stream_adapter,
        "S-100",
        ctx=ctx,
        payload={
            "shipment_id": "S-100",
            "status": "IN_TRANSIT",
            "event_time": event_time,
            "source_time": source_time,
        },
    )

    foundry.datasets.archive_stream_events("raw.shipment_events", stream=stream, ctx=ctx)

    row = foundry.datasets.preview("raw.shipment_events", ctx=ctx)[0]
    assert _same_instant(row["event_time"], event_time)
    assert _same_instant(row["source_time"], source_time)
    assert row["ingested_at"] == row["processed_at"]
    assert row["processed_at"] != event_time
    assert row["event_time_lag_seconds"] >= 0
    assert row["late_data_status"] == "ON_TIME"


def test_watermark_never_moves_backward(tmp_path: Path) -> None:
    foundry, dependencies = _core_with_stream(tmp_path)
    ctx = demo_admin_context()
    stream = StreamArchiveConfig(
        stream_name="shipments",
        topic="shipment_events",
        allowed_lateness_seconds=3600,
        too_late_after_seconds=86400,
    )
    foundry.datasets.ensure("raw.shipment_events", ctx=ctx, primary_key=["event_id"])
    _publish_shipment(
        dependencies.stream_adapter,
        "S-100",
        ctx=ctx,
        payload={"shipment_id": "S-100", "status": "IN_TRANSIT", "event_time": _iso_seconds_ago(60)},
    )
    foundry.datasets.archive_stream_events("raw.shipment_events", stream=stream, ctx=ctx)
    first = _latest_stream_transaction(foundry, dependencies, ctx, "raw.shipment_events")
    first_watermark = first["metadata"]["lateDataWatermark"]["watermarkEventTime"]
    _publish_shipment(
        dependencies.stream_adapter,
        "S-late",
        ctx=ctx,
        payload={"shipment_id": "S-late", "status": "IN_TRANSIT", "event_time": _iso_seconds_ago(1800)},
    )

    foundry.datasets.archive_stream_events("raw.shipment_events", stream=stream, ctx=ctx)

    latest = _latest_stream_transaction(foundry, dependencies, ctx, "raw.shipment_events")
    preview = foundry.datasets.preview("raw.shipment_events", ctx=ctx)
    assert latest["metadata"]["lateDataWatermark"]["watermarkEventTime"] == first_watermark
    assert preview[0]["late_data_status"] == "LATE_REQUIRES_REPROCESS"


def test_duplicate_late_event_is_idempotent(tmp_path: Path) -> None:
    foundry, dependencies = _core_with_stream(tmp_path)
    ctx = demo_admin_context()
    stream = StreamArchiveConfig(
        stream_name="shipments",
        topic="shipment_events",
        allowed_lateness_seconds=3600,
        too_late_after_seconds=86400,
    )
    foundry.datasets.ensure("raw.shipment_events", ctx=ctx, primary_key=["event_id"])
    _publish_shipment(
        dependencies.stream_adapter,
        "S-100",
        ctx=ctx,
        payload={"shipment_id": "S-100", "status": "IN_TRANSIT", "event_time": _iso_seconds_ago(60)},
    )
    foundry.datasets.archive_stream_events("raw.shipment_events", stream=stream, ctx=ctx)
    _publish_shipment(
        dependencies.stream_adapter,
        "S-late",
        ctx=ctx,
        payload={"shipment_id": "S-late", "status": "IN_TRANSIT", "event_time": _iso_seconds_ago(1800)},
    )

    late_commit = foundry.datasets.archive_stream_events("raw.shipment_events", stream=stream, ctx=ctx)
    duplicate_delivery = foundry.datasets.archive_stream_events("raw.shipment_events", stream=stream, ctx=ctx)

    latest = _latest_stream_transaction(foundry, dependencies, ctx, "raw.shipment_events")
    preview = foundry.datasets.preview("raw.shipment_events", ctx=ctx)
    versions = foundry.datasets.list_versions("raw.shipment_events", ctx=ctx)
    assert late_commit is not None
    assert late_commit.row_count == 1
    assert duplicate_delivery is None
    assert len(versions) == 2
    assert latest["metadata"]["streamCursor"]["offset"] == 1
    assert preview[0]["event_id"] == "shipment_events:0:1"
    assert preview[0]["late_data_status"] == "LATE_REQUIRES_REPROCESS"


def test_late_event_creates_reprocessing_plan_for_closed_archive_output(tmp_path: Path) -> None:
    foundry, dependencies = _core_with_stream(tmp_path)
    ctx = demo_admin_context()
    stream = StreamArchiveConfig(
        stream_name="shipments",
        topic="shipment_events",
        allowed_lateness_seconds=3600,
        too_late_after_seconds=86400,
    )
    foundry.datasets.ensure("raw.shipment_events", ctx=ctx, primary_key=["event_id"])
    _publish_shipment(
        dependencies.stream_adapter,
        "S-100",
        ctx=ctx,
        payload={"shipment_id": "S-100", "status": "IN_TRANSIT", "event_time": _iso_seconds_ago(60)},
    )
    first = foundry.datasets.archive_stream_events("raw.shipment_events", stream=stream, ctx=ctx)
    late_event_time = _iso_seconds_ago(1800)
    _publish_shipment(
        dependencies.stream_adapter,
        "S-late",
        ctx=ctx,
        payload={"shipment_id": "S-late", "status": "IN_TRANSIT", "event_time": late_event_time},
    )

    foundry.datasets.archive_stream_events("raw.shipment_events", stream=stream, ctx=ctx)

    latest = _latest_stream_transaction(foundry, dependencies, ctx, "raw.shipment_events")
    plan = latest["metadata"]["lateDataReprocessingPlan"]
    assert first is not None
    assert plan["status"] == "REPROCESS_REQUIRED"
    assert plan["previousCommittedDatasetVersionId"] == first.version_id
    assert plan["affectedClosedOutputs"] == [
        {"resourceType": "dataset_version", "resourceId": first.version_id, "relation": "closed_archive"}
    ]
    assert plan["lateEventIds"] == ["shipment_events:0:1"]
    assert _same_instant(plan["reprocessFromEventTime"], late_event_time)


def test_run_detail_shows_event_time_lag_and_reprocessing_plan(tmp_path: Path) -> None:
    foundry, dependencies = _core_with_stream(tmp_path)
    ctx = demo_admin_context()
    stream = StreamArchiveConfig(
        stream_name="shipments",
        topic="shipment_events",
        allowed_lateness_seconds=3600,
        too_late_after_seconds=86400,
    )
    foundry.datasets.ensure("raw.shipment_events", ctx=ctx, primary_key=["event_id"])
    _publish_shipment(
        dependencies.stream_adapter,
        "S-100",
        ctx=ctx,
        payload={"shipment_id": "S-100", "status": "IN_TRANSIT", "event_time": _iso_seconds_ago(60)},
    )
    foundry.datasets.archive_stream_events("raw.shipment_events", stream=stream, ctx=ctx)
    _publish_shipment(
        dependencies.stream_adapter,
        "S-late",
        ctx=ctx,
        payload={"shipment_id": "S-late", "status": "IN_TRANSIT", "event_time": _iso_seconds_ago(1800)},
    )

    late_commit = foundry.datasets.archive_stream_events("raw.shipment_events", stream=stream, ctx=ctx)

    assert late_commit is not None
    run = _sync_run_for_version(foundry, ctx, late_commit.version_id)
    detail = foundry.operations.run_detail("sync", str(run["id"]), ctx=ctx)
    assert detail["datasetTransaction"]["committed_version_id"] == late_commit.version_id
    assert detail["lateData"]["summary"]["requiresReprocessing"] is True
    assert detail["lateData"]["summary"]["maxEventTimeLagSeconds"] >= 1800
    assert detail["lateData"]["reprocessingPlan"]["previousCommittedDatasetVersionId"] is not None


def test_stream_archive_dead_letter_store_failure_fails_main_pipeline(tmp_path: Path) -> None:
    foundry, dependencies = _core_with_stream(tmp_path)
    failing_foundry = FoundryLite(
        dependencies=replace(
            dependencies,
            dataset_transaction_repository=_FailingDeadLetterRepository(dependencies.dataset_transaction_repository),
        )
    )
    ctx = demo_admin_context()
    stream = StreamArchiveConfig(
        stream_name="shipment_cdc",
        topic="shipment_cdc_events",
        schema_strategy="cdc_envelope_json",
    )
    foundry.datasets.ensure("raw.shipment_cdc", ctx=ctx, primary_key=["event_id"])
    _publish_cdc_shipment(dependencies.stream_adapter, "S-bad", ctx=ctx, payload={"op": "u", "pk": "bad"})
    _publish_cdc_shipment(dependencies.stream_adapter, "S-100", ctx=ctx)

    with pytest.raises(ValidationFailed, match="stream archive failed"):
        failing_foundry.datasets.archive_stream_events("raw.shipment_cdc", stream=stream, ctx=ctx)

    assert _dead_letter_records(dependencies, ctx) == []
    assert foundry.datasets.list_versions("raw.shipment_cdc", ctx=ctx) == []
    failed_runs = failing_foundry.operations.query_runs(ctx=ctx, run_type="sync", status="FAILED")["syncRuns"]
    assert _run_error_adapter(failed_runs[0]) == "stream_archive_writer"


def test_operations_record_dead_letter_replay_request_is_idempotent_and_audited(tmp_path: Path) -> None:
    foundry, dependencies = _core_with_stream(tmp_path)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.shipment_cdc", ctx=ctx, primary_key=["event_id"])
    _insert_dead_letter_record(dependencies, ctx, record_id="dlqr_ops_retry", source_event_id="shipment_cdc_events:0:9")
    _insert_dead_letter_record(
        dependencies,
        ctx,
        record_id="dlqr_ops_discard",
        source_event_id="shipment_cdc_events:0:10",
        payload_hash="payload-hash-discard",
    )

    listed = foundry.operations.list_dead_letter_records(ctx=ctx, status="QUARANTINED")
    detail = foundry.operations.dead_letter_record("dlqr_ops_retry", ctx=ctx)
    retry = foundry.operations.retry_dead_letter_record(
        "dlqr_ops_retry",
        idempotency_key="record-replay-key",
        ctx=ctx,
    )
    duplicate = foundry.operations.retry_dead_letter_record(
        "dlqr_ops_retry",
        idempotency_key="record-replay-key",
        ctx=ctx,
    )
    discard = foundry.operations.discard_dead_letter_record("dlqr_ops_discard", ctx=ctx)
    audits = foundry.operations.query_runs(ctx=ctx, run_type="audit")["auditEvents"]
    preview = foundry.datasets.preview("raw.shipment_cdc", ctx=ctx)

    assert {row["id"] for row in listed} == {"dlqr_ops_retry", "dlqr_ops_discard"}
    assert detail["source_event_id"] == "shipment_cdc_events:0:9"
    assert retry["replayStatus"] == "SUCCEEDED"
    assert retry["replayDatasetVersionId"] is not None
    assert duplicate["is_idempotent_replay"] is True
    assert duplicate["replayRunId"] == retry["replayRunId"]
    assert duplicate["replayDatasetVersionId"] == retry["replayDatasetVersionId"]
    assert discard["status"] == "DISCARDED"
    assert preview[0]["event_id"] == "shipment_cdc_events:0:9"
    assert any(event["event_type"] == "dead_letter_record.replay_requested" for event in audits)
    assert any(event["event_type"] == "dead_letter_record.replayed" for event in audits)
    assert any(event["event_type"] == "dead_letter_record.discarded" for event in audits)
    with pytest.raises(ConflictDetected):
        foundry.operations.retry_dead_letter_record("dlqr_ops_retry", idempotency_key="different-key", ctx=ctx)


def test_operations_record_dead_letter_replay_failure_is_visible(tmp_path: Path) -> None:
    foundry, dependencies = _core_with_stream(tmp_path)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.shipment_cdc", ctx=ctx, primary_key=["event_id"])
    _insert_dead_letter_record(
        dependencies,
        ctx,
        record_id="dlqr_ops_invalid",
        source_event_id="shipment_cdc_events:0:11",
        payload={"op": "u", "pk": "bad"},
    )

    retry = foundry.operations.retry_dead_letter_record(
        "dlqr_ops_invalid",
        idempotency_key="record-replay-fail-key",
        ctx=ctx,
    )
    duplicate = foundry.operations.retry_dead_letter_record(
        "dlqr_ops_invalid",
        idempotency_key="record-replay-fail-key",
        ctx=ctx,
    )
    audits = foundry.operations.query_runs(ctx=ctx, run_type="audit")["auditEvents"]

    assert retry["status"] == "QUARANTINED"
    assert retry["replayStatus"] == "FAILED"
    assert retry["replayDatasetVersionId"] is None
    assert retry["rowCount"] is None
    assert retry["error"] is not None
    assert retry["error"]["type"] == "ValidationFailed"
    assert duplicate["is_idempotent_replay"] is True
    assert duplicate["replayRunId"] == retry["replayRunId"]
    assert foundry.datasets.list_versions("raw.shipment_cdc", ctx=ctx) == []
    assert any(event["event_type"] == "dead_letter_record.replay_failed" for event in audits)


def test_same_replay_idempotency_key_returns_existing_result(tmp_path: Path) -> None:
    foundry, dependencies = _core_with_stream(tmp_path)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.shipment_cdc", ctx=ctx, primary_key=["event_id"])
    _insert_dead_letter_record(dependencies, ctx, record_id="dlqr_same_key", source_event_id="shipment_cdc_events:0:12")
    replay_key = "same-replay-key"

    first = foundry.operations.retry_dead_letter_record("dlqr_same_key", idempotency_key=replay_key, ctx=ctx)
    second = foundry.operations.retry_dead_letter_record("dlqr_same_key", idempotency_key=replay_key, ctx=ctx)

    assert first["replayStatus"] == "SUCCEEDED"
    assert second["is_idempotent_replay"] is True
    assert second["replayRunId"] == first["replayRunId"]
    assert second["replayDatasetVersionId"] == first["replayDatasetVersionId"]


def test_same_replay_idempotency_key_resumes_after_requested_state_crash(tmp_path: Path) -> None:
    foundry, dependencies = _core_with_stream(tmp_path)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.shipment_cdc", ctx=ctx, primary_key=["event_id"])
    _insert_dead_letter_record(
        dependencies,
        ctx,
        record_id="dlqr_resume_key",
        source_event_id="shipment_cdc_events:0:13",
    )
    replay_key = "resume-replay-key"
    with dependencies.engine.begin() as transaction:
        row = dependencies.dataset_transaction_repository.dead_letter_record_by_id(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            record_id="dlqr_resume_key",
        )
        assert row is not None
        dependencies.dataset_transaction_repository.update_dead_letter_record_replay_requested(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            record_id="dlqr_resume_key",
            replay_run_id="sync_run_resume_dlq",
            replay_idempotency_key=replay_key,
            replay_requested_at="2026-06-10T00:04:00Z",
            metadata={**dict(row["metadata"]), "lastOperation": {"operation": "retry"}},
            backfill_plan={
                "affectedDatasetVersionId": None,
                "is_closed_partition_affected": False,
                "nextStep": "resume",
            },
        )

    resumed = foundry.operations.retry_dead_letter_record("dlqr_resume_key", idempotency_key=replay_key, ctx=ctx)
    duplicate = foundry.operations.retry_dead_letter_record("dlqr_resume_key", idempotency_key=replay_key, ctx=ctx)
    preview = foundry.datasets.preview("raw.shipment_cdc", ctx=ctx)

    assert resumed["replayStatus"] == "SUCCEEDED"
    assert resumed["is_idempotent_replay"] is False
    assert resumed["replayRunId"] == "sync_run_resume_dlq"
    assert duplicate["is_idempotent_replay"] is True
    assert preview[0]["event_id"] == "shipment_cdc_events:0:13"


def test_dead_letter_payload_is_replayable(tmp_path: Path) -> None:
    foundry, dependencies = _core_with_stream(tmp_path)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.shipment_cdc", ctx=ctx, primary_key=["event_id"])
    _insert_dead_letter_record(dependencies, ctx, record_id="dlqr_payload", source_event_id="shipment_cdc_events:0:13")

    replay = foundry.operations.retry_dead_letter_record("dlqr_payload", idempotency_key="payload-replay-key", ctx=ctx)
    preview = foundry.datasets.preview("raw.shipment_cdc", ctx=ctx)

    assert replay["replayStatus"] == "SUCCEEDED"
    assert replay["rowCount"] == 1
    assert preview[0]["event_id"] == "shipment_cdc_events:0:13"


def test_replayed_record_is_marked_with_origin(tmp_path: Path) -> None:
    foundry, dependencies = _core_with_stream(tmp_path)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.shipment_cdc", ctx=ctx, primary_key=["event_id"])
    _insert_dead_letter_record(dependencies, ctx, record_id="dlqr_origin", source_event_id="shipment_cdc_events:0:14")

    replay = foundry.operations.retry_dead_letter_record("dlqr_origin", idempotency_key="origin-replay-key", ctx=ctx)
    latest = _latest_stream_transaction(foundry, dependencies, ctx, "raw.shipment_cdc")

    assert replay["originDeadLetterRecordId"] == "dlqr_origin"
    assert latest["metadata"]["recordDlqReplay"]["deadLetterRecordId"] == "dlqr_origin"
    assert latest["metadata"]["recordDlqReplay"]["originSourceEventId"] == "shipment_cdc_events:0:14"


def test_replay_emits_downstream_backfill_plan(tmp_path: Path) -> None:
    foundry, dependencies = _core_with_stream(tmp_path)
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.shipment_cdc", ctx=ctx, primary_key=["event_id"])
    _insert_dead_letter_record(dependencies, ctx, record_id="dlqr_backfill", source_event_id="shipment_cdc_events:0:15")

    replay = foundry.operations.retry_dead_letter_record(
        "dlqr_backfill",
        idempotency_key="backfill-replay-key",
        ctx=ctx,
    )

    assert replay["downstreamBackfillPlan"]["affectedDatasetVersionId"] is None
    assert replay["downstreamBackfillPlan"]["is_closed_partition_affected"] is False
    assert "Replay worker" in replay["downstreamBackfillPlan"]["nextStep"]


def _core_with_stream(tmp_path: Path) -> tuple[FoundryLite, CoreDependencies]:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    return FoundryLite(dependencies=dependencies), dependencies


def _publish_shipment(
    stream_adapter: StreamAdapter,
    shipment_id: str,
    *,
    ctx: RequestContext,
    payload: dict[str, object] | None = None,
) -> None:
    stream_adapter.publish_event(
        StreamPublishRequest(
            stream_name="shipments",
            event_type="shipment.updated",
            tenant_id=ctx.tenant_id,
            request_id=ctx.request_id,
            key=shipment_id,
            payload=payload or {"shipment_id": shipment_id, "status": "IN_TRANSIT"},
        )
    )


def _publish_cdc_shipment(
    stream_adapter: StreamAdapter,
    shipment_id: str,
    *,
    ctx: RequestContext,
    payload: dict[str, object] | None = None,
) -> None:
    stream_adapter.publish_event(
        StreamPublishRequest(
            stream_name="shipment_cdc",
            event_type="shipment.changed",
            tenant_id=ctx.tenant_id,
            request_id=ctx.request_id,
            key=shipment_id,
            payload=payload
            or {
                "op": "u",
                "pk": {"shipment_id": shipment_id},
                "before": None,
                "after": {"shipment_id": shipment_id, "status": "IN_TRANSIT"},
                "ordering": {"lsn": shipment_id},
            },
        )
    )


def _invalid_after_payload(shipment_id: str) -> dict[str, object]:
    return {
        "op": "u",
        "pk": {"shipment_id": shipment_id},
        "before": None,
        "after": "bad",
        "ordering": {"lsn": shipment_id},
    }


def _iso_seconds_ago(seconds: int) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


def _same_instant(left: object, right: object) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    left_at = datetime.fromisoformat(left.replace("Z", "+00:00")).astimezone(UTC)
    right_at = datetime.fromisoformat(right.replace("Z", "+00:00")).astimezone(UTC)
    return left_at == right_at


def _latest_stream_transaction(
    foundry: FoundryLite,
    dependencies: CoreDependencies,
    ctx: RequestContext,
    dataset_ref: str,
) -> dict[str, Any]:
    dataset = foundry.datasets.get(dataset_ref, ctx=ctx)
    with dependencies.engine.begin() as transaction:
        row = dependencies.dataset_transaction_repository.latest_committed_transaction(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            dataset_id=dataset["id"],
        )
    assert row is not None
    return dict(row)


def _dead_letter_records(dependencies: CoreDependencies, ctx: RequestContext) -> list[dict[str, Any]]:
    with dependencies.engine.begin() as transaction:
        rows = dependencies.dataset_transaction_repository.list_dead_letter_records(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
        )
    return [dict(row) for row in rows]


def _sync_run_for_version(foundry: FoundryLite, ctx: RequestContext, version_id: str) -> dict[str, Any]:
    runs = foundry.operations.query_runs(ctx=ctx, run_type="sync")["syncRuns"]
    row = next(run for run in runs if run["committed_version_id"] == version_id)
    return dict(row)


def _insert_dead_letter_record(
    dependencies: CoreDependencies,
    ctx: RequestContext,
    *,
    record_id: str,
    source_event_id: str,
    payload_hash: str = "payload-hash-ops",
    payload: dict[str, object] | None = None,
) -> None:
    offset = int(source_event_id.rsplit(":", maxsplit=1)[-1])
    replay_payload = payload or {
        "op": "u",
        "pk": {"shipment_id": record_id},
        "before": None,
        "after": {"shipment_id": record_id, "status": "IN_TRANSIT"},
        "ordering": {"lsn": record_id},
    }
    with dependencies.engine.begin() as transaction:
        dependencies.dataset_transaction_repository.insert_dead_letter_record(
            transaction=transaction,
            record=DeadLetterRecord(
                dead_letter_record_id=record_id,
                tenant_id=ctx.tenant_id,
                source_event_id=source_event_id,
                source_dataset_version_id=None,
                source_run_id="sync_stream_ops",
                payload=replay_payload,
                payload_hash=payload_hash,
                schema_version=1,
                transform_version=None,
                error_kind="VALIDATION_FAILED",
                error_message="cdc envelope field must be an object",
                event_time=None,
                ingested_at="2026-06-10T00:03:00Z",
                first_failed_at="2026-06-10T00:03:00Z",
                attempts=1,
                status="QUARANTINED",
                replay_status="NOT_REQUESTED",
                replay_run_id=None,
                is_closed_partition_affected=False,
                metadata={
                    "dataset_id": "ds_shipment_cdc",
                    "dataset_ref": "raw.shipment_cdc",
                    "schema_strategy": "cdc_envelope_json",
                    "stream": "shipment_cdc",
                    "topic": "shipment_cdc_events",
                    "consumer_group": "foundry-lite-archive",
                    "partition": 0,
                    "offset": offset,
                    "event_type": "shipment.changed",
                    "event_key": record_id,
                    "request_id": ctx.request_id,
                },
            ),
        )


def _run_error_adapter(row: Any) -> object:
    error = row["error"]
    assert isinstance(error, dict)
    trace = error["trace"]
    assert isinstance(trace, dict)
    return trace["adapter"]


class _ExplodingRowsComputeAdapter(DuckDBComputeAdapter):
    def rows_to_parquet(self, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("stream archive parquet write failed")


class _FailingDeadLetterRepository:
    def __init__(self, wrapped: object) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    def insert_dead_letter_record(self, *, transaction: object, record: DeadLetterRecord) -> bool:
        del transaction, record
        raise RuntimeError("dead letter record store unavailable")
