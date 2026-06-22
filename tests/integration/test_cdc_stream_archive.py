from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import StreamArchiveConfig, StreamPublishRequest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.adapters import (
    DebeziumPostgresSourceConfig,
    DebeziumPostgresStreamAdapter,
    LocalStreamAdapter,
)
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite.observability import metrics


def test_cdc_stream_archive_appends_standard_changelog_preview(tmp_path: Path) -> None:
    inner = LocalStreamAdapter()
    adapter = DebeziumPostgresStreamAdapter(inner, DebeziumPostgresSourceConfig(primary_key=("order_id",)))
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    foundry = FoundryLite(dependencies=replace(dependencies, stream_adapter=adapter))
    ctx = demo_admin_context()
    stream = StreamArchiveConfig(
        stream_name="erp_orders_cdc",
        topic="dbserver1.inventory.orders",
        schema_strategy="cdc_envelope_json",
    )
    foundry.datasets.ensure("raw_cdc.erp_orders", ctx=ctx, primary_key=["event_id"])
    _publish_order(inner, "c", after={"order_id": "O-1001", "status": "PENDING"}, lsn=11)
    _publish_order(inner, "u", after={"order_id": "O-1001", "status": "APPROVED"}, lsn=12)
    _publish_order(inner, "d", before={"order_id": "O-1001", "status": "APPROVED"}, lsn=13)

    result = foundry.datasets.archive_stream_events("raw_cdc.erp_orders", stream=stream, ctx=ctx)

    preview = foundry.datasets.preview("raw_cdc.erp_orders", ctx=ctx)
    assert result is not None
    assert result.row_count == 3
    assert [row["op"] for row in preview] == ["c", "u", "d"]
    assert preview[0]["pk_json"] == '{"order_id":"O-1001"}'
    assert (
        preview[0]["ordering_json"]
        == '{"lsn":11,"source_ts_ms":1700000000011,"stream_name":"erp_orders_cdc","stream_offset":0,"table":"orders"}'
    )
    assert preview[2]["after_json"] == "null"


def test_cdc_stream_archive_updates_unread_lag_metric(tmp_path: Path) -> None:
    inner = LocalStreamAdapter()
    adapter = DebeziumPostgresStreamAdapter(inner, DebeziumPostgresSourceConfig(primary_key=("order_id",)))
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    foundry = FoundryLite(dependencies=replace(dependencies, stream_adapter=adapter))
    ctx = demo_admin_context()
    stream = StreamArchiveConfig(
        stream_name="erp_orders_cdc",
        topic="dbserver1.inventory.orders",
        limit=2,
        schema_strategy="cdc_envelope_json",
    )
    foundry.datasets.ensure("raw_cdc.erp_orders", ctx=ctx, primary_key=["event_id"])
    _publish_order(inner, "c", after={"order_id": "O-1001", "status": "PENDING"}, lsn=11)
    _publish_order(inner, "u", after={"order_id": "O-1001", "status": "APPROVED"}, lsn=12)
    _publish_order(inner, "d", before={"order_id": "O-1001", "status": "APPROVED"}, lsn=13)

    result = foundry.datasets.archive_stream_events("raw_cdc.erp_orders", stream=stream, ctx=ctx)

    payload, _media_type = metrics.prometheus_payload()
    assert result is not None
    assert result.row_count == 2
    assert b"foundry_lite_stream_archive_lag_events 1.0" in payload


def test_cdc_stream_archive_read_failure_is_visible_in_operations(tmp_path: Path) -> None:
    inner = LocalStreamAdapter()
    adapter = DebeziumPostgresStreamAdapter(inner, DebeziumPostgresSourceConfig(primary_key=("order_id",)))
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    foundry = FoundryLite(dependencies=replace(dependencies, stream_adapter=adapter))
    ctx = demo_admin_context()
    stream = StreamArchiveConfig(
        stream_name="erp_orders_cdc",
        topic="dbserver1.inventory.orders",
        schema_strategy="cdc_envelope_json",
    )
    foundry.datasets.ensure("raw_cdc.erp_orders", ctx=ctx, primary_key=["event_id"])
    inner.publish_event(
        StreamPublishRequest(
            stream_name="erp_orders_cdc",
            event_type="debezium.raw",
            tenant_id=ctx.tenant_id,
            request_id="req-cdc-invalid-live",
            key="O-1001",
            payload={"payload": {"op": "x", "before": None, "after": {}, "source": {}}},
        )
    )

    with pytest.raises(AdapterError):
        foundry.datasets.archive_stream_events("raw_cdc.erp_orders", stream=stream, ctx=ctx)

    failed_runs = foundry.operations.query_runs(ctx=ctx, run_type="sync", status="FAILED")["syncRuns"]
    failed_run = next(run for run in failed_runs if run["source_type"] == "stream.erp_orders_cdc")
    detail = foundry.operations.run_detail("sync", str(failed_run["id"]), ctx=ctx)
    assert detail["error"]["adapterFailure"]["adapterProfile"] == "debezium-postgres-stream"
    assert detail["error"]["adapterFailure"]["operation"] == "read_events"
    assert detail["error"]["trace"]["adapter"] == "stream_archive_reader"


def _publish_order(
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
            payload={
                "payload": {
                    "op": op,
                    "before": before,
                    "after": after,
                    "source": {"lsn": lsn, "ts_ms": 1700000000000 + lsn, "table": "orders"},
                }
            },
        )
    )
