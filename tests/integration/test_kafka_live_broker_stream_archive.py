from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient, NewTopic
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


def test_kafka_live_broker_flows_through_data_connection_source(tmp_path: Path) -> None:
    topic = f"foundry-lite-source-{uuid4().hex}"
    consumer_group = f"foundry-lite-source-archive-{uuid4().hex}"
    container = KafkaContainer().with_kraft()
    container.start(timeout=90)
    try:
        bootstrap_servers = container.get_bootstrap_server()
        _publish_live_event(bootstrap_servers, topic)
        dependencies = create_local_core_dependencies(storage_root=tmp_path / "source-flite")
        foundry = FoundryLite(dependencies=dependencies)
        ctx = demo_admin_context()
        foundry.sources.create_managed_sync(
            sync_name="shipment_streaming_sync",
            source_name="shipment_kafka_source",
            display_name="Shipment Kafka Source",
            source_type="kafka",
            capability="streaming",
            mode="APPEND",
            target_dataset_ref="live.shipment_events",
            schedule={"mode": "manual"},
            config_summary={
                "bootstrapServers": bootstrap_servers,
                "connectionMode": "direct",
                "topic": topic,
                "partition": 0,
                "streamName": "shipments",
                "consumerGroup": consumer_group,
                "batchLimit": 100,
            },
            idempotency_key="shipment-source-sync",
            ctx=ctx,
        )
        source = foundry.sources.get_source("shipment_kafka_source", ctx=ctx)
        connection_test = foundry.sources.test_connection(
            "shipment_kafka_source",
            expected_config_fingerprint=source["configFingerprint"],
            idempotency_key="shipment-source-connection-test",
            ctx=ctx,
        )

        topics = foundry.sources.explore_source(
            source_name="shipment_kafka_source",
            source_type="kafka",
            request={"sampleLimit": 100},
            ctx=ctx,
        )
        preview = foundry.sources.explore_source(
            source_name="shipment_kafka_source",
            source_type="kafka",
            request={"topic": topic, "streamName": "shipments", "sampleLimit": 10},
            ctx=ctx,
        )
        run = foundry.sources.start_managed_sync_run(
            "shipment_streaming_sync",
            idempotency_key="shipment-source-run-1",
            ctx=ctx,
        )

        topic_rows = topics["resultSummary"]["topics"]
        preview_result = preview["resultSummary"]
        result = run["resultSummary"]
        assert connection_test["status"] == "succeeded"
        assert connection_test["checks"]["summary"] == {"passed": 5, "total": 5}
        assert topic in {row["topicName"] for row in topic_rows}
        assert preview_result["sampleRows"][0]["key"] == "S-LIVE-100"
        assert preview_result["datasetCommitCreated"] is False
        assert run["status"] == "succeeded"
        assert run["checkpointEnd"]["offset"] == 0
        assert result["brokerEndOffset"] == 1
        assert result["brokerLag"] == 0
        assert foundry.datasets.preview("live.shipment_events", ctx=ctx)[0]["event_id"] == f"{topic}:0:0"
    finally:
        container.stop()


def test_kafka_live_broker_all_partitions_keep_independent_checkpoints(tmp_path: Path) -> None:
    topic = f"foundry-lite-source-partitions-{uuid4().hex}"
    consumer_group = f"foundry-lite-source-partitions-{uuid4().hex}"
    container = KafkaContainer().with_kraft()
    container.start(timeout=90)
    try:
        bootstrap_servers = container.get_bootstrap_server()
        _create_topic(bootstrap_servers, topic, partition_count=2)
        _publish_partition_event(bootstrap_servers, topic, partition=0, shipment_id="S-P0")
        _publish_partition_event(bootstrap_servers, topic, partition=1, shipment_id="S-P1")
        dependencies = create_local_core_dependencies(storage_root=tmp_path / "source-partitions")
        foundry = FoundryLite(dependencies=dependencies)
        ctx = demo_admin_context()
        foundry.sources.create_managed_sync(
            sync_name="shipment_all_partitions",
            source_name="shipment_partitioned_kafka",
            display_name="Shipment all partitions",
            source_type="kafka",
            capability="streaming",
            mode="APPEND",
            target_dataset_ref="live.shipment_partition_events",
            schedule={"mode": "manual"},
            config_summary={
                "bootstrapServers": bootstrap_servers,
                "connectionMode": "direct",
                "topic": topic,
                "partitionMode": "all",
                "streamName": "shipments",
                "consumerGroup": consumer_group,
                "batchLimit": 100,
            },
            idempotency_key="shipment-all-partitions-sync",
            ctx=ctx,
        )

        first = foundry.sources.start_managed_sync_run(
            "shipment_all_partitions", idempotency_key="shipment-all-partitions-run-1", ctx=ctx
        )
        _publish_partition_event(bootstrap_servers, topic, partition=1, shipment_id="S-P1-2")
        second = foundry.sources.start_managed_sync_run(
            "shipment_all_partitions", idempotency_key="shipment-all-partitions-run-2", ctx=ctx
        )
        rows = foundry.datasets.preview("live.shipment_partition_events", ctx=ctx)

        assert first["resultSummary"]["partitionCount"] == 2
        assert first["resultSummary"]["eventCount"] == 2
        assert first["checkpointEnd"]["partitions"]["0"]["offset"] == 0
        assert first["checkpointEnd"]["partitions"]["1"]["offset"] == 0
        assert second["resultSummary"]["eventCount"] == 1
        assert second["checkpointEnd"]["partitions"]["0"]["offset"] == 0
        assert second["checkpointEnd"]["partitions"]["1"]["offset"] == 1
        assert {row["event_id"] for row in rows} == {
            f"{topic}:0:0",
            f"{topic}:1:0",
            f"{topic}:1:1",
        }
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


def _create_topic(bootstrap_servers: str, topic: str, *, partition_count: int) -> None:
    admin = AdminClient({"bootstrap.servers": bootstrap_servers})
    future = admin.create_topics([NewTopic(topic, num_partitions=partition_count, replication_factor=1)])[topic]
    future.result(timeout=20)


def _publish_partition_event(
    bootstrap_servers: str,
    topic: str,
    *,
    partition: int,
    shipment_id: str,
) -> None:
    producer = Producer({"bootstrap.servers": bootstrap_servers})
    producer.produce(
        topic,
        partition=partition,
        key=shipment_id,
        value=f'{{"shipment_id":"{shipment_id}","status":"IN_TRANSIT"}}',
        headers={
            "foundry-event-type": "shipment.updated",
            "foundry-tenant-id": "tenant-demo",
            "foundry-request-id": f"req-{shipment_id}",
        },
    )
    producer.flush(10)


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
