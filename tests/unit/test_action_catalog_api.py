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


def _plan(*, is_dry_run: bool) -> dict[str, object]:
    return {
        "actionApiName": "ExpediteOrder",
        "ontologyVersionId": "ont-v3",
        "definitionFingerprint": "sha256:def",
        "functionVersion": None,
        "target": {"objectType": "Order", "objectId": "O-1"},
        "parameters": {"mode": "urgent"},
        "editManifest": {},
        "diffs": [],
        "effectManifest": [],
        "risk": {"effectiveLevel": "low"},
        "authorization": {"decision": "allow"},
        "approval": {"requiredForAgent": False},
        "executionMode": "sync",
        "isDryRun": is_dry_run,
        "requestId": "req-plan",
        "createdAt": "2026-08-03T00:00:00Z",
        "planHash": "sha256:plan",
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

        def plan(self, action_api_name, **kwargs):
            calls.append(("plan", (action_api_name, kwargs["object_id"])))
            return _plan(is_dry_run=False)

        def dry_run(self, action_api_name, **kwargs):
            calls.append(("dry_run", (action_api_name, kwargs["object_id"])))
            return _plan(is_dry_run=True)

    class FakeFoundry:
        actions = FakeActions()

    monkeypatch.setattr(api_runtime, "foundry", FakeFoundry())
    client = TestClient(app)

    listed = client.get("/api/actions?cursor=prior-page&limit=25")
    fetched = client.get("/api/actions/ExpediteOrder")
    schema = client.get("/api/actions/ExpediteOrder/schema")
    payload = {
        "target": {"objectType": "Order", "objectId": "O-1"},
        "expectedObjectVersion": 4,
        "params": {"mode": "urgent"},
    }
    plan = client.post("/api/actions/ExpediteOrder/plan", json=payload)
    dry_run = client.post("/api/actions/ExpediteOrder/dry-run", json=payload)

    assert (
        listed.status_code
        == fetched.status_code
        == schema.status_code
        == plan.status_code
        == dry_run.status_code
        == 200
    )
    assert listed.json()["nextCursor"] == "next-page"
    assert fetched.json()["contractVersion"] == 3
    assert schema.json()["type"] == "object"
    assert plan.json()["isDryRun"] is False
    assert dry_run.json()["isDryRun"] is True
    assert calls == [
        ("list", ("prior-page", 25)),
        ("get", "ExpediteOrder"),
        ("schema", "ExpediteOrder"),
        ("plan", ("ExpediteOrder", "O-1")),
        ("dry_run", ("ExpediteOrder", "O-1")),
    ]
    assert listed.headers["X-Request-ID"]
