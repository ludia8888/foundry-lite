from __future__ import annotations

from dataclasses import dataclass

import pytest
from foundry_lite.application.ports import ConnectorNetworkRoute
from foundry_lite.application.ports.secret_provider import SecretValue
from foundry_lite.application.ports.source_stream_adapter import (
    SourceStreamAdapter,
    SourceStreamConnection,
    SourceStreamSubscription,
)
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters import KafkaSourceStreamAdapter, KafkaStreamAdapter


@dataclass(frozen=True)
class _TopicMetadata:
    partitions: dict[int, object]


@dataclass(frozen=True)
class _ClusterMetadata:
    brokers: dict[int, object]
    topics: dict[str, _TopicMetadata]


class _AdminClient:
    def __init__(self, config: dict[str, object]) -> None:
        self.config = config

    def list_topics(self, *, timeout: float) -> _ClusterMetadata:
        assert timeout == 10.0
        return _ClusterMetadata(
            brokers={1: object(), 2: object()},
            topics={
                "orders": _TopicMetadata({0: object(), 1: object()}),
                "__consumer_offsets": _TopicMetadata({0: object()}),
            },
        )


class _LagConsumer:
    def get_watermark_offsets(self, partition: object, *, timeout: float) -> tuple[int, int]:
        assert timeout == 10.0
        return 0, 12

    def close(self) -> None:
        return None


@pytest.fixture
def adapter() -> SourceStreamAdapter:
    return KafkaSourceStreamAdapter(admin_factory=lambda config: _AdminClient(dict(config)))


def test_source_stream_adapter_lists_topics_and_redacted_probe(adapter: SourceStreamAdapter) -> None:
    connection = SourceStreamConnection(bootstrap_servers="redpanda:9092")

    probe = adapter.test_connection(connection)
    topics = adapter.list_topics(connection, limit=10)

    assert probe.adapter_profile == "kafka-source-stream"
    assert probe.broker_count == 2
    assert probe.topic_count == 2
    assert [(topic.topic_name, topic.partition_count) for topic in topics] == [
        ("__consumer_offsets", 1),
        ("orders", 2),
    ]
    assert topics[0].is_internal is True


def test_source_stream_adapter_opens_source_scoped_reader_with_sasl_secret() -> None:
    secret = SecretValue(name="kafka-login", version="v1", value="alice:correct-horse")
    adapter = KafkaSourceStreamAdapter(admin_factory=lambda config: _AdminClient(dict(config)))

    reader = adapter.open_stream(
        SourceStreamConnection(
            bootstrap_servers="redpanda:9092",
            auth_scheme="sasl_plain",
            is_tls_enabled=True,
            credential=secret,
        ),
        SourceStreamSubscription(
            stream_name="orders-stream",
            topic="orders",
            consumer_group="orders-archive",
        ),
    )

    assert isinstance(reader, KafkaStreamAdapter)
    assert reader.config.security_protocol == "SASL_SSL"
    assert reader.config.sasl_mechanism == "PLAIN"
    assert reader.config.sasl_username == "alice"
    assert reader.config.sasl_password == "correct-horse"
    assert "correct-horse" not in repr(reader.config)


def test_source_stream_adapter_rejects_agent_route_until_transport_exists(
    adapter: SourceStreamAdapter,
) -> None:
    connection = SourceStreamConnection(
        bootstrap_servers="redpanda:9092",
        network_route=ConnectorNetworkRoute(mode="agent_proxy", agent_id="agent-1"),
    )

    with pytest.raises(ValidationFailed, match="Agent routing"):
        adapter.list_topics(connection, limit=10)


def test_source_stream_adapter_failure_contract_names_operations(adapter: SourceStreamAdapter) -> None:
    assert {mode.operation for mode in adapter.failure_contract().modes} == {
        "test_connection",
        "list_topics",
        "open_stream",
        "read_lag",
    }


def test_source_stream_adapter_reads_high_watermark_lag() -> None:
    adapter = KafkaSourceStreamAdapter(
        admin_factory=lambda config: _AdminClient(dict(config)),
        consumer_factory=lambda config: _LagConsumer(),
        topic_partition_factory=lambda topic, partition, offset: (topic, partition, offset),
    )

    lag = adapter.read_lag(
        SourceStreamConnection(bootstrap_servers="redpanda:9092"),
        SourceStreamSubscription("orders-stream", "orders", "orders-archive", partition=0),
        current_offset=8,
    )

    assert lag.earliest_offset == 0
    assert lag.end_offset == 12
    assert lag.current_offset == 8
    assert lag.lag == 3
