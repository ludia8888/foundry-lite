"""The sandbox query bridge is bounded, nonce-scoped, and transports no credentials."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from pathlib import Path

from foundry_lite.infrastructure.adapters.function_query_bridge import FunctionQueryBridge
from foundry_lite.infrastructure.runners.python_function_osdk import QueryBridge


def test_networkless_query_bridge_round_trips_one_governed_page(tmp_path: Path) -> None:
    seen: list[dict[str, object]] = []

    def execute(request: Mapping[str, object]) -> Mapping[str, object]:
        seen.append(dict(request))
        return {"items": [{"objectId": "T-1", "properties": {"seats": 4}}], "nextCursor": None}

    host = FunctionQueryBridge(tmp_path, "nonce-1", execute)
    with host:
        result = QueryBridge(tmp_path, "nonce-1", 1).call(
            {"operation": "fetchPage", "objectType": "DiningTable", "pageSize": 500}
        )

    assert result["items"][0]["objectId"] == "T-1"  # type: ignore[index]
    assert seen == [{"operation": "fetchPage", "objectType": "DiningTable", "pageSize": 500}]
    host.raise_if_failed()


def test_a_request_with_another_nonce_is_not_executed(tmp_path: Path) -> None:
    calls: list[object] = []
    host = FunctionQueryBridge(tmp_path, "expected", lambda request: calls.append(request) or {})
    with host:
        (tmp_path / "request-other-1.json").write_text(json.dumps({"operation": "fetchPage"}), encoding="utf-8")
        threading.Event().wait(0.02)

    assert calls == []
