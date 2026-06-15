from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import StreamAdapter, StreamArchiveConfig, StreamPublishRequest
from foundry_lite.application.services.dataset.stream_archive import stream_cursor_offset
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ValidationFailed
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
    assert failed_run["error"]["trace"]["adapter"] == "stream_archive_writer"


def _core_with_stream(tmp_path: Path) -> tuple[FoundryLite, CoreDependencies]:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    return FoundryLite(dependencies=dependencies), dependencies


def _publish_shipment(stream_adapter: StreamAdapter, shipment_id: str, *, ctx: RequestContext) -> None:
    stream_adapter.publish_event(
        StreamPublishRequest(
            stream_name="shipments",
            event_type="shipment.updated",
            tenant_id=ctx.tenant_id,
            request_id=ctx.request_id,
            key=shipment_id,
            payload={"shipment_id": shipment_id, "status": "IN_TRANSIT"},
        )
    )


def _latest_stream_transaction(
    foundry: FoundryLite,
    dependencies: CoreDependencies,
    ctx: RequestContext,
    dataset_ref: str,
) -> dict[str, object]:
    dataset = foundry.datasets.get(dataset_ref, ctx=ctx)
    with dependencies.engine.begin() as transaction:
        row = dependencies.dataset_transaction_repository.latest_committed_transaction(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            dataset_id=dataset["id"],
        )
    assert row is not None
    return dict(row)


class _ExplodingRowsComputeAdapter(DuckDBComputeAdapter):
    def rows_to_parquet(self, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("stream archive parquet write failed")
