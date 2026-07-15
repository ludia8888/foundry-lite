from __future__ import annotations

import json
from collections import deque
from threading import Event

from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.adapters import (
    KrakenWebSocketV2Adapter,
    KrakenWebSocketV2Config,
    LocalStreamAdapter,
)
from foundry_lite_worker.kraken_kafka_bridge import (
    KrakenBridgeTelemetry,
    KrakenKafkaBridgeConfig,
    run_kraken_kafka_bridge,
)


class _FakeConnection:
    def __init__(self, messages: list[str]) -> None:
        self.messages = deque(messages)
        self.sent: list[str] = []
        self.is_closed = False

    def send(self, message: str) -> None:
        self.sent.append(message)

    def recv(self, timeout: float | None = None) -> str:
        if self.messages:
            return self.messages.popleft()
        raise ConnectionError("test connection closed")

    def close(self) -> None:
        self.is_closed = True


def test_kraken_bridge_normalizes_trade_and_publishes_to_kafka_stream() -> None:
    connection = _FakeConnection(
        [
            json.dumps(
                {
                    "channel": "status",
                    "type": "update",
                    "data": [{"system": "online", "connection_id": 42, "api_version": "v2"}],
                }
            ),
            json.dumps({"method": "subscribe", "success": True, "result": {"channel": "trade"}}),
            json.dumps(
                {
                    "channel": "trade",
                    "type": "update",
                    "data": [
                        {
                            "symbol": "BTC/USD",
                            "side": "buy",
                            "price": 101250.5,
                            "qty": 0.001,
                            "ord_type": "market",
                            "trade_id": 987654,
                            "timestamp": "2026-07-16T00:00:00.123456Z",
                        }
                    ],
                }
            ),
        ]
    )
    kraken = KrakenWebSocketV2Adapter(
        KrakenWebSocketV2Config(),
        connection_factory=lambda _url, _timeout: connection,
    )
    kafka = LocalStreamAdapter()
    telemetry = KrakenBridgeTelemetry()

    run_kraken_kafka_bridge(
        KrakenKafkaBridgeConfig(
            stream_name="kraken-btc-usd",
            reconnect_base_seconds=0,
            reconnect_max_seconds=0,
        ),
        kraken=kraken,
        kafka=kafka,
        ctx=demo_admin_context(),
        stop_event=Event(),
        telemetry=telemetry,
        max_connections=1,
    )

    events = kafka.read_events("kraken-btc-usd", limit=10)
    subscription = json.loads(connection.sent[0])
    snapshot = telemetry.snapshot()
    assert subscription["params"] == {"channel": "trade", "symbol": ["BTC/USD"], "snapshot": False}
    assert connection.is_closed is True
    assert len(events) == 1
    assert events[0].key == "kraken:BTC/USD:987654"
    assert events[0].event_type == "kraken.trade"
    assert events[0].payload["event_time"] == "2026-07-16T00:00:00.123456Z"
    assert events[0].payload["price"] == 101250.5
    assert snapshot.published_records == 1
    assert snapshot.exchange_status == "online"
    assert snapshot.connection_id == "42"
    assert snapshot.reconnect_count == 1
