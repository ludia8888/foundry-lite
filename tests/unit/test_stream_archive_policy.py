from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

import pytest
from foundry_lite.application.ports import StreamArchiveConfig, StreamEvent
from foundry_lite.application.services.dataset.stream_archive import (
    ensure_stream_archive_batch_writable,
    prepare_stream_archive_batch,
    stream_archive_limit,
    stream_transaction_metadata,
)
from foundry_lite.application.services.dataset.stream_archive_time import validate_late_data_policy
from foundry_lite.domain.errors import ValidationFailed


def test_stream_archive_policy_classifies_late_accepted_event() -> None:
    stream = StreamArchiveConfig(
        stream_name="shipments",
        topic="shipment_events",
        allowed_lateness_seconds=3600,
        clock_skew_seconds=60,
    )
    event_time = _iso_seconds_ago(600)

    batch = prepare_stream_archive_batch([_event({"event_time": event_time})], stream)
    metadata = stream_transaction_metadata(stream, [_event({"event_time": event_time})], rows=batch.rows)
    watermark = cast(Mapping[str, object], metadata["lateDataWatermark"])

    assert batch.rows[0]["late_data_status"] == "LATE_ACCEPTED"
    assert watermark["eventTimeField"] == "event_time"
    assert watermark["watermarkEventTime"] == datetime.fromisoformat(event_time).isoformat()


@pytest.mark.parametrize(
    "stream",
    [
        StreamArchiveConfig(stream_name="shipments", topic="shipment_events", event_time_field=""),
        StreamArchiveConfig(stream_name="shipments", topic="shipment_events", source_time_field=""),
        StreamArchiveConfig(stream_name="shipments", topic="shipment_events", time_zone="Mars/Colony"),
        StreamArchiveConfig(stream_name="shipments", topic="shipment_events", allowed_lateness_seconds=-1),
        StreamArchiveConfig(
            stream_name="shipments",
            topic="shipment_events",
            allowed_lateness_seconds=60,
            too_late_after_seconds=30,
        ),
    ],
)
def test_stream_archive_late_data_policy_rejects_invalid_config(stream: StreamArchiveConfig) -> None:
    with pytest.raises(ValidationFailed):
        validate_late_data_policy(stream)


def test_stream_archive_time_parse_errors_become_dead_letters() -> None:
    stream = StreamArchiveConfig(stream_name="shipments", topic="shipment_events")

    batch = prepare_stream_archive_batch([_event({"event_time": "not-a-date"}), _event({"event_time": 123})], stream)

    assert [row.error_kind for row in batch.dead_letters] == ["VALIDATION_FAILED", "VALIDATION_FAILED"]
    with pytest.raises(ValidationFailed, match="no valid records"):
        ensure_stream_archive_batch_writable(batch, stream, total_events=2)


def test_future_event_time_beyond_clock_skew_becomes_dead_letter() -> None:
    stream = StreamArchiveConfig(stream_name="shipments", topic="shipment_events", clock_skew_seconds=60)
    future_event_time = (datetime.now(UTC) + timedelta(seconds=300)).isoformat()

    batch = prepare_stream_archive_batch([_event({"event_time": future_event_time})], stream)

    assert batch.rows == []
    assert len(batch.dead_letters) == 1
    assert batch.dead_letters[0].error_kind == "FUTURE_CLOCK_SKEW"
    assert batch.dead_letters[0].event_time == future_event_time
    with pytest.raises(ValidationFailed, match="no valid records"):
        ensure_stream_archive_batch_writable(batch, stream, total_events=1)


def test_stream_archive_tenant_mismatch_becomes_dead_letter() -> None:
    stream = StreamArchiveConfig(stream_name="shipments", topic="shipment_events")

    batch = prepare_stream_archive_batch([_event({})], stream, expected_tenant_id="tenant-other")

    assert batch.rows == []
    assert len(batch.dead_letters) == 1
    assert batch.dead_letters[0].error_kind == "TENANT_MISMATCH"
    assert batch.dead_letters[0].error_message == "stream event tenant does not match request tenant"


def test_stream_archive_watermark_keeps_previous_when_batch_has_no_event_time() -> None:
    stream = StreamArchiveConfig(stream_name="shipments", topic="shipment_events")
    previous = {
        "lateDataWatermark": {
            "streamName": "shipments",
            "topic": "shipment_events",
            "partition": 0,
            "consumerGroup": "foundry-lite-archive",
            "watermarkEventTime": "2026-06-18T00:00:00+00:00",
        }
    }

    batch = prepare_stream_archive_batch([_event({})], stream, previous)
    metadata = stream_transaction_metadata(stream, [_event({})], previous, batch.rows)
    watermark = cast(Mapping[str, object], metadata["lateDataWatermark"])

    assert batch.rows[0]["event_time"] is None
    assert batch.rows[0]["event_time_lag_seconds"] is None
    assert watermark["watermarkEventTime"] == "2026-06-18T00:00:00+00:00"


def test_slow_partition_does_not_silently_drop_data() -> None:
    stream = StreamArchiveConfig(
        stream_name="shipments",
        topic="shipment_events",
        partition=1,
        allowed_lateness_seconds=3600,
        too_late_after_seconds=86400,
    )
    previous_fast_partition = {
        "lateDataWatermark": {
            "streamName": "shipments",
            "topic": "shipment_events",
            "partition": 0,
            "consumerGroup": "foundry-lite-archive",
            "watermarkEventTime": _iso_seconds_ago(60),
        }
    }
    slow_event_time = _iso_seconds_ago(1800)

    batch = prepare_stream_archive_batch([_event({"event_time": slow_event_time})], stream, previous_fast_partition)
    metadata = stream_transaction_metadata(
        stream,
        [_event({"event_time": slow_event_time})],
        previous_fast_partition,
        batch.rows,
    )
    watermark = cast(Mapping[str, object], metadata["lateDataWatermark"])

    assert batch.rows[0]["late_data_status"] == "LATE_ACCEPTED"
    assert watermark["partition"] == 1
    assert watermark["watermarkEventTime"] == datetime.fromisoformat(slow_event_time).isoformat()


def test_stream_archive_naive_event_time_defaults_to_utc() -> None:
    stream = StreamArchiveConfig(stream_name="shipments", topic="shipment_events", clock_skew_seconds=0)
    event_time = datetime.now(UTC).replace(tzinfo=None).isoformat()

    batch = prepare_stream_archive_batch([_event({"event_time": event_time})], stream)
    lag_seconds = batch.rows[0]["event_time_lag_seconds"]

    assert batch.rows[0]["event_time"] == event_time
    assert isinstance(lag_seconds, int)
    assert lag_seconds >= 0


def test_late_data_named_timezone_override_applies_source_policy() -> None:
    stream = StreamArchiveConfig(
        stream_name="shipments",
        topic="shipment_events",
        time_zone="America/Los_Angeles",
        clock_skew_seconds=3600,
        allowed_lateness_seconds=7200,
        too_late_after_seconds=10800,
    )
    source_local_time = datetime.now(ZoneInfo("America/Los_Angeles")).replace(tzinfo=None).isoformat()

    batch = prepare_stream_archive_batch([_event({"event_time": source_local_time})], stream)
    metadata = stream_transaction_metadata(stream, [_event({"event_time": source_local_time})], rows=batch.rows)
    watermark = cast(Mapping[str, object], metadata["lateDataWatermark"])
    event_time = datetime.fromisoformat(str(batch.rows[0]["event_time"]))

    assert batch.dead_letters == []
    assert batch.rows[0]["late_data_status"] == "ON_TIME"
    assert event_time.tzinfo is not None
    assert abs((datetime.now(UTC) - event_time).total_seconds()) < 60
    assert watermark["timeZone"] == "America/Los_Angeles"


def test_stream_archive_limit_rejects_out_of_range_values() -> None:
    with pytest.raises(ValidationFailed, match="positive"):
        stream_archive_limit(0)
    with pytest.raises(ValidationFailed, match="exceeds maximum"):
        stream_archive_limit(501)


def _event(payload: dict[str, object]) -> StreamEvent:
    return StreamEvent(
        stream_name="shipments",
        offset=0,
        event_type="shipment.updated",
        tenant_id="tenant_demo",
        request_id="req_demo",
        key="S-100",
        payload={"shipment_id": "S-100", "status": "IN_TRANSIT", **payload},
    )


def _iso_seconds_ago(seconds: int) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()
