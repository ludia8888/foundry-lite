"""Debezium PostgreSQL CDC stream normalization adapter."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureKind,
    AdapterFailureMode,
)
from foundry_lite.application.ports.stream_adapter import StreamAdapter, StreamEvent, StreamPublishRequest

DEBEZIUM_OPS = frozenset({"c", "u", "d", "r"})


@dataclass(frozen=True)
class DebeziumPostgresSourceConfig:
    """Source definition for a Debezium PostgreSQL CDC topic."""

    primary_key: tuple[str, ...]


class DebeziumPostgresStreamAdapter:
    """Normalize Debezium PostgreSQL envelopes behind the StreamAdapter port."""

    profile_name = "debezium-postgres-stream"

    def __init__(self, inner: StreamAdapter, config: DebeziumPostgresSourceConfig) -> None:
        self.inner = inner
        self.config = config

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "read_events",
                    "validation",
                    False,
                    "Debezium CDC payload could not be normalized into the standard CDC envelope.",
                ),
                AdapterFailureMode(
                    "read_events",
                    "timeout",
                    True,
                    "CDC stream read timed out; retry from the same committed offset checkpoint.",
                    timeout_seconds=30,
                ),
            ),
        )

    def publish_event(self, request: StreamPublishRequest) -> StreamEvent:
        return self._normalize_event(self.inner.publish_event(request), operation="publish_event")

    def read_events(self, stream_name: str, *, after_offset: int | None = None, limit: int = 100) -> list[StreamEvent]:
        return [
            self._normalize_event(event, operation="read_events")
            for event in self.inner.read_events(stream_name, after_offset=after_offset, limit=limit)
        ]

    def close(self) -> None:
        """Release resources owned by the wrapped broker adapter."""
        self.inner.close()

    def _normalize_event(self, event: StreamEvent, *, operation: str) -> StreamEvent:
        try:
            payload = dict(normalize_debezium_postgres_payload(event.payload, self.config.primary_key))
        except DebeziumPayloadError as exc:
            raise self._error(operation, "validation", exc.message, exc.details) from exc
        ordering = payload.get("ordering")
        if not isinstance(ordering, Mapping):
            raise self._error(operation, "validation", "CDC payload ordering is not an object", {"ordering": ordering})
        payload["ordering"] = {
            **dict(ordering),
            "stream_name": event.stream_name,
            "stream_offset": event.offset,
        }
        return StreamEvent(
            stream_name=event.stream_name,
            offset=event.offset,
            event_type=f"cdc.{payload['op']}",
            tenant_id=event.tenant_id,
            request_id=event.request_id,
            key=event.key,
            payload=payload,
        )

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


@dataclass(frozen=True)
class DebeziumPayloadError(Exception):
    message: str
    details: Mapping[str, object]


def normalize_debezium_postgres_payload(
    payload: Mapping[str, object],
    primary_key: tuple[str, ...],
) -> Mapping[str, object]:
    body = _payload_body(payload)
    op = _operation(body)
    before = _nullable_mapping(body, "before")
    after = _nullable_mapping(body, "after")
    source = _required_mapping(body, "source")
    return {
        "op": op,
        "pk": _primary_key(primary_key, before, after),
        "before": before,
        "after": after,
        "ordering": _ordering(body, source),
    }


def _payload_body(payload: Mapping[str, object]) -> Mapping[str, object]:
    nested = payload.get("payload")
    if isinstance(nested, Mapping):
        return dict(nested)
    if not payload:
        raise DebeziumPayloadError("Debezium CDC payload is empty or tombstoned.", {"field": "payload"})
    return dict(payload)


def _operation(body: Mapping[str, object]) -> str:
    op = body.get("op")
    if not isinstance(op, str) or op not in DEBEZIUM_OPS:
        raise DebeziumPayloadError("Debezium CDC operation is invalid.", {"field": "op", "value": str(op)})
    return op


def _nullable_mapping(body: Mapping[str, object], name: str) -> Mapping[str, object] | None:
    value = body.get(name)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise DebeziumPayloadError("Debezium CDC envelope field must be an object or null.", {"field": name})
    return dict(value)


def _required_mapping(body: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = body.get(name)
    if not isinstance(value, Mapping):
        raise DebeziumPayloadError("Debezium CDC envelope field must be an object.", {"field": name})
    return dict(value)


def _primary_key(
    primary_key: tuple[str, ...],
    before: Mapping[str, object] | None,
    after: Mapping[str, object] | None,
) -> Mapping[str, object]:
    if not primary_key:
        raise DebeziumPayloadError("Debezium CDC source must declare a primary key.", {"field": "primary_key"})
    row = after or before
    if row is None:
        raise DebeziumPayloadError("Debezium CDC event has no row image for primary-key extraction.", {"field": "pk"})
    return {name: _required_pk_value(row, name) for name in primary_key}


def _required_pk_value(row: Mapping[str, object], name: str) -> object:
    value = row.get(name)
    if value is None:
        raise DebeziumPayloadError("Debezium CDC primary-key field is missing.", {"field": name})
    return value


def _ordering(body: Mapping[str, object], source: Mapping[str, object]) -> Mapping[str, object]:
    source_ts_ms = source.get("ts_ms", body.get("ts_ms"))
    ordering: dict[str, object] = {
        "lsn": source.get("lsn"),
        "source_ts_ms": source_ts_ms,
        "table": source.get("table"),
    }
    _copy_optional_ordering_value(ordering, source, "txId", "tx_id")
    transaction = body.get("transaction")
    if isinstance(transaction, Mapping):
        _copy_optional_ordering_value(ordering, transaction, "id", "transaction_id")
        _copy_optional_ordering_value(ordering, transaction, "total_order", "transaction_order")
        _copy_optional_ordering_value(ordering, transaction, "data_collection_order", "data_collection_order")
    return ordering


def _copy_optional_ordering_value(
    ordering: dict[str, object],
    source: Mapping[str, object],
    source_key: str,
    target_key: str,
) -> None:
    value = source.get(source_key)
    if value is not None:
        ordering[target_key] = value
