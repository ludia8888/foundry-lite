from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from foundry_lite.application.core import FoundryLiteCore
from foundry_lite.application.ports import StreamPublishRequest
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.adapters import KafkaStreamAdapter, KafkaStreamAdapterConfig, KafkaStreamSubscription
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite_worker.stream_archive import StreamArchiveWorkerConfig, run_stream_archive_once
from testcontainers.kafka import KafkaContainer


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


def _preview_dataset(storage_root: Path, dataset_ref: str) -> list[dict[str, object]]:
    core = FoundryLiteCore(dependencies=create_local_core_dependencies(storage_root=storage_root))
    return [dict(row) for row in core.preview_dataset(dataset_ref, ctx=demo_admin_context())]
