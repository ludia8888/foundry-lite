from __future__ import annotations

import pytest
from foundry_lite.application.ports import StreamPublishRequest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.infrastructure.adapters import (
    DebeziumPostgresSourceConfig,
    DebeziumPostgresStreamAdapter,
    LocalStreamAdapter,
)


def test_debezium_adapter_normalizes_insert_update_delete_envelopes() -> None:
    inner = LocalStreamAdapter()
    adapter = _adapter(inner)
    _publish_debezium(inner, "c", after={"order_id": "O-1001", "status": "PENDING"}, lsn=11)
    _publish_debezium(
        inner,
        "u",
        before={"order_id": "O-1001", "status": "PENDING"},
        after={"order_id": "O-1001", "status": "APPROVED"},
        lsn=12,
    )
    _publish_debezium(inner, "d", before={"order_id": "O-1001", "status": "APPROVED"}, lsn=13)

    events = adapter.read_events("erp_orders_cdc", limit=3)

    assert [event.event_type for event in events] == ["cdc.c", "cdc.u", "cdc.d"]
    assert events[0].payload["pk"] == {"order_id": "O-1001"}
    first_ordering = events[0].payload["ordering"]
    assert first_ordering["lsn"] == 11
    assert first_ordering["source_ts_ms"] == 1700000000011
    assert first_ordering["table"] == "orders"
    assert first_ordering["stream_name"] == "erp_orders_cdc"
    assert isinstance(first_ordering["stream_offset"], int)
    assert events[2].payload["after"] is None


def test_debezium_adapter_normalizes_direct_payload_and_publish_path() -> None:
    inner = LocalStreamAdapter()
    adapter = _adapter(inner)

    event = adapter.publish_event(
        StreamPublishRequest(
            stream_name="erp_orders_cdc",
            event_type="debezium.raw",
            tenant_id="tenant-demo",
            request_id="req-cdc-direct",
            key="O-1002",
            payload={
                "op": "c",
                "before": None,
                "after": {"order_id": "O-1002", "status": "PENDING"},
                "source": {"lsn": 21, "ts_ms": 1700000000021, "table": "orders"},
            },
        )
    )

    assert event.event_type == "cdc.c"
    assert event.payload["pk"] == {"order_id": "O-1002"}


def test_debezium_adapter_reports_publish_validation_operation() -> None:
    inner = LocalStreamAdapter()
    adapter = _adapter(inner)

    with pytest.raises(AdapterError) as error:
        adapter.publish_event(
            StreamPublishRequest(
                stream_name="erp_orders_cdc",
                event_type="debezium.raw",
                tenant_id="tenant-demo",
                request_id="req-cdc-bad-publish",
                key="O-1002",
                payload={"payload": {"op": "x", "before": None, "after": {}, "source": {}}},
            )
        )

    assert error.value.failure.operation == "publish_event"
    assert error.value.failure.kind == "validation"


def test_debezium_adapter_rejects_missing_primary_key() -> None:
    inner = LocalStreamAdapter()
    adapter = _adapter(inner)
    _publish_debezium(inner, "c", after={"status": "PENDING"}, lsn=11)

    with pytest.raises(AdapterError) as error:
        adapter.read_events("erp_orders_cdc")

    assert error.value.failure.kind == "validation"
    assert error.value.failure.adapter_profile == "debezium-postgres-stream"


@pytest.mark.parametrize(
    ("payload", "primary_key"),
    [
        ({}, ("order_id",)),
        ({"payload": {"op": "x", "before": None, "after": {}, "source": {}}}, ("order_id",)),
        ({"payload": {"op": "c", "before": "bad", "after": {}, "source": {}}}, ("order_id",)),
        ({"payload": {"op": "c", "before": None, "after": {}, "source": "bad"}}, ("order_id",)),
        ({"payload": {"op": "c", "before": None, "after": {"order_id": "O-1001"}, "source": {}}}, ()),
        ({"payload": {"op": "d", "before": None, "after": None, "source": {}}}, ("order_id",)),
    ],
)
def test_debezium_adapter_rejects_invalid_envelopes(
    payload: dict[str, object],
    primary_key: tuple[str, ...],
) -> None:
    inner = LocalStreamAdapter()
    adapter = DebeziumPostgresStreamAdapter(inner, DebeziumPostgresSourceConfig(primary_key=primary_key))
    _publish_payload(inner, payload)

    with pytest.raises(AdapterError) as error:
        adapter.read_events("erp_orders_cdc")

    assert error.value.failure.kind == "validation"


def _adapter(inner: LocalStreamAdapter) -> DebeziumPostgresStreamAdapter:
    return DebeziumPostgresStreamAdapter(
        inner,
        DebeziumPostgresSourceConfig(primary_key=("order_id",)),
    )


def _publish_payload(inner: LocalStreamAdapter, payload: dict[str, object]) -> None:
    inner.publish_event(
        StreamPublishRequest(
            stream_name="erp_orders_cdc",
            event_type="debezium.raw",
            tenant_id="tenant-demo",
            request_id="req-cdc-invalid",
            key="O-1001",
            payload=payload,
        )
    )


def _publish_debezium(
    inner: LocalStreamAdapter,
    op: str,
    *,
    lsn: int,
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
) -> None:
    inner.publish_event(
        StreamPublishRequest(
            stream_name="erp_orders_cdc",
            event_type="debezium.raw",
            tenant_id="tenant-demo",
            request_id=f"req-cdc-{lsn}",
            key="O-1001",
            payload=_debezium_payload(op, before=before, after=after, lsn=lsn),
        )
    )


def _debezium_payload(
    op: str,
    *,
    lsn: int,
    before: dict[str, object] | None,
    after: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "payload": {
            "op": op,
            "before": before,
            "after": after,
            "source": {"lsn": lsn, "ts_ms": 1700000000000 + lsn, "table": "orders"},
        }
    }
