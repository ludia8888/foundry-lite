from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import StreamPublishRequest
from foundry_lite.application.ports.stream_adapter import StreamEvent
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.adapters import KafkaStreamAdapter, KafkaStreamAdapterConfig, KafkaStreamSubscription
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite_worker.stream_archive import StreamArchiveWorkerConfig, run_stream_archive_once
from testcontainers.kafka import KafkaContainer
from testcontainers.postgres import PostgresContainer


def test_kafka_live_broker_event_archives_through_worker(tmp_path: Path) -> None:
    topic = f"foundry-lite-live-{uuid4().hex}"
    consumer_group = f"foundry-lite-archive-{uuid4().hex}"
    container = KafkaContainer().with_kraft()
    container.start(timeout=90)
    try:
        bootstrap_servers = container.get_bootstrap_server()
        _publish_live_event(bootstrap_servers, topic)

        storage_root = tmp_path / "flite"
        result = run_stream_archive_once(
            StreamArchiveWorkerConfig(
                dataset_ref="raw.shipment_events",
                stream_name="shipments",
                topic=topic,
                bootstrap_servers=bootstrap_servers,
                storage_root=storage_root,
                consumer_group=consumer_group,
                poll_timeout_seconds=1.0,
                max_empty_polls=10,
            )
        )

        preview = _preview_dataset(storage_root, "raw.shipment_events")
        assert result is not None
        assert result.row_count == 1
        assert preview[0]["event_id"] == f"{topic}:0:0"
        assert preview[0]["event_type"] == "shipment.updated"
        assert preview[0]["payload_json"] == '{"shipment_id":"S-LIVE-100","status":"IN_TRANSIT"}'
    finally:
        container.stop()


def test_kafka_live_broker_parallel_postgres_workers_do_not_duplicate_committed_offset(tmp_path: Path) -> None:
    topic = f"foundry-lite-live-race-{uuid4().hex}"
    consumer_group = f"foundry-lite-archive-race-{uuid4().hex}"
    kafka = KafkaContainer().with_kraft()
    postgres = PostgresContainer("postgres:16-alpine", driver="psycopg")
    kafka.start(timeout=90)
    postgres.start()
    try:
        bootstrap_servers = kafka.get_bootstrap_server()
        db_url = _postgres_url(postgres)
        _publish_live_event(bootstrap_servers, topic)
        storage_root = tmp_path / "flite-postgres-race"
        foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=storage_root, db_url=db_url))
        foundry.datasets.ensure("raw.shipment_events", ctx=demo_admin_context(), primary_key=["event_id"])

        config = StreamArchiveWorkerConfig(
            dataset_ref="raw.shipment_events",
            stream_name="shipments",
            topic=topic,
            bootstrap_servers=bootstrap_servers,
            storage_root=storage_root,
            db_url=db_url,
            consumer_group=consumer_group,
            poll_timeout_seconds=1.0,
            max_empty_polls=10,
        )
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda worker_id: _archive_with_read_barrier(worker_id, config, barrier),
                    ("worker-1", "worker-2"),
                )
            )

        foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=storage_root, db_url=db_url))
        versions = foundry.datasets.list_versions("raw.shipment_events", ctx=demo_admin_context())
        archived = [result for result in results if result["status"] == "ARCHIVED"]
        errors = [result for result in results if result["status"] == "ERROR"]
        assert len(archived) == 1
        assert len(errors) == 1
        assert errors[0]["errorType"] == "ConflictDetected"
        assert len(versions) == 1
        assert _preview_dataset(storage_root, "raw.shipment_events", db_url=db_url)[0]["event_id"] == f"{topic}:0:0"
    finally:
        kafka.stop()
        postgres.stop()


def _publish_live_event(bootstrap_servers: str, topic: str) -> None:
    adapter = KafkaStreamAdapter(
        KafkaStreamAdapterConfig(
            bootstrap_servers=bootstrap_servers,
            consumer_group=f"foundry-lite-producer-{uuid4().hex}",
            subscriptions=(KafkaStreamSubscription("shipments", topic),),
            producer_flush_timeout_seconds=10.0,
        )
    )
    adapter.publish_event(
        StreamPublishRequest(
            stream_name="shipments",
            event_type="shipment.updated",
            tenant_id="tenant-demo",
            request_id="req-kafka-live",
            key="S-LIVE-100",
            payload={"shipment_id": "S-LIVE-100", "status": "IN_TRANSIT"},
        )
    )


def _archive_with_read_barrier(
    worker_id: str,
    config: StreamArchiveWorkerConfig,
    barrier: Barrier,
) -> dict[str, object]:
    try:
        result = run_stream_archive_once(config, stream_adapter=_ReadBarrierKafkaAdapter(config, barrier))
        if result is None:
            return {"worker": worker_id, "status": "NO_EVENTS"}
        return {"worker": worker_id, "status": "ARCHIVED", "versionId": result.version_id}
    except Exception as exc:  # noqa: BLE001 - integration test captures the losing worker state
        return {"worker": worker_id, "status": "ERROR", "errorType": type(exc).__name__, "error": str(exc)}


class _ReadBarrierKafkaAdapter(KafkaStreamAdapter):
    def __init__(self, config: StreamArchiveWorkerConfig, barrier: Barrier) -> None:
        self._barrier = barrier
        super().__init__(config.kafka_config())

    def read_events(self, stream_name: str, *, after_offset: int | None = None, limit: int = 100) -> list[StreamEvent]:
        events = super().read_events(stream_name, after_offset=after_offset, limit=limit)
        self._barrier.wait(timeout=20)
        return events


def _preview_dataset(storage_root: Path, dataset_ref: str, *, db_url: str | None = None) -> list[dict[str, object]]:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=storage_root, db_url=db_url))
    return [dict(row) for row in foundry.datasets.preview(dataset_ref, ctx=demo_admin_context())]


def _postgres_url(container: PostgresContainer) -> str:
    url = container.get_connection_url()
    if url.startswith("postgresql://") and "+psycopg" not in url:
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url
