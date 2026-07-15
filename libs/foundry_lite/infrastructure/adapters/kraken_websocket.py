"""Kraken Spot WebSocket v2 adapter for public real-time trade events."""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from math import ceil
from types import ModuleType
from typing import Literal, Protocol, cast

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureMode,
)
from foundry_lite.application.ports.stream_adapter import StreamPublishRequest
from foundry_lite.domain.context import RequestContext


class KrakenWebSocketConnection(Protocol):
    def send(self, message: str) -> None: ...

    def recv(self, timeout: float | None = None) -> str | bytes: ...

    def close(self) -> None: ...


KrakenConnectionFactory = Callable[[str, float], KrakenWebSocketConnection]
KrakenSignalKind = Literal["trade", "heartbeat", "status", "subscribed", "timeout", "ignored"]


@dataclass(frozen=True)
class KrakenWebSocketV2Config:
    websocket_url: str = "wss://ws.kraken.com/v2"
    symbol: str = "BTC/USD"
    receive_timeout_seconds: float = 2.0
    open_timeout_seconds: float = 10.0


@dataclass(frozen=True)
class KrakenTrade:
    symbol: str
    side: str
    price: float
    quantity: float
    order_type: str
    trade_id: int
    timestamp: str


@dataclass(frozen=True)
class KrakenSignal:
    kind: KrakenSignalKind
    trades: tuple[KrakenTrade, ...] = ()
    exchange_status: str | None = None
    connection_id: str | None = None


class KrakenWebSocketV2Adapter:
    """Connect, subscribe, and normalize Kraken public trade messages."""

    profile_name = "kraken-websocket-v2"

    def __init__(
        self,
        config: KrakenWebSocketV2Config,
        *,
        connection_factory: KrakenConnectionFactory | None = None,
    ) -> None:
        self.config = config
        self._connection_factory = connection_factory

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode("connect", "unavailable", True, "Kraken WebSocket is unavailable."),
                AdapterFailureMode("receive", "timeout", True, "Kraken WebSocket receive timed out."),
                AdapterFailureMode("receive", "unavailable", True, "Kraken WebSocket connection closed."),
                AdapterFailureMode("subscribe", "validation", False, "Kraken rejected the trade subscription."),
            ),
        )

    def connect(self) -> KrakenWebSocketConnection:
        try:
            factory = self._connection_factory or self._default_connection_factory
            return factory(self.config.websocket_url, self.config.open_timeout_seconds)
        except AdapterError:
            raise
        except Exception as exc:
            raise self._error("connect", "unavailable", "Kraken WebSocket connection failed.") from exc

    def subscribe(self, connection: KrakenWebSocketConnection) -> None:
        request = {
            "method": "subscribe",
            "params": {"channel": "trade", "symbol": [self.config.symbol], "snapshot": False},
            "req_id": 1,
        }
        try:
            connection.send(json.dumps(request, separators=(",", ":")))
        except Exception as exc:
            raise self._error("subscribe", "unavailable", "Kraken trade subscription could not be sent.") from exc

    def receive(self, connection: KrakenWebSocketConnection) -> KrakenSignal:
        try:
            message = connection.recv(timeout=self.config.receive_timeout_seconds)
        except TimeoutError:
            return KrakenSignal(kind="timeout")
        except Exception as exc:
            raise self._error("receive", "unavailable", "Kraken WebSocket receive failed.") from exc
        return _signal_from_message(message)

    def publish_request(self, trade: KrakenTrade, ctx: RequestContext, *, stream_name: str) -> StreamPublishRequest:
        trade_key = f"kraken:{trade.symbol}:{trade.trade_id}"
        return StreamPublishRequest(
            stream_name=stream_name,
            event_type="kraken.trade",
            tenant_id=ctx.tenant_id,
            request_id=f"{ctx.request_id}:trade:{trade.trade_id}",
            key=trade_key,
            payload={
                "provider": "kraken",
                "channel": "trade",
                "symbol": trade.symbol,
                "side": trade.side,
                "price": trade.price,
                "quantity": trade.quantity,
                "orderType": trade.order_type,
                "tradeId": trade.trade_id,
                "event_time": trade.timestamp,
            },
        )

    def _default_connection_factory(self, url: str, timeout_seconds: float) -> KrakenWebSocketConnection:
        module = _websockets_sync_client()
        connect = cast(Callable[..., KrakenWebSocketConnection], module.__dict__["connect"])
        return connect(url, open_timeout=timeout_seconds, ping_interval=20, ping_timeout=20)

    def _error(self, operation: str, kind: str, message: str) -> AdapterError:
        return AdapterError(
            AdapterFailure(
                adapter_profile=self.profile_name,
                operation=operation,
                kind=cast(Literal["timeout", "unavailable", "validation"], kind),
                is_retryable=kind in {"timeout", "unavailable"},
                operator_message=message,
                timeout_seconds=max(1, ceil(self.config.receive_timeout_seconds)) if kind == "timeout" else None,
                details={"symbol": self.config.symbol},
            )
        )


def _signal_from_message(message: str | bytes) -> KrakenSignal:
    payload = _json_object(message)
    channel = payload.get("channel")
    if channel == "heartbeat":
        return KrakenSignal(kind="heartbeat")
    if channel == "status":
        return _status_signal(payload)
    if payload.get("method") == "subscribe":
        return _subscription_signal(payload)
    if channel == "trade":
        return KrakenSignal(kind="trade", trades=_trades(payload))
    return KrakenSignal(kind="ignored")


def _json_object(message: str | bytes) -> Mapping[str, object]:
    try:
        parsed: object = json.loads(message.decode("utf-8") if isinstance(message, bytes) else message)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _subscription_signal(payload: Mapping[str, object]) -> KrakenSignal:
    if payload.get("success") is False:
        error = str(payload.get("error") or "Kraken rejected the trade subscription.")
        raise AdapterError(
            AdapterFailure(
                adapter_profile="kraken-websocket-v2",
                operation="subscribe",
                kind="validation",
                is_retryable=False,
                operator_message=error,
            )
        )
    return KrakenSignal(kind="subscribed")


def _status_signal(payload: Mapping[str, object]) -> KrakenSignal:
    data = payload.get("data")
    first = data[0] if isinstance(data, list) and data and isinstance(data[0], Mapping) else {}
    status = first.get("system") if isinstance(first, Mapping) else None
    connection_id = first.get("connection_id") if isinstance(first, Mapping) else None
    return KrakenSignal(
        kind="status",
        exchange_status=str(status) if status is not None else None,
        connection_id=str(connection_id) if connection_id is not None else None,
    )


def _trades(payload: Mapping[str, object]) -> tuple[KrakenTrade, ...]:
    data = payload.get("data")
    if not isinstance(data, list):
        return ()
    return tuple(trade for item in data if isinstance(item, Mapping) if (trade := _trade(item)) is not None)


def _trade(payload: Mapping[str, object]) -> KrakenTrade | None:
    try:
        return KrakenTrade(
            symbol=str(payload["symbol"]),
            side=str(payload["side"]),
            price=_float_value(payload["price"]),
            quantity=_float_value(payload["qty"]),
            order_type=str(payload["ord_type"]),
            trade_id=_integer_value(payload["trade_id"]),
            timestamp=str(payload["timestamp"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _float_value(value: object) -> float:
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return float(value)
    raise ValueError("Kraken numeric field is invalid")


def _integer_value(value: object) -> int:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return int(value)
    raise ValueError("Kraken integer field is invalid")


def _websockets_sync_client() -> ModuleType:
    try:
        return importlib.import_module("websockets.sync.client")
    except ModuleNotFoundError as exc:
        raise AdapterError(
            AdapterFailure(
                adapter_profile="kraken-websocket-v2",
                operation="connect",
                kind="unavailable",
                is_retryable=False,
                operator_message="The websockets package is required for Kraken streaming.",
                details={"package": "websockets"},
            )
        ) from exc
