from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import StreamAdapter, StreamArchiveConfig, StreamEvent
from foundry_lite.application.primitives import _now
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
    "ingested_at",
]


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


def stream_archive_limit(limit: int) -> int:
    if limit < 1:
        raise ValidationFailed("stream archive limit must be positive", details={"limit": limit})
    if limit > STREAM_ARCHIVE_MAX_LIMIT:
        raise ValidationFailed(
            "stream archive limit exceeds maximum",
            details={"limit": limit, "max_limit": STREAM_ARCHIVE_MAX_LIMIT},
        )
    return limit


def stream_event_row(event: StreamEvent, stream: StreamArchiveConfig) -> Mapping[str, object]:
    return {
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
        "ingested_at": f"ts:{_now()}",
    }


def stream_transaction_metadata(stream: StreamArchiveConfig, events: Sequence[StreamEvent]) -> Mapping[str, object]:
    return {
        "streamCursor": {
            "streamName": stream.stream_name,
            "topic": stream.topic,
            "partition": stream.partition,
            "consumerGroup": stream.consumer_group,
            "offset": events[-1].offset,
            "eventCount": len(events),
            "schemaStrategy": stream.schema_strategy,
        }
    }


def stream_cursor_offset(metadata: Mapping[str, object], stream: StreamArchiveConfig) -> int | None:
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
    )


def stream_event_id(event: StreamEvent, stream: StreamArchiveConfig) -> str:
    return f"{stream.topic}:{stream.partition}:{event.offset}"
