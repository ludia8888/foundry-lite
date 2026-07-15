from __future__ import annotations

import json
from collections import deque
from types import ModuleType

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailure
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.adapters import kraken_websocket as kraken_module
from foundry_lite.infrastructure.adapters.kraken_websocket import (
    KrakenWebSocketV2Adapter,
    KrakenWebSocketV2Config,
)


class _Connection:
    def __init__(self, messages: list[str | bytes] | None = None) -> None:
        self.messages = deque(messages or [])
        self.sent: list[str] = []

    def send(self, message: str) -> None:
        self.sent.append(message)

    def recv(self, timeout: float | None = None) -> str | bytes:
        assert timeout == 2.0
        return self.messages.popleft()

    def close(self) -> None:
        return None


def test_kraken_adapter_normalizes_protocol_signals_and_publish_request() -> None:
    connection = _Connection(
        [
            '{"channel":"heartbeat"}',
            '{"channel":"status","data":[{"system":"online","connection_id":42}]}',
            '{"method":"subscribe","success":true}',
            json.dumps(
                {
                    "channel": "trade",
                    "data": [
                        {
                            "symbol": "BTC/USD",
                            "side": "buy",
                            "price": "100.5",
                            "qty": 2,
                            "ord_type": "market",
                            "trade_id": "7",
                            "timestamp": "2026-07-16T00:00:00Z",
                        },
                        {"symbol": "invalid"},
                    ],
                }
            ),
            b"not-json",
        ]
    )
    adapter = KrakenWebSocketV2Adapter(KrakenWebSocketV2Config(), connection_factory=lambda *_args: connection)
    assert adapter.connect() is connection
    adapter.subscribe(connection)
    assert json.loads(connection.sent[0])["params"]["channel"] == "trade"
    assert adapter.receive(connection).kind == "heartbeat"
    status = adapter.receive(connection)
    assert status.exchange_status == "online"
    assert status.connection_id == "42"
    assert adapter.receive(connection).kind == "subscribed"
    trade = adapter.receive(connection).trades[0]
    assert trade.trade_id == 7
    assert trade.price == 100.5
    assert adapter.receive(connection).kind == "ignored"
    assert kraken_module._signal_from_message("[]").kind == "ignored"

    request = adapter.publish_request(trade, demo_admin_context(), stream_name="kraken-live")
    assert request.key == "kraken:BTC/USD:7"
    assert request.payload["quantity"] == 2.0
    assert adapter.failure_contract().modes[0].operation == "connect"


def test_kraken_adapter_maps_connection_subscription_and_receive_failures() -> None:
    adapter = KrakenWebSocketV2Adapter(
        KrakenWebSocketV2Config(),
        connection_factory=lambda *_args: (_ for _ in ()).throw(OSError("offline")),
    )
    with pytest.raises(AdapterError) as connect_error:
        adapter.connect()
    assert connect_error.value.failure.operation == "connect"

    original_error = AdapterError(
        AdapterFailure(
            adapter_profile="kraken-websocket-v2",
            operation="connect",
            kind="unavailable",
            is_retryable=True,
            operator_message="offline",
        )
    )
    passthrough = KrakenWebSocketV2Adapter(
        KrakenWebSocketV2Config(),
        connection_factory=lambda *_args: (_ for _ in ()).throw(original_error),
    )
    with pytest.raises(AdapterError) as passthrough_error:
        passthrough.connect()
    assert passthrough_error.value is original_error

    class _FailingConnection(_Connection):
        def send(self, _message: str) -> None:
            raise OSError("closed")

        def recv(self, timeout: float | None = None) -> str:
            raise OSError("closed")

    failing = _FailingConnection()
    with pytest.raises(AdapterError) as subscribe_error:
        adapter.subscribe(failing)
    assert subscribe_error.value.failure.operation == "subscribe"
    with pytest.raises(AdapterError) as receive_error:
        adapter.receive(failing)
    assert receive_error.value.failure.operation == "receive"

    class _TimeoutConnection(_Connection):
        def recv(self, timeout: float | None = None) -> str:
            raise TimeoutError

    assert adapter.receive(_TimeoutConnection()).kind == "timeout"
    rejected = _Connection(['{"method":"subscribe","success":false,"error":"bad symbol"}'])
    with pytest.raises(AdapterError, match="bad symbol"):
        adapter.receive(rejected)


def test_kraken_adapter_default_factory_and_missing_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _Connection()
    module = ModuleType("websockets.sync.client")
    module.connect = lambda url, **kwargs: (url, kwargs, connection)[2]
    default_module_loader = kraken_module._websockets_sync_client
    monkeypatch.setattr(kraken_module, "_websockets_sync_client", lambda: module)
    adapter = KrakenWebSocketV2Adapter(KrakenWebSocketV2Config())
    assert adapter.connect() is connection

    monkeypatch.setattr(kraken_module, "_websockets_sync_client", default_module_loader)
    monkeypatch.setattr(
        kraken_module.importlib,
        "import_module",
        lambda _name: (_ for _ in ()).throw(ModuleNotFoundError("websockets")),
    )
    with pytest.raises(AdapterError) as missing:
        kraken_module._websockets_sync_client()
    assert missing.value.failure.details == {"package": "websockets"}


@pytest.mark.parametrize(
    "payload",
    [
        {
            "symbol": "BTC/USD",
            "side": "buy",
            "price": True,
            "qty": 1,
            "ord_type": "market",
            "trade_id": 1,
            "timestamp": "now",
        },
        {
            "symbol": "BTC/USD",
            "side": "buy",
            "price": 1,
            "qty": 1,
            "ord_type": "market",
            "trade_id": False,
            "timestamp": "now",
        },
    ],
)
def test_kraken_adapter_ignores_invalid_numeric_trade_fields(payload: dict[str, object]) -> None:
    signal = kraken_module._signal_from_message(json.dumps({"channel": "trade", "data": [payload]}))
    assert signal.kind == "trade"
    assert signal.trades == ()
