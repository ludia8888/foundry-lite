from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import StreamPublishRequest
from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailure
from foundry_lite.application.primitives import CommitResult
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.adapters import (
    KafkaStreamAdapter,
    KafkaStreamAdapterConfig,
    KafkaStreamSubscription,
    LocalStreamAdapter,
)
from foundry_lite.infrastructure.adapters import kafka_stream as kafka_module
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite_worker import stream_archive as worker_module
from foundry_lite_worker.stream_archive import (
    StreamArchiveWorkerConfig,
    run_stream_archive_continuously,
    run_stream_archive_once,
)


def test_kafka_stream_adapter_reads_assigned_offsets() -> None:
    consumer = _FakeConsumer(
        [
            _FakeMessage(
                offset=2,
                key=b"S-102",
                value=b'{"shipment_id":"S-102","status":"IN_TRANSIT"}',
                headers=[
                    ("foundry-event-type", b"shipment.updated"),
                    ("foundry-request-id", b"req-kafka-102"),
                ],
            )
        ]
    )
    adapter = _adapter(consumer)

    events = adapter.read_events("shipments", after_offset=1, limit=1)

    assert consumer.assigned == [("shipment_events", 0, 2)]
    assert events[0].offset == 2
    assert events[0].event_type == "shipment.updated"
    assert events[0].tenant_id == "tenant-demo"
    assert events[0].request_id == "req-kafka-102"
    assert events[0].key == "S-102"
    assert events[0].payload["shipment_id"] == "S-102"


def test_kafka_stream_adapter_reports_missing_subscription() -> None:
    adapter = _adapter(_FakeConsumer([]))

    with pytest.raises(AdapterError) as error:
        adapter.read_events("missing")

    assert error.value.failure.kind == "validation"
    assert error.value.failure.adapter_profile == "kafka-stream"


def test_kafka_stream_adapter_returns_empty_for_non_positive_limit() -> None:
    adapter = _adapter(_FakeConsumer([]))

    assert adapter.read_events("shipments", limit=0) == []


def test_kafka_stream_adapter_publish_event_uses_configured_topic() -> None:
    producer = _FakeProducer(offset=7)
    adapter = KafkaStreamAdapter(
        _adapter_config(),
        producer=producer,
    )

    event = adapter.publish_event(
        StreamPublishRequest(
            stream_name="shipments",
            event_type="shipment.updated",
            tenant_id="tenant-demo",
            request_id="req-produce",
            key="S-107",
            payload={"shipment_id": "S-107"},
        )
    )

    assert event.offset == 7
    assert producer.produced[0]["topic"] == "shipment_events"
    assert producer.produced[0]["key"] == b"S-107"
    assert producer.produced[0]["value"] == b'{"shipment_id":"S-107"}'
    assert ("foundry-request-id", b"req-produce") in producer.produced[0]["headers"]


def test_kafka_stream_adapter_publish_reports_flush_timeout() -> None:
    adapter = KafkaStreamAdapter(_adapter_config(), producer=_FakeProducer(offset=None, pending_flushes=1))

    with pytest.raises(AdapterError) as error:
        adapter.publish_event(
            StreamPublishRequest(
                stream_name="shipments",
                event_type="shipment.updated",
                tenant_id="tenant-demo",
                request_id="req-produce",
                key="S-108",
                payload={"shipment_id": "S-108"},
            )
        )

    assert error.value.failure.kind == "timeout"


def test_kafka_stream_adapter_publish_supports_unknown_offset() -> None:
    adapter = KafkaStreamAdapter(_adapter_config(), producer=_FakeProducer(offset=None))

    event = adapter.publish_event(
        StreamPublishRequest(
            stream_name="shipments",
            event_type="shipment.updated",
            tenant_id="tenant-demo",
            request_id="req-produce",
            key="S-109",
            payload={"shipment_id": "S-109"},
        )
    )

    assert event.offset == -1


def test_kafka_stream_adapter_consumer_factory_closes_internal_consumer() -> None:
    consumer = _FakeConsumer(
        [
            _FakeMessage(
                offset=1,
                key=None,
                value={"shipment_id": "S-101", "tenant_id": "tenant-from-payload"},
                headers=[],
            )
        ]
    )
    adapter = KafkaStreamAdapter(
        _adapter_config(),
        consumer_factory=lambda _config: consumer,
        topic_partition_factory=_topic_partition,
    )

    events = adapter.read_events("shipments")

    assert consumer.closed is True
    assert events[0].tenant_id == "tenant-from-payload"
    assert events[0].request_id == "kafka:shipment_events:0:1"
    assert events[0].key == "offset:1"


def test_kafka_stream_adapter_skips_stale_consumer_message() -> None:
    consumer = _FakeConsumer([_FakeMessage(offset=1, key="S-101", value=None, headers=[])])
    adapter = _adapter(consumer)

    assert adapter.read_events("shipments", after_offset=1, limit=1) == []


def test_kafka_stream_adapter_reports_consumer_errors() -> None:
    consumer = _FakeConsumer([_FakeMessage(offset=1, key="S-101", value="", headers=[], error="broker down")])
    adapter = _adapter(consumer)

    with pytest.raises(AdapterError) as error:
        adapter.read_events("shipments")

    assert error.value.failure.kind == "unavailable"


def test_kafka_stream_adapter_reports_missing_confluent_package(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_package(_name: str) -> object:
        raise ModuleNotFoundError("confluent_kafka")

    monkeypatch.setattr(kafka_module.importlib, "import_module", missing_package)
    adapter = KafkaStreamAdapter(_adapter_config())

    with pytest.raises(AdapterError) as error:
        adapter.read_events("shipments")

    assert error.value.failure.details["package"] == "confluent-kafka"


def test_kafka_stream_adapter_payload_fallbacks() -> None:
    adapter = _adapter(
        _FakeConsumer(
            [
                _FakeMessage(offset=1, key="invalid", value="not-json", headers=[]),
                _FakeMessage(offset=2, key="list", value="[1,2]", headers=[]),
            ]
        )
    )

    events = adapter.read_events("shipments", limit=2)

    assert events[0].payload == {"value": "not-json"}
    assert events[1].payload == {"value": [1, 2]}


def test_kafka_stream_worker_archives_broker_event(tmp_path: Path) -> None:
    storage_root = tmp_path / "flite"
    config = StreamArchiveWorkerConfig(
        dataset_ref="raw.shipment_events",
        stream_name="shipments",
        topic="shipment_events",
        bootstrap_servers="redpanda:9092",
        storage_root=storage_root,
        limit=10,
    )
    adapter = _adapter(
        _FakeConsumer(
            [
                _FakeMessage(
                    offset=0,
                    key="S-100",
                    value='{"shipment_id":"S-100","status":"IN_TRANSIT"}',
                    headers=[("foundry-event-type", "shipment.updated")],
                )
            ]
        )
    )

    result = run_stream_archive_once(config, stream_adapter=adapter)

    assert result is not None
    dependencies = create_local_core_dependencies(storage_root=storage_root)
    foundry = FoundryLite(dependencies=dependencies)
    preview = foundry.datasets.preview("raw.shipment_events", ctx=demo_admin_context())
    assert result.row_count == 1
    assert preview[0]["event_id"] == "shipment_events:0:0"
    assert preview[0]["payload_json"] == '{"shipment_id":"S-100","status":"IN_TRANSIT"}'


def test_stream_archive_worker_continuous_loop_archives_until_empty_poll(tmp_path: Path) -> None:
    storage_root = tmp_path / "continuous"
    adapter = LocalStreamAdapter()
    _publish_local_event(adapter, "shipments", "S-201")
    _publish_local_event(adapter, "shipments", "S-202")
    config = StreamArchiveWorkerConfig(
        dataset_ref="raw.shipment_events",
        stream_name="shipments",
        topic="shipment_events",
        bootstrap_servers="redpanda:9092",
        storage_root=storage_root,
        limit=1,
        is_continuous=True,
        continuous_max_empty_polls=1,
    )

    result = run_stream_archive_continuously(config, stream_adapter=adapter)

    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=storage_root))
    ctx = demo_admin_context()
    archived_rows = [
        row
        for version in foundry.datasets.list_versions("raw.shipment_events", ctx=ctx)
        for row in foundry.datasets.preview("raw.shipment_events", ctx=ctx, version=version["id"])
    ]
    assert result.stop_reason == "empty_polls"
    assert result.iterations == 3
    assert result.archived_batches == 2
    assert result.rows_archived == 2
    assert [row["event_id"] for row in archived_rows] == ["shipment_events:0:0", "shipment_events:0:1"]


def test_stream_archive_worker_continuous_loop_honors_stop_callback(tmp_path: Path) -> None:
    adapter = LocalStreamAdapter()
    _publish_local_event(adapter, "shipments", "S-301")
    _publish_local_event(adapter, "shipments", "S-302")
    checks = 0

    def stop_after_first_iteration() -> bool:
        nonlocal checks
        checks += 1
        return checks > 1

    result = run_stream_archive_continuously(
        StreamArchiveWorkerConfig(
            dataset_ref="raw.shipment_events",
            stream_name="shipments",
            topic="shipment_events",
            bootstrap_servers="redpanda:9092",
            storage_root=tmp_path / "stoppable",
            limit=10,
            is_continuous=True,
        ),
        stream_adapter=adapter,
        should_stop=stop_after_first_iteration,
    )

    assert result.stop_reason == "stop_requested"
    assert result.iterations == 1
    assert result.archived_batches == 1
    assert result.rows_archived == 2


def test_stream_archive_worker_continuous_loop_stops_after_max_batches(tmp_path: Path) -> None:
    adapter = LocalStreamAdapter()
    _publish_local_event(adapter, "shipments", "S-401")
    _publish_local_event(adapter, "shipments", "S-402")

    result = run_stream_archive_continuously(
        StreamArchiveWorkerConfig(
            dataset_ref="raw.shipment_events",
            stream_name="shipments",
            topic="shipment_events",
            bootstrap_servers="redpanda:9092",
            storage_root=tmp_path / "max-batches",
            limit=1,
            is_continuous=True,
            continuous_max_batches=1,
        ),
        stream_adapter=adapter,
    )

    assert result.stop_reason == "max_batches"
    assert result.iterations == 1
    assert result.archived_batches == 1
    assert result.rows_archived == 1


def test_stream_archive_worker_continuous_loop_counts_empty_polls_before_stop(tmp_path: Path) -> None:
    result = run_stream_archive_continuously(
        StreamArchiveWorkerConfig(
            dataset_ref="raw.empty_stream",
            stream_name="empty",
            topic="empty_topic",
            bootstrap_servers="redpanda:9092",
            storage_root=tmp_path / "empty-polls",
            limit=1,
            is_continuous=True,
            continuous_max_empty_polls=2,
        ),
        stream_adapter=LocalStreamAdapter(),
    )

    assert result.stop_reason == "empty_polls"
    assert result.iterations == 2
    assert result.empty_polls == 2
    assert result.archived_batches == 0


def test_stream_archive_worker_config_from_env(tmp_path: Path) -> None:
    config = worker_module.config_from_env(
        {
            "FOUNDRY_LITE_HOME": str(tmp_path),
            "FOUNDRY_LITE_STREAM_DATASET": "raw.custom_stream",
            "FOUNDRY_LITE_STREAM_NAME": "custom",
            "FOUNDRY_LITE_KAFKA_TOPIC": "custom_topic",
            "FOUNDRY_LITE_KAFKA_BOOTSTRAP_SERVERS": "redpanda:19092",
            "FOUNDRY_LITE_KAFKA_CONSUMER_GROUP": "custom-group",
            "FOUNDRY_LITE_KAFKA_PARTITION": "2",
            "FOUNDRY_LITE_STREAM_ARCHIVE_LIMIT": "9",
            "FOUNDRY_LITE_KAFKA_POLL_TIMEOUT_SECONDS": "2.5",
            "FOUNDRY_LITE_KAFKA_MAX_EMPTY_POLLS": "7",
            "FOUNDRY_LITE_STREAM_SCHEMA_STRATEGY": "cdc_envelope_json",
            "FOUNDRY_LITE_CDC_PRIMARY_KEY": "order_id, line_id",
            "FOUNDRY_LITE_TENANT_ID": "tenant-custom",
            "FOUNDRY_LITE_STREAM_SYNC_NAME": "custom-sync",
            "FOUNDRY_LITE_STREAM_CONTINUOUS": "true",
            "FOUNDRY_LITE_STREAM_CONTINUOUS_MAX_BATCHES": "3",
            "FOUNDRY_LITE_STREAM_CONTINUOUS_MAX_EMPTY_POLLS": "2",
        }
    )

    assert config.stream_config().partition == 2
    assert config.kafka_config().subscriptions[0].default_tenant_id == "tenant-custom"
    assert config.kafka_config().poll_timeout_seconds == 2.5
    assert config.kafka_config().max_empty_polls == 7
    assert config.stream_config().schema_strategy == "cdc_envelope_json"
    assert config.cdc_primary_key == ("order_id", "line_id")
    assert config.request_context().tenant_id == "tenant-custom"
    assert config.sync_name == "custom-sync"
    assert config.is_continuous is True
    assert config.continuous_max_batches == 3
    assert config.continuous_max_empty_polls == 2


def test_worker_requires_tenant_context_for_background_jobs(tmp_path: Path) -> None:
    config = worker_module.config_from_env(
        {
            "FOUNDRY_LITE_HOME": str(tmp_path),
            "FOUNDRY_LITE_TENANT_ID": " ",
        }
    )

    with pytest.raises(ValueError, match="requires tenant_id"):
        config.request_context()


def test_stream_archive_worker_builds_debezium_adapter_for_cdc(tmp_path: Path) -> None:
    config = StreamArchiveWorkerConfig(
        dataset_ref="raw_cdc.erp_orders",
        stream_name="erp_orders_cdc",
        topic="dbserver1.inventory.orders",
        bootstrap_servers="redpanda:9092",
        storage_root=tmp_path,
        schema_strategy="cdc_envelope_json",
        cdc_primary_key=("order_id",),
    )

    assert config.stream_adapter().profile_name == "debezium-postgres-stream"


def test_stream_archive_worker_rejects_unknown_schema_strategy() -> None:
    with pytest.raises(ValueError, match="unsupported stream schema strategy"):
        worker_module.config_from_env({"FOUNDRY_LITE_STREAM_SCHEMA_STRATEGY": "raw"})


def test_stream_archive_worker_main_prints_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        worker_module,
        "run_stream_archive_once",
        lambda _config: CommitResult(
            dataset_id="ds_1",
            dataset_ref="raw.stream_archive",
            transaction_id="tx_1",
            version_id="ver_1",
            version_number=1,
            row_count=2,
            manifest_uri="manifest.json",
            schema_hash="schema_hash",
        ),
    )

    exit_code = worker_module.main(["--storage-root", str(tmp_path), "--limit", "3"])

    assert exit_code == 0
    assert '"status": "ARCHIVED"' in capsys.readouterr().out


def test_stream_archive_worker_main_prints_adapter_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_run(_config: StreamArchiveWorkerConfig) -> None:
        raise AdapterError(
            AdapterFailure(
                adapter_profile="kafka-stream",
                operation="read_events",
                kind="unavailable",
                is_retryable=True,
                operator_message="Kafka broker is unavailable.",
            )
        )

    monkeypatch.setattr(worker_module, "run_stream_archive_once", fail_run)

    exit_code = worker_module.main(["--storage-root", str(tmp_path)])

    assert exit_code == 1
    assert '"adapterProfile": "kafka-stream"' in capsys.readouterr().out


def test_stream_archive_worker_main_prints_continuous_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        worker_module,
        "run_stream_archive_continuously",
        lambda _config: worker_module.ContinuousStreamArchiveResult(
            iterations=2,
            archived_batches=1,
            empty_polls=1,
            rows_archived=3,
            last_version_id="ver_continuous",
            stop_reason="empty_polls",
        ),
    )

    exit_code = worker_module.main(["--storage-root", str(tmp_path), "--continuous", "--max-batches", "1"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"status": "STOPPED"' in output
    assert '"stopReason": "empty_polls"' in output
    assert '"lastVersionId": "ver_continuous"' in output


def test_stream_archive_worker_result_json_for_no_events() -> None:
    assert worker_module._result_json(None) == '{"status": "NO_EVENTS"}'


def _publish_local_event(adapter: LocalStreamAdapter, stream_name: str, key: str) -> None:
    adapter.publish_event(
        StreamPublishRequest(
            stream_name=stream_name,
            event_type="shipment.updated",
            tenant_id="tenant-demo",
            request_id=f"req-{key}",
            key=key,
            payload={"shipment_id": key, "status": "IN_TRANSIT"},
        )
    )


def _adapter(consumer: _FakeConsumer) -> KafkaStreamAdapter:
    return KafkaStreamAdapter(
        _adapter_config(),
        consumer=consumer,
        topic_partition_factory=_topic_partition,
    )


def _adapter_config() -> KafkaStreamAdapterConfig:
    return KafkaStreamAdapterConfig(
        bootstrap_servers="redpanda:9092",
        subscriptions=(KafkaStreamSubscription("shipments", "shipment_events"),),
        poll_timeout_seconds=0,
    )


def _topic_partition(topic: str, partition: int, offset: int) -> tuple[str, int, int]:
    return (topic, partition, offset)


class _FakeConsumer:
    def __init__(self, messages: list[_FakeMessage]) -> None:
        self._messages = list(messages)
        self.assigned: list[tuple[str, int, int]] = []
        self.closed = False

    def assign(self, partitions: list[object]) -> None:
        self.assigned = [partition for partition in partitions if isinstance(partition, tuple)]

    def poll(self, _timeout: float) -> _FakeMessage | None:
        if not self._messages:
            return None
        return self._messages.pop(0)

    def close(self) -> None:
        self.closed = True


class _FakeMessage:
    def __init__(
        self,
        *,
        offset: int,
        key: bytes | str | None,
        value: bytes | str | None,
        headers: list[tuple[str, bytes | str | None]],
        error: object | None = None,
    ) -> None:
        self._offset = offset
        self._key = key
        self._value = value
        self._headers = headers
        self._error = error

    def error(self) -> object | None:
        return self._error

    def offset(self) -> int:
        return self._offset

    def key(self) -> bytes | str | None:
        return self._key

    def value(self) -> bytes | str | None:
        return self._value

    def headers(self) -> list[tuple[str, bytes | str | None]]:
        return self._headers


class _FakeProducer:
    def __init__(self, *, offset: int | None, pending_flushes: int = 0) -> None:
        self._offset = offset
        self._pending_flushes = pending_flushes
        self.produced: list[dict[str, object]] = []

    def produce(
        self,
        topic: str,
        *,
        key: bytes,
        value: bytes,
        headers: tuple[tuple[str, bytes], ...],
    ) -> int:
        self.produced.append({"topic": topic, "key": key, "value": value, "headers": headers})
        return self._offset

    def flush(self, _timeout: float | None = None) -> int:
        return self._pending_flushes
