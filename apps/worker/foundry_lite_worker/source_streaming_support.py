"""Pure parsing and telemetry helpers for the Source streaming worker."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from threading import Event, Thread
from typing import Protocol

from foundry_lite.domain.error_redaction import scrub_error_mapping, scrub_error_text
from foundry_lite.domain.errors import FoundryLiteError, ValidationFailed

from foundry_lite_worker.kraken_kafka_bridge import KrakenBridgeSnapshot


@dataclass(frozen=True)
class _HeartbeatHandle:
    stop_event: Event
    thread: Thread
    errors: list[Exception]


class RetryBackoffConfig(Protocol):
    @property
    def retry_base_seconds(self) -> float: ...

    @property
    def retry_max_seconds(self) -> float: ...


def run_with_heartbeat[ResultT](
    *,
    operation: Callable[[], ResultT],
    heartbeat: Callable[[], object],
    interval_seconds: float,
) -> ResultT:
    handle = _start_heartbeat(heartbeat, interval_seconds)
    try:
        result = operation()
    except Exception:
        _stop_heartbeat(handle)
        raise
    _stop_heartbeat(handle)
    if handle.errors:
        raise handle.errors[0]
    return result


def _start_heartbeat(heartbeat: Callable[[], object], interval_seconds: float) -> _HeartbeatHandle:
    stop_event = Event()
    errors: list[Exception] = []

    def refresh() -> None:
        while not stop_event.wait(interval_seconds):
            try:
                heartbeat()
            except Exception as exc:
                errors.append(exc)
                return

    thread = Thread(target=refresh, name="source-streaming-lease-heartbeat", daemon=True)
    thread.start()
    return _HeartbeatHandle(stop_event=stop_event, thread=thread, errors=errors)


def _stop_heartbeat(handle: _HeartbeatHandle) -> None:
    handle.stop_event.set()
    handle.thread.join(timeout=5.0)


def bridge_payload(bridge: KrakenBridgeSnapshot) -> dict[str, object]:
    return {
        "provider": "kraken_websocket_v2",
        "connectionState": bridge.connection_state,
        "exchangeStatus": bridge.exchange_status,
        "connectionId": bridge.connection_id,
        "lastMessageAt": bridge.last_message_at,
        "lastTradeAt": bridge.last_trade_at,
        "publishedRecords": bridge.published_records,
        "reconnectCount": bridge.reconnect_count,
        "consecutiveFailures": bridge.consecutive_failures,
        "lastError": bridge.last_error,
    }


def worker_error_payload(exc: Exception) -> Mapping[str, object]:
    if isinstance(exc, FoundryLiteError):
        return {
            "code": exc.code,
            "message": scrub_error_text(exc.message),
            "details": scrub_error_mapping(exc.details),
        }
    return {"code": "WORKER_FAILURE", "message": f"source streaming worker failed with {type(exc).__name__}"}


def mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def integer(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def output_rate(rows: int, elapsed_ms: float | None) -> float | None:
    if elapsed_ms is None or elapsed_ms <= 0:
        return None
    return round(rows * 1000 / elapsed_ms, 3)


def kafka_telemetry(
    summary: Mapping[str, object], run: Mapping[str, object], previous: Mapping[str, object]
) -> dict[str, object]:
    if run.get("status") == "failed":
        return dict(previous)
    checkpoint = mapping(run.get("checkpointEnd"))
    partition_rows = _partition_telemetry(summary)
    if partition_rows:
        return {
            "topic": summary.get("topic"),
            "consumerGroup": summary.get("consumerGroup"),
            "partitionCount": len(partition_rows),
            "partitions": partition_rows,
            "currentOffset": None,
            "brokerEndOffset": None,
            "brokerLag": summary.get("brokerLag"),
            "brokerLagStatus": summary.get("brokerLagStatus"),
            "brokerLagError": summary.get("brokerLagError"),
            "deliveryGuarantee": summary.get("deliveryGuarantee", "AT_LEAST_ONCE"),
        }
    return {
        "topic": summary.get("topic"),
        "partition": summary.get("partition"),
        "consumerGroup": summary.get("consumerGroup"),
        "currentOffset": checkpoint.get("offset"),
        "brokerEndOffset": summary.get("brokerEndOffset"),
        "brokerLag": summary.get("brokerLag"),
        "brokerLagStatus": summary.get("brokerLagStatus"),
        "brokerLagError": summary.get("brokerLagError"),
        "partitionCount": 1,
        "partitions": [
            {
                "partition": summary.get("partition"),
                "currentOffset": checkpoint.get("offset"),
                "brokerEndOffset": summary.get("brokerEndOffset"),
                "brokerLag": summary.get("brokerLag"),
                "eventCount": summary.get("eventCount"),
            }
        ],
        "deliveryGuarantee": summary.get("deliveryGuarantee", "AT_LEAST_ONCE"),
    }


def _partition_telemetry(summary: Mapping[str, object]) -> list[dict[str, object]]:
    raw = summary.get("partitions")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    return [
        {
            "partition": item.get("partition"),
            "currentOffset": mapping(item.get("checkpoint")).get("offset"),
            "brokerEndOffset": item.get("brokerEndOffset"),
            "brokerLag": item.get("brokerLag"),
            "eventCount": item.get("eventCount"),
        }
        for item in raw
        if isinstance(item, Mapping)
    ]


def optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def required_config(summary: Mapping[str, object], key: str) -> str:
    value = optional_text(summary.get(key))
    if value is None:
        raise ValidationFailed("streaming sync configuration is incomplete", details={"missingField": key})
    return value


def required(field: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"source streaming worker requires {field}")
    return normalized


def retry_delay(config: RetryBackoffConfig, failures: int) -> float:
    base = float(config.retry_base_seconds)
    maximum = float(config.retry_max_seconds)
    return min(maximum, base * (2 ** min(max(failures - 1, 0), 10)))


def maximum_reached(current: int, maximum: int | None) -> bool:
    return maximum is not None and current >= maximum
