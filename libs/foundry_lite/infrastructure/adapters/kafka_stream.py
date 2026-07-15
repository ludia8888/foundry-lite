"""Kafka/Redpanda stream adapter implementation for archive workers."""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import ModuleType
from typing import Protocol, cast

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureKind,
    AdapterFailureMode,
)
from foundry_lite.application.ports.stream_adapter import StreamEvent, StreamPublishRequest
from foundry_lite.domain.context import DEFAULT_TENANT_ID


class KafkaMessageLike(Protocol):
    """Minimal broker message shape used by the adapter."""

    def error(self) -> object | None:
        """Return broker error metadata for this message, if present."""
        ...

    def offset(self) -> int:
        """Return the durable broker offset."""
        ...

    def key(self) -> bytes | str | None:
        """Return the broker key as bytes or text."""
        ...

    def value(self) -> bytes | str | Mapping[str, object] | None:
        """Return the raw event payload."""
        ...

    def headers(self) -> Sequence[tuple[str, bytes | str | None]] | None:
        """Return broker headers for Foundry metadata."""
        ...


class KafkaConsumerLike(Protocol):
    """Minimal Kafka consumer behavior needed for archive reads."""

    def assign(self, _partitions: Sequence[object]) -> None:
        """Assign the consumer to an explicit topic partition and offset."""
        ...

    def poll(self, _timeout: float) -> KafkaMessageLike | None:
        """Poll for one broker message."""
        ...

    def close(self) -> None:
        """Close resources owned by the consumer."""
        ...


class KafkaProducerLike(Protocol):
    """Minimal Kafka producer behavior used by publish tests and demos."""

    def produce(
        self,
        topic: str,
        *,
        key: bytes,
        value: bytes,
        headers: Sequence[tuple[str, bytes]],
    ) -> int | None:
        """Publish one event and return the assigned offset when available."""
        ...

    def flush(self, _timeout: float | None = None) -> int:
        """Return the number of messages still pending after flush."""
        ...


TopicPartitionFactory = Callable[[str, int, int], object]
ConsumerFactory = Callable[[Mapping[str, object]], KafkaConsumerLike]
ProducerFactory = Callable[[Mapping[str, object]], KafkaProducerLike]


@dataclass(frozen=True)
class KafkaStreamSubscription:
    """Mapping from a Foundry stream name to a Kafka topic partition."""

    stream_name: str
    topic: str
    partition: int = 0
    default_tenant_id: str = DEFAULT_TENANT_ID


@dataclass(frozen=True)
class KafkaStreamAdapterConfig:
    """Runtime configuration for Kafka/Redpanda stream IO."""

    bootstrap_servers: str
    subscriptions: tuple[KafkaStreamSubscription, ...]
    consumer_group: str = "foundry-lite-archive"
    poll_timeout_seconds: float = 1.0
    max_empty_polls: int = 1
    producer_flush_timeout_seconds: float = 5.0
    security_protocol: str | None = None
    sasl_mechanism: str | None = None
    sasl_username: str | None = None
    sasl_password: str | None = field(default=None, repr=False)


class KafkaStreamAdapter:
    """Kafka/Redpanda-backed stream adapter for production archive workers."""

    profile_name = "kafka-stream"

    def __init__(
        self,
        config: KafkaStreamAdapterConfig,
        *,
        consumer: KafkaConsumerLike | None = None,
        producer: KafkaProducerLike | None = None,
        topic_partition_factory: TopicPartitionFactory | None = None,
        consumer_factory: ConsumerFactory | None = None,
        producer_factory: ProducerFactory | None = None,
    ) -> None:
        """Create an adapter with optional injected broker clients for tests."""
        self.config = config
        self._subscriptions = {subscription.stream_name: subscription for subscription in config.subscriptions}
        self._consumer = consumer
        self._producer = producer
        self._owned_producer: KafkaProducerLike | None = None
        self._topic_partition_factory = topic_partition_factory
        self._consumer_factory = consumer_factory
        self._producer_factory = producer_factory

    def failure_contract(self) -> AdapterFailureContract:
        """Describe retry and operator behavior for Kafka failures."""
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "read_events",
                    "timeout",
                    True,
                    "Kafka stream read timed out; retry from the same committed offset checkpoint.",
                    timeout_seconds=30,
                ),
                AdapterFailureMode("read_events", "unavailable", True, "Kafka broker is unavailable."),
                AdapterFailureMode(
                    "publish_event",
                    "unsupported",
                    False,
                    "Kafka archive worker expects external producers.",
                ),
            ),
        )

    def read_events(self, stream_name: str, *, after_offset: int | None = None, limit: int = 100) -> list[StreamEvent]:
        """Read a bounded batch from a configured stream subscription."""
        if limit < 1:
            return []
        subscription = self._subscription(stream_name)
        consumer = self._consumer or self._build_consumer()
        start_offset = 0 if after_offset is None else after_offset + 1
        consumer.assign([self._topic_partition(subscription, start_offset)])
        try:
            return self._read_assigned_events(consumer, subscription, after_offset, limit)
        finally:
            if self._consumer is None:
                consumer.close()

    def publish_event(self, request: StreamPublishRequest) -> StreamEvent:
        """Publish one stream event through Kafka and return its broker offset."""
        subscription = self._subscription(request.stream_name)
        producer = self._publish_producer()
        payload = json.dumps(dict(request.payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
        offset = producer.produce(
            subscription.topic,
            key=request.key.encode("utf-8"),
            value=payload,
            headers=_publish_headers(request),
        )
        pending = producer.flush(self.config.producer_flush_timeout_seconds)
        if pending:
            raise self._error("publish_event", "timeout", "Kafka producer did not flush every message.")
        return StreamEvent(
            stream_name=request.stream_name,
            offset=offset if offset is not None else -1,
            event_type=request.event_type,
            tenant_id=request.tenant_id,
            request_id=request.request_id,
            key=request.key,
            payload=dict(request.payload),
        )

    def _publish_producer(self) -> KafkaProducerLike:
        if self._producer is not None:
            return self._producer
        if self._owned_producer is None:
            self._owned_producer = self._build_producer()
        return self._owned_producer

    def _read_assigned_events(
        self,
        consumer: KafkaConsumerLike,
        subscription: KafkaStreamSubscription,
        after_offset: int | None,
        limit: int,
    ) -> list[StreamEvent]:
        events: list[StreamEvent] = []
        empty_polls = 0
        while len(events) < limit and empty_polls <= self.config.max_empty_polls:
            event = self._poll_event(consumer, subscription)
            if event is None:
                empty_polls += 1
                continue
            empty_polls = 0
            if after_offset is None or event.offset > after_offset:
                events.append(event)
        return events

    def _poll_event(
        self,
        consumer: KafkaConsumerLike,
        subscription: KafkaStreamSubscription,
    ) -> StreamEvent | None:
        message = consumer.poll(self.config.poll_timeout_seconds)
        if message is None:
            return None
        error = message.error()
        if error is not None:
            raise self._error("read_events", "unavailable", "Kafka consumer returned an error.", {"error": str(error)})
        return _event_from_message(message, subscription)

    def _subscription(self, stream_name: str) -> KafkaStreamSubscription:
        subscription = self._subscriptions.get(stream_name)
        if subscription is None:
            raise self._error(
                "read_events",
                "validation",
                "Kafka stream subscription is not configured.",
                {"stream_name": stream_name},
            )
        return subscription

    def _topic_partition(self, subscription: KafkaStreamSubscription, offset: int) -> object:
        factory = self._topic_partition_factory or self._confluent_topic_partition_factory()
        return factory(subscription.topic, subscription.partition, offset)

    def _build_consumer(self) -> KafkaConsumerLike:
        if self._consumer_factory is not None:
            return self._consumer_factory(self._consumer_config())
        module = self._load_confluent("read_events")
        factory = cast(ConsumerFactory, module.__dict__["Consumer"])
        return factory(self._consumer_config())

    def _build_producer(self) -> KafkaProducerLike:
        if self._producer_factory is not None:
            return self._producer_factory(self._producer_config())
        module = self._load_confluent("publish_event")
        factory = cast(ProducerFactory, module.__dict__["Producer"])
        return factory(self._producer_config())

    def _confluent_topic_partition_factory(self) -> TopicPartitionFactory:
        module = self._load_confluent("read_events")
        return cast(TopicPartitionFactory, module.__dict__["TopicPartition"])

    def _load_confluent(self, operation: str) -> ModuleType:
        try:
            return importlib.import_module("confluent_kafka")
        except ModuleNotFoundError as exc:
            raise self._error(
                operation,
                "unavailable",
                "confluent_kafka is required for the production Kafka stream adapter.",
                {"package": "confluent-kafka"},
            ) from exc

    def _consumer_config(self) -> Mapping[str, object]:
        return {
            "bootstrap.servers": self.config.bootstrap_servers,
            "group.id": self.config.consumer_group,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
            **self._security_config(),
        }

    def _producer_config(self) -> Mapping[str, object]:
        return {"bootstrap.servers": self.config.bootstrap_servers, **self._security_config()}

    def _security_config(self) -> Mapping[str, object]:
        values = {
            "security.protocol": self.config.security_protocol,
            "sasl.mechanism": self.config.sasl_mechanism,
            "sasl.username": self.config.sasl_username,
            "sasl.password": self.config.sasl_password,
        }
        return {key: value for key, value in values.items() if value is not None}

    def _error(
        self,
        operation: str,
        kind: AdapterFailureKind,
        message: str,
        details: Mapping[str, object] | None = None,
    ) -> AdapterError:
        return AdapterError(
            AdapterFailure(
                adapter_profile=self.profile_name,
                operation=operation,
                kind=kind,
                is_retryable=kind in {"timeout", "unavailable"},
                operator_message=message,
                timeout_seconds=30 if kind == "timeout" else None,
                details=dict(details or {}),
            )
        )


def _event_from_message(message: KafkaMessageLike, subscription: KafkaStreamSubscription) -> StreamEvent:
    headers = _headers_by_name(message.headers())
    payload = _payload_from_value(message.value())
    offset = message.offset()
    return StreamEvent(
        stream_name=subscription.stream_name,
        offset=offset,
        event_type=_metadata_value(headers, payload, ("foundry-event-type", "event_type"), ("event_type", "eventType"))
        or "stream.message",
        tenant_id=_metadata_value(headers, payload, ("foundry-tenant-id", "tenant_id"), ("tenant_id", "tenantId"))
        or subscription.default_tenant_id,
        request_id=_metadata_value(headers, payload, ("foundry-request-id", "request_id"), ("request_id", "requestId"))
        or f"kafka:{subscription.topic}:{subscription.partition}:{offset}",
        key=_message_key(message.key(), offset),
        payload=payload,
    )


def _headers_by_name(headers: Sequence[tuple[str, bytes | str | None]] | None) -> Mapping[str, str]:
    return {name.lower(): _decode_value(value) for name, value in headers or () if value is not None}


def _metadata_value(
    headers: Mapping[str, str],
    payload: Mapping[str, object],
    header_names: Sequence[str],
    payload_names: Sequence[str],
) -> str | None:
    for name in header_names:
        if name in headers:
            return headers[name]
    for name in payload_names:
        value = payload.get(name)
        if value is not None:
            return str(value)
    return None


def _payload_from_value(value: bytes | str | Mapping[str, object] | None) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return dict(value)
    if value is None:
        return {}
    text = _decode_value(value)
    if not text:
        return {}
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError:
        return {"value": text}
    return dict(parsed) if isinstance(parsed, Mapping) else {"value": parsed}


def _message_key(value: bytes | str | None, offset: int) -> str:
    return _decode_value(value) if value is not None else f"offset:{offset}"


def _decode_value(value: bytes | str) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else value


def _publish_headers(request: StreamPublishRequest) -> list[tuple[str, bytes]]:
    return [
        ("foundry-event-type", request.event_type.encode("utf-8")),
        ("foundry-tenant-id", request.tenant_id.encode("utf-8")),
        ("foundry-request-id", request.request_id.encode("utf-8")),
    ]
