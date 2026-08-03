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


def test_action_run_routes_preserve_wait_cursor_idempotency_and_status_codes(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class FakeActions:
        def start_run(self, action_api_name, **kwargs):
            calls.append(("start", (action_api_name, kwargs["idempotency_key"], kwargs["wait_seconds"])))
            status = "succeeded" if kwargs["wait_seconds"] else "queued"
            return {"actionRunId": "run-1", "status": status}

        def list_runs(self, **kwargs):
            calls.append(("list_runs", (kwargs["cursor"], kwargs["limit"])))
            return {"items": [{"actionRunId": "run-1", "status": "queued"}], "nextCursor": "next-run"}

        def get_run(self, run_id, **_kwargs):
            calls.append(("get_run", run_id))
            return {"actionRunId": run_id, "status": "running"}

        def cancel(self, run_id, **kwargs):
            calls.append(("cancel", (run_id, kwargs["idempotency_key"], kwargs["reason"])))
            return {"actionRunId": run_id, "status": "cancelling"}

    class FakeFoundry:
        actions = FakeActions()

    monkeypatch.setattr(api_runtime, "foundry", FakeFoundry())
    client = TestClient(app)
    payload = {
        "target": {"objectType": "Order", "objectId": "O-1"},
        "expectedObjectVersion": 4,
        "params": {},
    }

    queued = client.post("/api/actions/ExpediteOrder/runs", json=payload, headers={"Idempotency-Key": "run-key-1"})
    terminal = client.post(
        "/api/actions/ExpediteOrder/runs?waitSeconds=3",
        json=payload,
        headers={"Idempotency-Key": "run-key-2"},
    )
    listed = client.get("/api/actions/runs?cursor=prior-run&limit=20")
    fetched = client.get("/api/actions/runs/run-1")
    cancelled = client.post(
        "/api/actions/runs/run-1/cancel",
        json={"reason": "operator request"},
        headers={"Idempotency-Key": "cancel-key-1"},
    )

    assert queued.status_code == 202
    assert terminal.status_code == 200
    assert listed.json()["nextCursor"] == "next-run"
    assert fetched.json()["status"] == "running"
    assert cancelled.status_code == 202
    assert cancelled.json()["status"] == "cancelling"
    assert calls == [
        ("start", ("ExpediteOrder", "run-key-1", 0)),
        ("start", ("ExpediteOrder", "run-key-2", 3)),
        ("list_runs", ("prior-run", 20)),
        ("get_run", "run-1"),
        ("cancel", ("run-1", "cancel-key-1", "operator request")),
    ]


def test_action_run_sse_resumes_after_last_event_id(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    class FakeActions:
        def events(self, run_id, **kwargs):
            calls.append((run_id, kwargs["after_sequence"]))
            if kwargs["after_sequence"] == 5:
                return {
                    "actionRunId": run_id,
                    "events": [
                        {
                            "id": 6,
                            "event": "action.run.succeeded",
                            "data": {"status": "succeeded"},
                        }
                    ],
                }
            return {"actionRunId": run_id, "events": []}

        def get_run(self, run_id, **_kwargs):
            return {"actionRunId": run_id, "status": "succeeded"}

    class FakeFoundry:
        actions = FakeActions()

    monkeypatch.setattr(api_runtime, "foundry", FakeFoundry())
    response = TestClient(app).get("/api/actions/runs/run-1/events", headers={"Last-Event-ID": "5"})

    assert response.status_code == 200
    assert 'id: 6\nevent: action.run.succeeded\ndata: {"status":"succeeded"}' in response.text
    assert calls == [("run-1", 5), ("run-1", 6)]
