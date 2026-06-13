from __future__ import annotations

import pytest
from foundry_lite.application.ports.stream_adapter import StreamAdapter, StreamPublishRequest
from foundry_lite.infrastructure.adapters import FakeStreamAdapter, LocalStreamAdapter


@pytest.fixture(params=[LocalStreamAdapter, FakeStreamAdapter])
def adapter(request: pytest.FixtureRequest) -> StreamAdapter:
    adapter_type = request.param
    return adapter_type()


def test_stream_adapter_contract_publishes_offsets_and_reads_in_order(adapter: StreamAdapter) -> None:
    first = adapter.publish_event(
        StreamPublishRequest(
            stream_name="object-events",
            event_type="object.updated",
            tenant_id="tenant-demo",
            request_id="req-stream-1",
            key="Order/O-1001",
            payload={"status": "APPROVED"},
        )
    )
    second = adapter.publish_event(
        StreamPublishRequest(
            stream_name="object-events",
            event_type="object.updated",
            tenant_id="tenant-demo",
            request_id="req-stream-2",
            key="Order/O-1002",
            payload={"status": "PENDING"},
        )
    )

    assert first.offset == 0
    assert second.offset == 1
    assert [event.key for event in adapter.read_events("object-events")] == ["Order/O-1001", "Order/O-1002"]
    assert adapter.read_events("object-events", after_offset=0, limit=1) == [second]


def test_stream_adapter_contract_isolated_streams(adapter: StreamAdapter) -> None:
    adapter.publish_event(
        StreamPublishRequest(
            stream_name="audit-events",
            event_type="audit.created",
            tenant_id="tenant-demo",
            request_id="req-stream-3",
            key="audit-1",
            payload={"action": "index_rebuild"},
        )
    )

    assert adapter.read_events("missing") == []
    assert adapter.read_events("audit-events")[0].offset == 0
