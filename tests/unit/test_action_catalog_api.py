"""HTTP contract for canonical Action discovery endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app


def _item() -> dict[str, object]:
    return {
        "apiName": "ExpediteOrder",
        "displayName": "Expedite order",
        "description": "Expedite one order",
        "target": {"kind": "object", "apiName": "Order"},
        "ontologyVersionId": "ont-v3",
        "contractVersion": 3,
        "contractFingerprint": "sha256:abc",
        "parameterSchema": {"type": "object", "properties": {}},
        "contract": {"contractVersion": 3, "apiName": "ExpediteOrder"},
        "riskLevel": "low",
        "agentExecutionPolicy": "autonomous",
        "enabled": True,
    }


def test_action_catalog_routes_preserve_cursor_and_return_contract(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class FakeActions:
        def list(self, **kwargs):
            calls.append(("list", (kwargs["cursor"], kwargs["limit"])))
            return {"items": [_item()], "nextCursor": "next-page"}

        def get(self, action_api_name, **_kwargs):
            calls.append(("get", action_api_name))
            return _item()

        def schema(self, action_api_name, **_kwargs):
            calls.append(("schema", action_api_name))
            return _item()["parameterSchema"]

    class FakeFoundry:
        actions = FakeActions()

    monkeypatch.setattr(api_runtime, "foundry", FakeFoundry())
    client = TestClient(app)

    listed = client.get("/api/actions?cursor=prior-page&limit=25")
    fetched = client.get("/api/actions/ExpediteOrder")
    schema = client.get("/api/actions/ExpediteOrder/schema")

    assert listed.status_code == fetched.status_code == schema.status_code == 200
    assert listed.json()["nextCursor"] == "next-page"
    assert fetched.json()["contractVersion"] == 3
    assert schema.json()["type"] == "object"
    assert calls == [
        ("list", ("prior-page", 25)),
        ("get", "ExpediteOrder"),
        ("schema", "ExpediteOrder"),
    ]
    assert listed.headers["X-Request-ID"]
