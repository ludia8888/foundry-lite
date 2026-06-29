from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import NoReturn, cast

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import OutboxEventRecord, StreamPublishRequest
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.adapters.scale_foundation import LocalStreamAdapter
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


class FailingStreamAdapter(LocalStreamAdapter):
    profile_name = "failing-stream"

    def publish_event(self, request: StreamPublishRequest) -> NoReturn:
        del request
        raise RuntimeError("stream down")


def test_operations_publish_pending_outbox_pushes_events_to_stream(tmp_path: Path) -> None:
    foundry, dependencies = _foundry(tmp_path)
    ctx = demo_admin_context()
    _insert_outbox(dependencies, event_id="outbox_success_1", request_id=ctx.request_id)

    result = foundry.operations.publish_pending_outbox(ctx=ctx, stream_name="ops-outbox", limit=10)
    events = dependencies.stream_adapter.read_events("ops-outbox")
    runs = foundry.operations.list_runs(ctx=ctx)

    assert result["requested"] == 1
    assert result["published"] == 1
    assert result["failed"] == 0
    assert [event.key for event in events] == ["outbox_success_1"]
    assert events[0].payload["aggregateId"] == "dsv_1"
    assert runs["outboxEvents"][0]["status"] == "published"
    assert runs["outboxEvents"][0]["published_at"] is not None


def test_operations_publish_pending_outbox_dead_letters_stream_failures(tmp_path: Path) -> None:
    stream_adapter = FailingStreamAdapter()
    foundry, dependencies = _foundry(tmp_path, stream_adapter=stream_adapter)
    ctx = demo_admin_context()
    _insert_outbox(dependencies, event_id="outbox_failed_1", request_id=ctx.request_id)

    result = foundry.operations.publish_pending_outbox(ctx=ctx, stream_name="ops-outbox", limit=10)
    runs = foundry.operations.list_runs(ctx=ctx)

    assert result["requested"] == 1
    assert result["published"] == 0
    assert result["failed"] == 1
    assert result["deadLetterEventIds"] == ["dlq_outbox_failed_1"]
    assert runs["outboxEvents"][0]["status"] == "failed"
    assert runs["deadLetterEvents"][0]["source_event_id"] == "outbox_failed_1"
    error = cast(dict[str, object], runs["deadLetterEvents"][0]["error"])
    trace = cast(dict[str, object], error["trace"])
    assert trace["adapter"] == "failing-stream"


def _foundry(
    tmp_path: Path, *, stream_adapter: LocalStreamAdapter | None = None
) -> tuple[FoundryLite, CoreDependencies]:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    if stream_adapter is not None:
        dependencies = replace(dependencies, stream_adapter=stream_adapter)
    return FoundryLite(dependencies=dependencies), dependencies


def _insert_outbox(dependencies: CoreDependencies, *, event_id: str, request_id: str) -> None:
    with dependencies.engine.begin() as transaction:
        dependencies.runtime_repository.insert_outbox_event(
            transaction=transaction,
            record=OutboxEventRecord(
                event_id=event_id,
                tenant_id="tenant-demo",
                event_type="dataset.version.committed",
                aggregate_type="dataset_version",
                aggregate_id="dsv_1",
                payload={"versionId": "dsv_1"},
                status="pending",
                attempts=0,
                idempotency_key=event_id,
                correlation_id=request_id,
                created_at="2026-06-10T00:00:00Z",
                published_at=None,
            ),
        )
