from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


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


class StreamAdapter(Protocol):
    """Scale Foundation boundary for future Kafka/Redpanda-style streams."""

    profile_name: str

    def publish_event(self, request: StreamPublishRequest) -> StreamEvent:
        """Append one event and return its stream offset."""
        ...

    def read_events(self, stream_name: str, *, after_offset: int | None = None, limit: int = 100) -> list[StreamEvent]:
        """Read events from a stream in offset order."""
        ...
