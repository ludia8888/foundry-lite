"""Kafka-backed adapter for Source exploration and stream readers."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import ModuleType
from typing import Protocol, cast

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureMode,
)
from foundry_lite.application.ports.source_stream_adapter import (
    SourceStreamAdapter,
    SourceStreamConnection,
    SourceStreamConnectionProbe,
    SourceStreamLag,
    SourceStreamSubscription,
    SourceStreamTopic,
)
from foundry_lite.application.ports.stream_adapter import StreamAdapter
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters.kafka_stream import (
    ConsumerFactory,
    KafkaStreamAdapter,
    KafkaStreamAdapterConfig,
    KafkaStreamSubscription,
    TopicPartitionFactory,
)


class KafkaTopicMetadataLike(Protocol):
    partitions: Mapping[int, object]


class KafkaClusterMetadataLike(Protocol):
    brokers: Mapping[int, object]
    topics: Mapping[str, KafkaTopicMetadataLike]


class KafkaAdminClientLike(Protocol):
    def list_topics(self, *, timeout: float) -> KafkaClusterMetadataLike: ...


AdminFactory = Callable[[Mapping[str, object]], KafkaAdminClientLike]


@dataclass(frozen=True)
class _KafkaSecurityConfig:
    protocol: str | None = None
    mechanism: str | None = None
    username: str | None = None
    password: str | None = None


class KafkaLagConsumerLike(Protocol):
    def get_watermark_offsets(self, partition: object, *, timeout: float) -> tuple[int, int]: ...

    def close(self) -> None: ...


class KafkaSourceStreamAdapter:
    """Create Source-scoped Kafka readers without leaking connection secrets."""

    profile_name = "kafka-source-stream"

    def __init__(
        self,
        *,
        admin_factory: AdminFactory | None = None,
        consumer_factory: ConsumerFactory | None = None,
        topic_partition_factory: TopicPartitionFactory | None = None,
    ) -> None:
        self._admin_factory = admin_factory
        self._consumer_factory = consumer_factory
        self._topic_partition_factory = topic_partition_factory

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode("test_connection", "unavailable", True, "Kafka broker is unavailable."),
                AdapterFailureMode("list_topics", "unavailable", True, "Kafka topics could not be listed."),
                AdapterFailureMode("open_stream", "validation", False, "Kafka Source configuration is invalid."),
                AdapterFailureMode("read_lag", "unavailable", True, "Kafka lag could not be measured."),
            ),
        )

    def test_connection(self, connection: SourceStreamConnection) -> SourceStreamConnectionProbe:
        metadata = self._metadata(connection, operation="test_connection")
        return SourceStreamConnectionProbe(
            adapter_profile=self.profile_name,
            broker_count=len(metadata.brokers),
            topic_count=len(metadata.topics),
        )

    def list_topics(self, connection: SourceStreamConnection, *, limit: int) -> Sequence[SourceStreamTopic]:
        if not 1 <= limit <= 1_000:
            raise ValidationFailed("stream topic limit must be between 1 and 1000", details={"limit": limit})
        metadata = self._metadata(connection, operation="list_topics")
        return tuple(self._topic(name, value) for name, value in sorted(metadata.topics.items())[:limit])

    def open_stream(
        self,
        connection: SourceStreamConnection,
        subscription: SourceStreamSubscription,
    ) -> StreamAdapter:
        self._require_direct_route(connection)
        security = self._security_config(connection)
        config = KafkaStreamAdapterConfig(
            bootstrap_servers=self._bootstrap_servers(connection),
            consumer_group=subscription.consumer_group,
            subscriptions=(
                KafkaStreamSubscription(
                    stream_name=subscription.stream_name,
                    topic=subscription.topic,
                    partition=subscription.partition,
                ),
            ),
            security_protocol=security.protocol,
            sasl_mechanism=security.mechanism,
            sasl_username=security.username,
            sasl_password=security.password,
        )
        return KafkaStreamAdapter(
            config,
            consumer_factory=self._consumer_factory,
            topic_partition_factory=self._topic_partition_factory,
        )

    def read_lag(
        self,
        connection: SourceStreamConnection,
        subscription: SourceStreamSubscription,
        *,
        current_offset: int | None,
    ) -> SourceStreamLag:
        self._require_direct_route(connection)
        consumer = self._lag_consumer(connection, subscription)
        try:
            earliest, end = consumer.get_watermark_offsets(
                self._topic_partition(subscription),
                timeout=10.0,
            )
        except Exception as exc:
            raise AdapterError(
                AdapterFailure(
                    adapter_profile=self.profile_name,
                    operation="read_lag",
                    kind="unavailable",
                    is_retryable=True,
                    operator_message="Kafka partition watermark could not be loaded.",
                    timeout_seconds=10,
                )
            ) from exc
        finally:
            consumer.close()
        next_offset = earliest if current_offset is None else current_offset + 1
        return SourceStreamLag(earliest, end, current_offset, max(0, end - next_offset))

    def _metadata(
        self,
        connection: SourceStreamConnection,
        *,
        operation: str,
    ) -> KafkaClusterMetadataLike:
        self._require_direct_route(connection)
        try:
            return self._admin_client(connection).list_topics(timeout=10.0)
        except (AdapterError, ValidationFailed):
            raise
        except Exception as exc:
            raise AdapterError(
                AdapterFailure(
                    adapter_profile=self.profile_name,
                    operation=operation,
                    kind="unavailable",
                    is_retryable=True,
                    operator_message="Kafka broker metadata could not be loaded.",
                    timeout_seconds=10,
                )
            ) from exc

    def _admin_client(self, connection: SourceStreamConnection) -> KafkaAdminClientLike:
        factory = self._admin_factory or self._confluent_admin_factory()
        return factory(
            {"bootstrap.servers": self._bootstrap_servers(connection), **self._admin_security_config(connection)}
        )

    def _lag_consumer(
        self,
        connection: SourceStreamConnection,
        subscription: SourceStreamSubscription,
    ) -> KafkaLagConsumerLike:
        config = {
            "bootstrap.servers": self._bootstrap_servers(connection),
            "group.id": subscription.consumer_group,
            "enable.auto.commit": False,
            **self._admin_security_config(connection),
        }
        if self._consumer_factory is not None:
            return cast(KafkaLagConsumerLike, self._consumer_factory(config))
        module = self._load_confluent()
        factory = cast(Callable[[Mapping[str, object]], KafkaLagConsumerLike], module.__dict__["Consumer"])
        return factory(config)

    def _topic_partition(self, subscription: SourceStreamSubscription) -> object:
        factory = self._topic_partition_factory
        if factory is None:
            module = self._load_confluent()
            factory = cast(TopicPartitionFactory, module.__dict__["TopicPartition"])
        return factory(subscription.topic, subscription.partition, 0)

    def _confluent_admin_factory(self) -> AdminFactory:
        self._load_confluent()
        admin_module = importlib.import_module("confluent_kafka.admin")
        return cast(AdminFactory, admin_module.__dict__["AdminClient"])

    def _load_confluent(self) -> ModuleType:
        try:
            return importlib.import_module("confluent_kafka")
        except ModuleNotFoundError as exc:
            raise ValidationFailed(
                "confluent_kafka is required for Kafka Source exploration",
                details={"package": "confluent-kafka"},
            ) from exc

    def _security_config(self, connection: SourceStreamConnection) -> _KafkaSecurityConfig:
        if connection.auth_scheme == "none":
            return _KafkaSecurityConfig(protocol="SSL" if connection.is_tls_enabled else None)
        username, password = self._credential_parts(connection)
        mechanism = _sasl_mechanism(connection.auth_scheme)
        return _KafkaSecurityConfig(
            protocol="SASL_SSL" if connection.is_tls_enabled else "SASL_PLAINTEXT",
            mechanism=mechanism,
            username=username,
            password=password,
        )

    def _admin_security_config(self, connection: SourceStreamConnection) -> dict[str, object]:
        values = self._security_config(connection)
        config = {
            "security.protocol": values.protocol,
            "sasl.mechanism": values.mechanism,
            "sasl.username": values.username,
            "sasl.password": values.password,
        }
        return {key: value for key, value in config.items() if value is not None}

    def _credential_parts(self, connection: SourceStreamConnection) -> tuple[str, str]:
        if connection.credential is None or ":" not in connection.credential.value:
            raise ValidationFailed("Kafka SASL credential must use username:password format")
        username, password = connection.credential.value.split(":", 1)
        if not username or not password:
            raise ValidationFailed("Kafka SASL credential must include username and password")
        return username, password

    def _bootstrap_servers(self, connection: SourceStreamConnection) -> str:
        value = connection.bootstrap_servers.strip()
        if value:
            return value
        raise ValidationFailed("Kafka Source requires bootstrap servers")

    def _require_direct_route(self, connection: SourceStreamConnection) -> None:
        if connection.network_route is not None and connection.network_route.mode != "direct":
            raise ValidationFailed("Kafka Source Agent routing is not available in this runtime profile")

    def _topic(self, name: str, metadata: KafkaTopicMetadataLike) -> SourceStreamTopic:
        return SourceStreamTopic(
            topic_name=name,
            partition_count=len(metadata.partitions),
            is_internal=name.startswith("__"),
        )


def _sasl_mechanism(auth_scheme: str) -> str:
    values = {
        "sasl_plain": "PLAIN",
        "sasl_scram_sha256": "SCRAM-SHA-256",
        "sasl_scram_sha512": "SCRAM-SHA-512",
    }
    mechanism = values.get(auth_scheme)
    if mechanism is None:
        raise ValidationFailed("Kafka authentication scheme is unsupported", details={"authScheme": auth_scheme})
    return mechanism


_: SourceStreamAdapter = KafkaSourceStreamAdapter()
