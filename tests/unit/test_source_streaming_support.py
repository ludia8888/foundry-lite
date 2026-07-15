from __future__ import annotations

from dataclasses import dataclass
from threading import Event

import pytest
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite_worker import source_streaming_support as support
from foundry_lite_worker.kraken_kafka_bridge import KrakenBridgeSnapshot


@dataclass(frozen=True)
class _RetryConfig:
    retry_base_seconds: float = 0.5
    retry_max_seconds: float = 3.0


def test_streaming_support_normalizes_telemetry_and_backoff() -> None:
    snapshot = KrakenBridgeSnapshot(
        connection_state="STREAMING",
        exchange_status="online",
        connection_id="connection-1",
        last_message_at="2026-07-16T00:00:01Z",
        last_trade_at="2026-07-16T00:00:01Z",
        published_records=4,
        reconnect_count=1,
        consecutive_failures=0,
        last_error=None,
        input_rate_per_second=2.5,
    )

    assert support.bridge_payload(snapshot)["publishedRecords"] == 4
    assert support.mapping({"key": "value"}) == {"key": "value"}
    assert support.mapping("bad") == {}
    assert support.integer(2.9) == 2
    assert support.integer(True) == 0
    assert support.optional_float(2) == 2.0
    assert support.optional_float(False) is None
    assert support.output_rate(4, 2_000) == 2.0
    assert support.output_rate(4, 0) is None
    assert support.output_rate(4, None) is None
    assert support.retry_delay(_RetryConfig(), 1) == 0.5
    assert support.retry_delay(_RetryConfig(), 20) == 3.0
    assert support.maximum_reached(3, 3) is True
    assert support.maximum_reached(3, None) is False


def test_streaming_support_builds_single_and_partition_kafka_telemetry() -> None:
    previous = {"topic": "previous"}
    assert support.kafka_telemetry({}, {"status": "failed"}, previous) == previous

    single = support.kafka_telemetry(
        {
            "topic": "trades",
            "partition": 0,
            "consumerGroup": "foundry",
            "brokerEndOffset": 12,
            "brokerLag": 2,
            "eventCount": 3,
        },
        {"status": "succeeded", "checkpointEnd": {"offset": 10}},
        {},
    )
    assert single["currentOffset"] == 10
    assert single["partitions"][0]["brokerLag"] == 2

    partitioned = support.kafka_telemetry(
        {
            "topic": "trades",
            "consumerGroup": "foundry",
            "partitions": [
                {"partition": 0, "checkpoint": {"offset": 10}, "brokerEndOffset": 12, "brokerLag": 2},
                "ignored",
            ],
            "brokerLag": 2,
        },
        {"status": "succeeded"},
        {},
    )
    assert partitioned["partitionCount"] == 1
    assert partitioned["partitions"][0]["currentOffset"] == 10


def test_streaming_support_errors_are_safe_and_required_values_fail_closed() -> None:
    domain_error = ValidationFailed("invalid stream", details={"field": "topic"})
    assert support.worker_error_payload(domain_error) == {
        "code": "VALIDATION_FAILED",
        "message": "invalid stream",
        "details": {"field": "topic"},
    }
    assert support.worker_error_payload(RuntimeError("secret detail")) == {
        "code": "WORKER_FAILURE",
        "message": "source streaming worker failed with RuntimeError",
    }
    assert support.required_config({"topic": "trades"}, "topic") == "trades"
    with pytest.raises(ValidationFailed, match="configuration is incomplete"):
        support.required_config({}, "topic")
    assert support.required("tenant", " tenant-live ") == "tenant-live"
    with pytest.raises(ValueError, match="requires tenant"):
        support.required("tenant", "  ")


def test_streaming_heartbeat_propagates_operation_and_heartbeat_failures() -> None:
    with pytest.raises(RuntimeError, match="operation failed"):
        support.run_with_heartbeat(
            operation=lambda: (_ for _ in ()).throw(RuntimeError("operation failed")),
            heartbeat=lambda: None,
            interval_seconds=1,
        )

    heartbeat_called = Event()
    release_operation = Event()

    def failing_heartbeat() -> None:
        heartbeat_called.set()
        raise ValidationFailed("lease lost")

    def operation() -> str:
        assert heartbeat_called.wait(timeout=1)
        release_operation.set()
        return "done"

    with pytest.raises(ValidationFailed, match="lease lost"):
        support.run_with_heartbeat(operation=operation, heartbeat=failing_heartbeat, interval_seconds=0)
    assert release_operation.is_set()
