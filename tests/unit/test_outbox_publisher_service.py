from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import NoReturn, cast

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import (
    OUTBOX_PUBLISHED,
    OUTBOX_PUBLISHING,
    OUTBOX_PUBLISHING_RECLAIM,
    OutboxEventRecord,
    StreamPublishRequest,
)
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


def test_operations_publish_pending_outbox_reclaims_and_republishes_stuck_publishing_event(tmp_path: Path) -> None:
    foundry, dependencies = _foundry(tmp_path)
    ctx = demo_admin_context()
    _insert_outbox(dependencies, event_id="outbox_stuck_1", request_id=ctx.request_id)
    # Simulate a worker that crashed after claiming the event but before publishing it:
    # the row is stranded in publishing with a claim older than the lease window.
    with dependencies.engine.begin() as transaction:
        dependencies.runtime_repository.mark_outbox_event_publishing(
            transaction=transaction,
            tenant_id="tenant-demo",
            event_id="outbox_stuck_1",
            transition=OUTBOX_PUBLISHING,
            claimed_at="2020-01-01T00:00:00+00:00",
        )

    result = foundry.operations.publish_pending_outbox(ctx=ctx, stream_name="ops-outbox", limit=10)
    events = dependencies.stream_adapter.read_events("ops-outbox")
    runs = foundry.operations.list_runs(ctx=ctx)

    assert result["published"] == 1
    assert [event.key for event in events] == ["outbox_stuck_1"]
    assert runs["outboxEvents"][0]["status"] == "published"


def test_reclaimed_publishing_row_rejects_a_stale_workers_mark_published(tmp_path: Path) -> None:
    # BUG 1 (fencing): worker A claims an event, its publish hangs past the lease,
    # worker B reclaims and re-claims the row, then A's delayed mark_published must
    # NOT land on B's fresh claim — otherwise the event is marked published while
    # B's publish is still in flight (double publish / false terminal state).
    _, dependencies = _foundry(tmp_path)
    repo = dependencies.runtime_repository
    _insert_outbox(dependencies, event_id="outbox_fence_1", request_id="req-fence")

    with dependencies.engine.begin() as conn:
        claimed_a = repo.mark_outbox_event_publishing(
            transaction=conn,
            tenant_id="tenant-demo",
            event_id="outbox_fence_1",
            transition=OUTBOX_PUBLISHING,
            claimed_at="2020-01-01T00:00:00+00:00",
        )
    assert claimed_a is not None
    fence_a = str(claimed_a["claimed_at"])

    # Worker B reclaims the stale claim and takes a fresh one.
    with dependencies.engine.begin() as conn:
        reclaimed = repo.reclaim_stale_publishing_events(
            transaction=conn,
            tenant_id="tenant-demo",
            transition=OUTBOX_PUBLISHING_RECLAIM,
            claimed_before="2020-06-01T00:00:00+00:00",
        )
        assert reclaimed == 1
    with dependencies.engine.begin() as conn:
        claimed_b = repo.mark_outbox_event_publishing(
            transaction=conn,
            tenant_id="tenant-demo",
            event_id="outbox_fence_1",
            transition=OUTBOX_PUBLISHING,
            claimed_at="2020-12-31T00:00:00+00:00",
        )
    assert claimed_b is not None and claimed_b["claimed_at"] != fence_a

    # Worker A's delayed mark_published carries its OWN (now superseded) fence.
    with dependencies.engine.begin() as conn:
        published = repo.mark_outbox_event_published(
            transaction=conn,
            tenant_id="tenant-demo",
            event_id="outbox_fence_1",
            transition=OUTBOX_PUBLISHED,
            published_at="2021-01-01T00:00:00+00:00",
            claimed_at=fence_a,
        )

    assert published is None
    with dependencies.engine.begin() as conn:
        row = repo._outbox_event_row(transaction=conn, tenant_id="tenant-demo", event_id="outbox_fence_1")
    assert row is not None and row["status"] == "publishing"
    assert row["claimed_at"] == claimed_b["claimed_at"]


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
