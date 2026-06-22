from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract

StreamSchemaStrategy = Literal["envelope_json", "cdc_envelope_json"]
LateDataStatus = Literal["ON_TIME", "LATE_ACCEPTED", "LATE_REQUIRES_REPROCESS", "TOO_LATE", "FUTURE_CLOCK_SKEW"]


@dataclass(frozen=True)
class StreamPublishRequest:
    """Vendor-neutral event publish request for a durable stream."""

    stream_name: str
    event_type: str
    tenant_id: str
    request_id: str
    key: str
    payload: Mapping[str, object]


@dataclass(frozen=True)
class StreamEvent:
    """Event record returned by a StreamAdapter."""

    stream_name: str
    offset: int
    event_type: str
    tenant_id: str
    request_id: str
    key: str
    payload: Mapping[str, object]


@dataclass(frozen=True)
class StreamArchiveConfig:
    """Stream subscription settings for raw archive dataset commits."""

    stream_name: str
    topic: str
    consumer_group: str = "foundry-lite-archive"
    partition: int = 0
    limit: int = 100
    schema_strategy: StreamSchemaStrategy = "envelope_json"
    max_record_error_ratio: float = 1.0
    fail_closed_error_kinds: tuple[str, ...] = ("IDENTITY_ERROR", "ORDERING_ERROR")
    event_time_field: str = "event_time"
    source_time_field: str = "source_time"
    time_zone: str | None = None
    allowed_lateness_seconds: int = 172800
    too_late_after_seconds: int = 2592000
    clock_skew_seconds: int = 300


class StreamAdapter(Protocol):
    """Scale Foundation boundary for future Kafka/Redpanda-style streams."""

    @property
    def profile_name(self) -> str: ...

    def failure_contract(self) -> AdapterFailureContract:
        """Return the adapter failure taxonomy promised by this profile."""
        ...

    def publish_event(self, request: StreamPublishRequest) -> StreamEvent:
        """Append one event and return its stream offset."""
        ...

    def read_events(self, stream_name: str, *, after_offset: int | None = None, limit: int = 100) -> list[StreamEvent]:
        """Read events from a stream in offset order."""
        ...
