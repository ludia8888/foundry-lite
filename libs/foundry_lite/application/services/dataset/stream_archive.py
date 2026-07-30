"""Application service helpers for stream archive workflows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from foundry_lite.application.ports import (
    DatasetRow,
    DeadLetterRecord,
    ParquetFieldType,
    StreamAdapter,
    StreamArchiveConfig,
    StreamEvent,
)
from foundry_lite.application.primitives import _json_hash, _now
from foundry_lite.application.services.dataset.ingest_models import UploadSyncPlan
from foundry_lite.application.services.dataset.stream_archive_time import (
    event_time_lag_seconds,
    late_data_status,
    late_data_watermark_metadata,
    next_late_data_watermark,
    payload_named_time,
    previous_stream_watermarks,
    stream_watermark_key,
    validate_late_data_policy,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.observability.metrics import set_stream_archive_lag

STREAM_ARCHIVE_MAX_LIMIT = 500
STREAM_ARCHIVE_FIELDS = [
    "event_id",
    "stream",
    "topic",
    "partition",
    "offset",
    "event_type",
    "event_key",
    "tenant_id",
    "request_id",
    "payload_json",
    "event_time",
    "source_time",
    "ingested_at",
    "processed_at",
    "late_data_status",
    "event_time_lag_seconds",
]
CDC_STREAM_ARCHIVE_FIELDS = [*STREAM_ARCHIVE_FIELDS, "op", "pk_json", "before_json", "after_json", "ordering_json"]
STREAM_ARCHIVE_FIELD_TYPES: Mapping[str, ParquetFieldType] = {
    "event_id": "string",
    "stream": "string",
    "topic": "string",
    "partition": "int64",
    "offset": "int64",
    "event_type": "string",
    "event_key": "string",
    "tenant_id": "string",
    "request_id": "string",
    "payload_json": "string",
    "event_time": "string",
    "source_time": "string",
    "ingested_at": "string",
    "processed_at": "string",
    "late_data_status": "string",
    "event_time_lag_seconds": "float64",
    "op": "string",
    "pk_json": "string",
    "before_json": "string",
    "after_json": "string",
    "ordering_json": "string",
}


@dataclass(frozen=True)
class StreamArchiveDeadLetter:
    event: StreamEvent
    source_event_id: str
    payload_hash: str
    error_kind: str
    error_message: str
    event_time: str | None


@dataclass(frozen=True)
class StreamArchivePreparedBatch:
    rows: list[Mapping[str, object]]
    dead_letters: list[StreamArchiveDeadLetter]


def read_stream_archive_events(
    adapter: StreamAdapter,
    stream: StreamArchiveConfig,
    resume_offset: int | None,
) -> list[StreamEvent]:
    query_limit = stream_archive_limit(stream.limit)
    read_events = adapter.read_events(stream.stream_name, after_offset=resume_offset, limit=query_limit + 1)
    events = read_events[:query_limit]
    set_stream_archive_lag(max(0, len(read_events) - len(events)))
    return events


def prepare_stream_archive_batch(
    events: Sequence[StreamEvent],
    stream: StreamArchiveConfig,
    previous_metadata: Mapping[str, object] | None = None,
    *,
    expected_tenant_id: str | None = None,
) -> StreamArchivePreparedBatch:
    _validate_error_threshold(stream.max_record_error_ratio)
    validate_late_data_policy(stream)
    rows: list[Mapping[str, object]] = []
    dead_letters: list[StreamArchiveDeadLetter] = []
    for event in events:
        source_event_id = stream_event_id(event, stream)
        try:
            _validate_event_tenant(event, expected_tenant_id)
            rows.append(stream_event_row(event, stream, previous_metadata))
        except ValidationFailed as exc:
            dead_letters.append(_dead_letter_stream_event(event, source_event_id, stream, exc))
    return StreamArchivePreparedBatch(rows=rows, dead_letters=dead_letters)


def stream_dead_letter_record(
    ctx: RequestContext,
    dataset: DatasetRow,
    stream: StreamArchiveConfig,
    plan: UploadSyncPlan,
    dead_letter: StreamArchiveDeadLetter,
    failed_at: str,
) -> DeadLetterRecord:
    return DeadLetterRecord(
        dead_letter_record_id=_dead_letter_record_id(ctx, dead_letter),
        tenant_id=ctx.tenant_id,
        source_event_id=dead_letter.source_event_id,
        source_dataset_version_id=None,
        source_run_id=plan.run_id,
        payload=dict(dead_letter.event.payload),
        payload_hash=dead_letter.payload_hash,
        schema_version=1,
        transform_version=None,
        error_kind=dead_letter.error_kind,
        error_message=dead_letter.error_message,
        event_time=dead_letter.event_time,
        ingested_at=failed_at,
        first_failed_at=failed_at,
        attempts=1,
        status="QUARANTINED",
        replay_status="NOT_REQUESTED",
        replay_run_id=None,
        is_closed_partition_affected=False,
        metadata=_dead_letter_metadata(dataset, stream, dead_letter),
    )


def _dead_letter_metadata(
    dataset: DatasetRow,
    stream: StreamArchiveConfig,
    dead_letter: StreamArchiveDeadLetter,
) -> Mapping[str, object]:
    return {
        "dataset_id": dataset["id"],
        "dataset_ref": f"{dataset['namespace']}.{dataset['name']}",
        "schema_strategy": stream.schema_strategy,
        "stream": stream.stream_name,
        "topic": stream.topic,
        "consumer_group": stream.consumer_group,
        "partition": stream.partition,
        "offset": dead_letter.event.offset,
        "event_type": dead_letter.event.event_type,
        "event_key": dead_letter.event.key,
        "request_id": dead_letter.event.request_id,
        "event_time_field": stream.event_time_field,
        "source_time_field": stream.source_time_field,
        "time_zone": stream.time_zone,
        "allowed_lateness_seconds": stream.allowed_lateness_seconds,
        "too_late_after_seconds": stream.too_late_after_seconds,
        "clock_skew_seconds": stream.clock_skew_seconds,
    }


def stream_archive_limit(limit: int) -> int:
    if limit < 1:
        raise ValidationFailed("stream archive limit must be positive", details={"limit": limit})
    if limit > STREAM_ARCHIVE_MAX_LIMIT:
        raise ValidationFailed(
            "stream archive limit exceeds maximum",
            details={"limit": limit, "max_limit": STREAM_ARCHIVE_MAX_LIMIT},
        )
    return limit


def ensure_stream_archive_error_policy(
    batch: StreamArchivePreparedBatch,
    stream: StreamArchiveConfig,
    total_events: int,
) -> None:
    fatal = [row.error_kind for row in batch.dead_letters if row.error_kind in stream.fail_closed_error_kinds]
    if fatal:
        raise ValidationFailed(
            "stream archive record error kind is fail-closed",
            details={"error_kinds": sorted(set(fatal)), "dead_letter_records": len(batch.dead_letters)},
        )
    if _record_error_ratio(batch.dead_letters, total_events) > stream.max_record_error_ratio:
        raise ValidationFailed(
            "stream archive record error ratio exceeds threshold",
            details={
                "dead_letter_records": len(batch.dead_letters),
                "total_records": total_events,
                "max_record_error_ratio": stream.max_record_error_ratio,
            },
        )


def ensure_stream_archive_batch_writable(
    batch: StreamArchivePreparedBatch,
    stream: StreamArchiveConfig,
    total_events: int,
) -> None:
    ensure_stream_archive_error_policy(batch, stream, total_events)
    if not batch.rows:
        raise ValidationFailed(
            "stream archive batch has no valid records",
            details={"dead_letter_records": len(batch.dead_letters)},
        )


def stream_event_row(
    event: StreamEvent,
    stream: StreamArchiveConfig,
    previous_metadata: Mapping[str, object] | None = None,
) -> Mapping[str, object]:
    processed_at = _now()
    event_time = payload_named_time(event.payload, stream.event_time_field, stream)
    source_time = payload_named_time(event.payload, stream.source_time_field, stream)
    late_status = late_data_status(event_time, processed_at, previous_metadata, stream)
    if late_status == "TOO_LATE":
        raise ValidationFailed(
            "stream archive event is too late for source policy",
            details={"field": stream.event_time_field, "late_data_status": late_status},
        )
    if late_status == "FUTURE_CLOCK_SKEW":
        raise ValidationFailed(
            "stream archive event time is too far in the future for source policy",
            details={"field": stream.event_time_field, "late_data_status": late_status},
        )
    row = {
        "event_id": stream_event_id(event, stream),
        "stream": event.stream_name,
        "topic": stream.topic,
        "partition": stream.partition,
        "offset": event.offset,
        "event_type": event.event_type,
        "event_key": event.key,
        "tenant_id": event.tenant_id,
        "request_id": event.request_id,
        "payload_json": json.dumps(dict(event.payload), sort_keys=True, separators=(",", ":"), default=str),
        "event_time": event_time,
        "source_time": source_time,
        "ingested_at": processed_at,
        "processed_at": processed_at,
        "late_data_status": late_status,
        "event_time_lag_seconds": event_time_lag_seconds(event_time, processed_at),
    }
    if stream.schema_strategy == "cdc_envelope_json":
        return {**row, **_cdc_envelope_fields(event.payload)}
    return row


def stream_archive_fields(stream: StreamArchiveConfig) -> list[str]:
    if stream.schema_strategy == "cdc_envelope_json":
        return list(CDC_STREAM_ARCHIVE_FIELDS)
    return list(STREAM_ARCHIVE_FIELDS)


def stream_archive_field_types(stream: StreamArchiveConfig) -> Mapping[str, ParquetFieldType]:
    """Return the stable logical schema even when a batch contains only nulls."""
    return {field: STREAM_ARCHIVE_FIELD_TYPES[field] for field in stream_archive_fields(stream)}


def stream_transaction_metadata(
    stream: StreamArchiveConfig,
    events: Sequence[StreamEvent],
    previous_metadata: Mapping[str, object] | None = None,
    rows: Sequence[Mapping[str, object]] | None = None,
) -> Mapping[str, object]:
    cursor = _stream_cursor_payload(stream, events)
    cursors = _previous_stream_cursors(previous_metadata)
    cursors[_stream_cursor_key(stream)] = cursor
    metadata: dict[str, object] = {"streamCursor": cursor, "streamCursors": cursors}
    watermark = next_late_data_watermark(previous_metadata, rows or (), stream)
    # Watermarks are kept per stream, carried forward like cursors, so a commit
    # from one partition cannot overwrite (and regress) another partition's
    # watermark stored in a single shared slot.
    watermarks = previous_stream_watermarks(previous_metadata)
    if watermark is not None:
        entry = late_data_watermark_metadata(stream, watermark, rows or ())
        watermarks[stream_watermark_key(stream)] = entry
        metadata["lateDataWatermark"] = entry
    if watermarks:
        metadata["lateDataWatermarks"] = watermarks
    return metadata


def stream_cursor_offset(metadata: Mapping[str, object], stream: StreamArchiveConfig) -> int | None:
    cursors = metadata.get("streamCursors")
    cursor = cursors.get(_stream_cursor_key(stream)) if isinstance(cursors, Mapping) else None
    if not isinstance(cursor, Mapping):
        cursor = metadata.get("streamCursor")
    if not isinstance(cursor, Mapping) or not stream_cursor_matches(cursor, stream):
        return None
    offset = cursor.get("offset")
    return offset if isinstance(offset, int) and not isinstance(offset, bool) else None


def stream_cursor_matches(cursor: Mapping[str, object], stream: StreamArchiveConfig) -> bool:
    return (
        cursor.get("streamName") == stream.stream_name
        and cursor.get("topic") == stream.topic
        and cursor.get("partition") == stream.partition
        and cursor.get("consumerGroup") == stream.consumer_group
        and _cursor_schema_strategy(cursor) == stream.schema_strategy
    )


def _stream_cursor_payload(stream: StreamArchiveConfig, events: Sequence[StreamEvent]) -> dict[str, object]:
    return {
        "streamName": stream.stream_name,
        "topic": stream.topic,
        "partition": stream.partition,
        "consumerGroup": stream.consumer_group,
        "offset": events[-1].offset,
        "eventCount": len(events),
        "schemaStrategy": stream.schema_strategy,
    }


def _previous_stream_cursors(metadata: Mapping[str, object] | None) -> dict[str, object]:
    if not isinstance(metadata, Mapping):
        return {}
    raw = metadata.get("streamCursors")
    cursors = dict(raw) if isinstance(raw, Mapping) else {}
    legacy = metadata.get("streamCursor")
    if isinstance(legacy, Mapping):
        key = _cursor_key_from_payload(legacy)
        if key is not None:
            cursors.setdefault(key, dict(legacy))
    return cursors


def _stream_cursor_key(stream: StreamArchiveConfig) -> str:
    return f"{stream.topic}:{stream.consumer_group}:{stream.partition}:{stream.schema_strategy}"


def _cursor_key_from_payload(cursor: Mapping[str, object]) -> str | None:
    topic = cursor.get("topic")
    group = cursor.get("consumerGroup")
    partition = cursor.get("partition")
    if not isinstance(topic, str) or not isinstance(group, str) or not isinstance(partition, int):
        return None
    return f"{topic}:{group}:{partition}:{_cursor_schema_strategy(cursor)}"


def stream_event_id(event: StreamEvent, stream: StreamArchiveConfig) -> str:
    return f"{stream.topic}:{stream.partition}:{event.offset}"


def _dead_letter_stream_event(
    event: StreamEvent,
    source_event_id: str,
    stream: StreamArchiveConfig,
    exc: ValidationFailed,
) -> StreamArchiveDeadLetter:
    return StreamArchiveDeadLetter(
        event=event,
        source_event_id=source_event_id,
        payload_hash=_json_hash(dict(event.payload)),
        error_kind=_dead_letter_error_kind(exc),
        error_message=exc.message,
        event_time=_payload_event_time(event.payload, stream),
    )


def _dead_letter_record_id(ctx: RequestContext, dead_letter: StreamArchiveDeadLetter) -> str:
    identity = f"{ctx.tenant_id}:{dead_letter.source_event_id}:{dead_letter.payload_hash}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"dlqr_{digest[:24]}"


def _payload_event_time(payload: Mapping[str, object], stream: StreamArchiveConfig) -> str | None:
    value = payload.get(stream.event_time_field)
    return value if isinstance(value, str) and value else None


def _cursor_schema_strategy(cursor: Mapping[str, object]) -> object:
    return cursor.get("schemaStrategy", "envelope_json")


def _record_error_ratio(dead_letters: Sequence[StreamArchiveDeadLetter], total_events: int) -> float:
    if total_events <= 0:
        return 0.0
    return len(dead_letters) / total_events


def _validate_error_threshold(value: float) -> None:
    if value < 0 or value > 1:
        raise ValidationFailed(
            "stream archive error threshold must be between 0 and 1",
            details={"max_record_error_ratio": value},
        )


def _dead_letter_error_kind(exc: ValidationFailed) -> str:
    if exc.details.get("late_data_status") == "TOO_LATE":
        return "TOO_LATE"
    if exc.details.get("late_data_status") == "FUTURE_CLOCK_SKEW":
        return "FUTURE_CLOCK_SKEW"
    field = exc.details.get("field")
    if field == "tenant_id":
        return "TENANT_MISMATCH"
    if field == "pk":
        return "IDENTITY_ERROR"
    if field == "ordering":
        return "ORDERING_ERROR"
    return exc.code


def _validate_event_tenant(event: StreamEvent, expected_tenant_id: str | None) -> None:
    if expected_tenant_id is None or event.tenant_id == expected_tenant_id:
        return
    raise ValidationFailed(
        "stream event tenant does not match request tenant",
        details={
            "field": "tenant_id",
            "expected_tenant_id": expected_tenant_id,
            "event_tenant_id": event.tenant_id,
        },
    )


def _cdc_envelope_fields(payload: Mapping[str, object]) -> Mapping[str, object]:
    return {
        "op": _required_text(payload, "op"),
        "pk_json": _required_json_object(payload, "pk"),
        "before_json": _nullable_json_object(payload, "before"),
        "after_json": _nullable_json_object(payload, "after"),
        "ordering_json": _required_json_object(payload, "ordering"),
    }


def _required_text(payload: Mapping[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValidationFailed("cdc envelope field must be non-empty text", details={"field": name})
    return value


def _required_json_object(payload: Mapping[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, Mapping):
        raise ValidationFailed("cdc envelope field must be an object", details={"field": name})
    return _json_field(value)


def _nullable_json_object(payload: Mapping[str, object], name: str) -> str:
    value = payload.get(name)
    if value is None:
        return "null"
    if not isinstance(value, Mapping):
        raise ValidationFailed("cdc envelope field must be null or an object", details={"field": name})
    return _json_field(value)


def _json_field(value: Mapping[str, object]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), default=str)
