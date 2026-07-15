"""Reconnectable Kraken WebSocket v2 to Kafka producer bridge."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event, Lock
from time import monotonic

from foundry_lite.application.ports import StreamAdapter
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure.adapters import KrakenSignal, KrakenWebSocketV2Adapter


@dataclass(frozen=True)
class KrakenKafkaBridgeConfig:
    stream_name: str
    reconnect_base_seconds: float = 1.0
    reconnect_max_seconds: float = 30.0


@dataclass(frozen=True)
class KrakenBridgeSnapshot:
    connection_state: str
    exchange_status: str | None
    connection_id: str | None
    last_message_at: str | None
    last_trade_at: str | None
    published_records: int
    reconnect_count: int
    consecutive_failures: int
    input_rate_per_second: float
    last_error: dict[str, object] | None


class KrakenBridgeTelemetry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._started = monotonic()
        self._connection_state = "STARTING"
        self._exchange_status: str | None = None
        self._connection_id: str | None = None
        self._last_message_at: str | None = None
        self._last_trade_at: str | None = None
        self._published_records = 0
        self._reconnect_count = 0
        self._consecutive_failures = 0
        self._last_error: dict[str, object] | None = None

    def connected(self) -> None:
        with self._lock:
            self._connection_state = "CONNECTED"
            self._consecutive_failures = 0
            self._last_error = None

    def observe(self, signal: KrakenSignal) -> None:
        with self._lock:
            if signal.kind != "timeout":
                self._last_message_at = _utc_now()
            if signal.exchange_status is not None:
                self._exchange_status = signal.exchange_status
            if signal.connection_id is not None:
                self._connection_id = signal.connection_id

    def published(self, count: int, event_time: str | None) -> None:
        with self._lock:
            self._published_records += count
            self._last_trade_at = event_time or _utc_now()
            self._connection_state = "CONNECTED"
            self._consecutive_failures = 0

    def reconnecting(self, error: dict[str, object]) -> None:
        with self._lock:
            self._connection_state = "RECONNECTING"
            self._reconnect_count += 1
            self._consecutive_failures += 1
            self._last_error = error

    def stopped(self) -> None:
        with self._lock:
            self._connection_state = "STOPPED"

    def failed(self, error: dict[str, object]) -> None:
        with self._lock:
            self._connection_state = "FAILED"
            self._consecutive_failures += 1
            self._last_error = error

    def snapshot(self) -> KrakenBridgeSnapshot:
        with self._lock:
            elapsed = max(monotonic() - self._started, 0.001)
            return KrakenBridgeSnapshot(
                connection_state=self._connection_state,
                exchange_status=self._exchange_status,
                connection_id=self._connection_id,
                last_message_at=self._last_message_at,
                last_trade_at=self._last_trade_at,
                published_records=self._published_records,
                reconnect_count=self._reconnect_count,
                consecutive_failures=self._consecutive_failures,
                input_rate_per_second=round(self._published_records / elapsed, 3),
                last_error=dict(self._last_error) if self._last_error is not None else None,
            )


def run_kraken_kafka_bridge(
    config: KrakenKafkaBridgeConfig,
    *,
    kraken: KrakenWebSocketV2Adapter,
    kafka: StreamAdapter,
    ctx: RequestContext,
    stop_event: Event,
    telemetry: KrakenBridgeTelemetry,
    max_connections: int | None = None,
) -> None:
    connections = 0
    while not stop_event.is_set() and not _limit_reached(connections, max_connections):
        connections += 1
        try:
            _run_connection(config, kraken, kafka, ctx, stop_event, telemetry)
        except AdapterError as exc:
            error = dict(exc.failure.to_payload())
            if not exc.failure.is_retryable:
                telemetry.failed(error)
                return
            telemetry.reconnecting(error)
            if stop_event.wait(_reconnect_delay(config, telemetry.snapshot().consecutive_failures)):
                break
        except Exception as exc:
            telemetry.reconnecting(_unexpected_error(exc))
            if stop_event.wait(_reconnect_delay(config, telemetry.snapshot().consecutive_failures)):
                break
    telemetry.stopped()


def _run_connection(
    config: KrakenKafkaBridgeConfig,
    kraken: KrakenWebSocketV2Adapter,
    kafka: StreamAdapter,
    ctx: RequestContext,
    stop_event: Event,
    telemetry: KrakenBridgeTelemetry,
) -> None:
    connection = kraken.connect()
    try:
        kraken.subscribe(connection)
        telemetry.connected()
        while not stop_event.is_set():
            signal = kraken.receive(connection)
            telemetry.observe(signal)
            _publish_signal(config, kraken, kafka, ctx, telemetry, signal)
    finally:
        connection.close()


def _publish_signal(
    config: KrakenKafkaBridgeConfig,
    kraken: KrakenWebSocketV2Adapter,
    kafka: StreamAdapter,
    ctx: RequestContext,
    telemetry: KrakenBridgeTelemetry,
    signal: KrakenSignal,
) -> None:
    if signal.kind != "trade":
        return
    for trade in signal.trades:
        kafka.publish_event(kraken.publish_request(trade, ctx, stream_name=config.stream_name))
        telemetry.published(1, trade.timestamp)


def _reconnect_delay(config: KrakenKafkaBridgeConfig, failures: int) -> float:
    exponent = min(max(failures - 1, 0), 10)
    return min(config.reconnect_max_seconds, config.reconnect_base_seconds * (2**exponent))


def _limit_reached(connections: int, maximum: int | None) -> bool:
    return maximum is not None and connections >= maximum


def _unexpected_error(exc: Exception) -> dict[str, object]:
    return {
        "adapterProfile": "kraken-websocket-v2",
        "operation": "bridge",
        "kind": "unavailable",
        "isRetryable": True,
        "operatorMessage": f"Kraken bridge failed with {type(exc).__name__}.",
    }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
