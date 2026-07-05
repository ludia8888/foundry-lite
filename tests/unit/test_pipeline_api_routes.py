from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite_api import main as api_main
from foundry_lite_api import runtime as api_runtime


def test_pipeline_api_routes_cover_builder_review_deploy_run_and_schedule(
    tmp_path: Path,
    monkeypatch,
) -> None:
    foundry = FoundryLite(
        dependencies=create_local_core_dependencies(
            db_url=f"sqlite:///{tmp_path / 'api.db'}",
            storage_root=tmp_path / "flite",
        )
    )
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,amount\nO-1,10\nO-2,20\n", encoding="utf-8")
    foundry.datasets.ensure("raw.pipeline_orders", ctx=ctx)
    foundry.datasets.upload_csv("raw.pipeline_orders", csv_path, ctx=ctx)

    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)
    headers = {
        "X-Tenant-ID": ctx.tenant_id,
        "X-User-ID": ctx.actor_user_id,
        "X-Roles": ",".join(ctx.roles),
    }

    branch = _ok(
        client.post(
            "/api/pipelines/branches",
            json={"pipelineId": "api_orders_readiness", "name": "api-v1"},
            headers={**headers, "Idempotency-Key": "api-pipeline-branch"},
        )
    )
    updated = _ok(
        client.post(
            f"/api/pipelines/branches/{branch['id']}/graph",
            json={
                "graph": _orders_pipeline_graph(output_ref="clean.api_orders_readiness"),
                "expectedFingerprint": branch["graphFingerprint"],
            },
            headers=headers,
        )
    )
    validation = _ok(client.get(f"/api/pipelines/branches/{branch['id']}/validate", headers=headers))
    casts = _ok(client.get(f"/api/pipelines/branches/{branch['id']}/nodes/clean_sql/casts", headers=headers))
    preview = _ok(
        client.post(
            f"/api/pipelines/branches/{branch['id']}/nodes/clean_sql/preview",
            json={"limit": 10},
            headers=headers,
        )
    )
    stats = _ok(
        client.post(
            f"/api/pipelines/branches/{branch['id']}/nodes/clean_sql/stats",
            json={"limit": 10},
            headers=headers,
        )
    )
    tests = _ok(client.post(f"/api/pipelines/branches/{branch['id']}/tests/run", headers=headers))
    diff = _ok(client.get(f"/api/pipelines/branches/{branch['id']}/diff", headers=headers))
    proposal = _ok(
        client.post(
            f"/api/pipelines/branches/{branch['id']}/propose",
            json={"title": "Deploy API orders readiness", "description": "HTTP route coverage"},
            headers={**headers, "Idempotency-Key": "api-pipeline-proposal"},
        )
    )
    proposals = _ok(client.get("/api/pipelines/proposals?limit=10", headers=headers))
    proposal_detail = _ok(client.get(f"/api/pipelines/proposals/{proposal['id']}", headers=headers))
    assigned = _ok(
        client.post(
            f"/api/pipelines/proposals/{proposal['id']}/assign",
            json={"assigneeUserId": "reviewer-api"},
            headers=headers,
        )
    )
    approved = _ok(
        client.post(
            f"/api/pipelines/proposals/{proposal['id']}/decision",
            json={"decision": "approve", "comment": "ship it"},
            headers=headers,
        )
    )
    version = _ok(client.post(f"/api/pipelines/proposals/{proposal['id']}/execute", headers=headers))
    versions = _ok(client.get("/api/pipelines/api_orders_readiness/versions?limit=10", headers=headers))
    version_detail = _ok(client.get(f"/api/pipelines/versions/{version['id']}", headers=headers))
    deployed = _ok(
        client.post(
            f"/api/pipelines/api_orders_readiness/deploy/{version['id']}",
            json={"options": {"mode": "api-test"}},
            headers=headers,
        )
    )
    run = _ok(client.post("/api/pipelines/api_orders_readiness/runs", json={}, headers=headers))
    run_detail = _ok(client.get(f"/api/pipelines/runs/{run['id']}", headers=headers))
    timeline = _ok(client.get(f"/api/pipelines/runs/{run['id']}/timeline", headers=headers))
    schedule = _ok(
        client.put(
            "/api/pipelines/api_orders_readiness/schedule",
            json={
                "versionId": version["id"],
                "schedule": {"kind": "interval", "everyMinutes": 15},
                "enabled": True,
            },
            headers=headers,
        )
    )
    schedule_detail = _ok(client.get("/api/pipelines/api_orders_readiness/schedule", headers=headers))
    due = _ok(client.get("/api/pipelines/scheduler/due?maxRuns=1", headers=headers))
    tick = _ok(client.post("/api/pipelines/scheduler/tick?maxRuns=1", headers=headers))
    deleted = _ok(client.delete("/api/pipelines/api_orders_readiness/schedule", headers=headers))
    branch_detail = _ok(client.get(f"/api/pipelines/branches/{branch['id']}", headers=headers))
    branch_list = _ok(client.get("/api/pipelines/branches?status=merged&limit=10", headers=headers))

    assert updated["graphFingerprint"] != branch["graphFingerprint"]
    assert validation["valid"] is True
    assert casts["suggestions"][0]["column"] == "order_id"
    assert preview["noCommit"] is True
    assert preview["schema"][0]["name"] == "order_id"
    assert stats["rowCount"] is None
    assert tests["status"] == "passed"
    assert diff["baseStale"] is False
    assert proposals["items"][0]["id"] == proposal["id"]
    assert proposal_detail["status"] == "submitted"
    assert assigned["assignedTo"] == "reviewer-api"
    assert approved["status"] == "approved"
    assert version["versionNumber"] == 1
    assert versions["items"][0]["id"] == version["id"]
    assert version_detail["pipelineId"] == "api_orders_readiness"
    assert deployed["options"] == {"mode": "api-test"}
    assert run["status"] == "succeeded"
    assert run_detail["outputDatasetRef"] == "clean.api_orders_readiness"
    assert any(item["event"] == "pipeline.run.succeeded" for item in timeline["timeline"])
    assert schedule["enabled"] is True
    assert schedule_detail["id"] == schedule["id"]
    assert due["items"][0]["pipelineId"] == "api_orders_readiness"
    assert tick["started"][0]["status"] == "succeeded"
    assert deleted == {"deleted": True}
    assert branch_detail["status"] == "merged"
    assert branch_list["items"][0]["id"] == branch["id"]

    terminal_cancel = client.post(f"/api/pipelines/runs/{run['id']}/cancel", headers=headers)
    assert terminal_cancel.status_code == 409
    missing_branch = client.get("/api/pipelines/branches/missing-branch", headers=headers)
    assert missing_branch.status_code == 404


def test_pipeline_api_routes_cover_rebase_abandon_and_withdraw(
    tmp_path: Path,
    monkeypatch,
) -> None:
    foundry = FoundryLite(
        dependencies=create_local_core_dependencies(
            db_url=f"sqlite:///{tmp_path / 'api.db'}",
            storage_root=tmp_path / "flite",
        )
    )
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,amount\nO-1,10\n", encoding="utf-8")
    foundry.datasets.ensure("raw.pipeline_orders", ctx=ctx)
    foundry.datasets.upload_csv("raw.pipeline_orders", csv_path, ctx=ctx)

    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)
    headers = {
        "X-Tenant-ID": ctx.tenant_id,
        "X-User-ID": ctx.actor_user_id,
        "X-Roles": ",".join(ctx.roles),
    }

    seed = _create_graph_branch(
        client,
        headers,
        pipeline_id="api_rebase_orders",
        name="seed",
        branch_key="api-rebase-seed",
        output_ref="clean.api_rebase_seed",
    )
    seed_proposal = _ok(
        client.post(
            f"/api/pipelines/branches/{seed['id']}/propose",
            json={"title": "Seed"},
            headers={**headers, "Idempotency-Key": "api-rebase-seed-proposal"},
        )
    )
    _ok(
        client.post(
            f"/api/pipelines/proposals/{seed_proposal['id']}/decision",
            json={"decision": "approve"},
            headers=headers,
        )
    )
    seed_version = _ok(client.post(f"/api/pipelines/proposals/{seed_proposal['id']}/execute", headers=headers))

    branch = _create_graph_branch(
        client,
        headers,
        pipeline_id="api_rebase_orders",
        name="candidate",
        branch_key="api-rebase-candidate",
        output_ref="clean.api_rebase_candidate",
    )
    rebased = _ok(
        client.post(
            f"/api/pipelines/branches/{branch['id']}/rebase",
            json={"expectedFingerprint": branch["graphFingerprint"]},
            headers=headers,
        )
    )
    abandoned = _ok(client.post(f"/api/pipelines/branches/{branch['id']}/abandon", headers=headers))

    withdraw_branch = _create_graph_branch(
        client,
        headers,
        pipeline_id="api_rebase_orders",
        name="withdraw",
        branch_key="api-rebase-withdraw",
        output_ref="clean.api_rebase_withdraw",
    )
    withdraw_proposal = _ok(
        client.post(
            f"/api/pipelines/branches/{withdraw_branch['id']}/propose",
            json={"title": "Withdraw"},
            headers={**headers, "Idempotency-Key": "api-rebase-withdraw-proposal"},
        )
    )
    withdrawn = _ok(client.post(f"/api/pipelines/proposals/{withdraw_proposal['id']}/withdraw", headers=headers))

    assert seed_version["versionNumber"] == 1
    assert rebased["baseVersionId"] == seed_version["id"]
    assert abandoned["status"] == "abandoned"
    assert withdrawn["status"] == "withdrawn"


def test_pipeline_api_routes_convert_domain_errors(monkeypatch) -> None:
    monkeypatch.setattr(api_runtime, "foundry", SimpleNamespace(pipelines=_FailingPipelines()))
    client = TestClient(api_main.app)
    headers = {"X-User-ID": "user-demo-admin", "X-Roles": "admin,data_engineer,ops_manager"}
    graph_update = {"graph": _orders_pipeline_graph(output_ref="clean.error"), "expectedFingerprint": "fp"}
    preview = {"limit": 1}

    requests = [
        lambda: client.post(
            "/api/pipelines/branches",
            json={"pipelineId": "p", "name": "n"},
            headers={**headers, "Idempotency-Key": "k"},
        ),
        lambda: client.get("/api/pipelines/branches", headers=headers),
        lambda: client.get("/api/pipelines/branches/b", headers=headers),
        lambda: client.post("/api/pipelines/branches/b/graph", json=graph_update, headers=headers),
        lambda: client.get("/api/pipelines/branches/b/diff", headers=headers),
        lambda: client.post(
            "/api/pipelines/branches/b/rebase",
            json={"expectedFingerprint": "fp"},
            headers=headers,
        ),
        lambda: client.post(
            "/api/pipelines/branches/b/propose",
            json={"title": "t"},
            headers={**headers, "Idempotency-Key": "proposal-k"},
        ),
        lambda: client.post("/api/pipelines/branches/b/abandon", headers=headers),
        lambda: client.get("/api/pipelines/branches/b/validate", headers=headers),
        lambda: client.get("/api/pipelines/branches/b/nodes/n/casts", headers=headers),
        lambda: client.post("/api/pipelines/branches/b/nodes/n/preview", json=preview, headers=headers),
        lambda: client.post("/api/pipelines/branches/b/nodes/n/stats", json=preview, headers=headers),
        lambda: client.post("/api/pipelines/branches/b/tests/run", headers=headers),
        lambda: client.get("/api/pipelines/proposals", headers=headers),
        lambda: client.get("/api/pipelines/proposals/p", headers=headers),
        lambda: client.post("/api/pipelines/proposals/p/assign", json={"assigneeUserId": "u"}, headers=headers),
        lambda: client.post("/api/pipelines/proposals/p/decision", json={"decision": "approve"}, headers=headers),
        lambda: client.post("/api/pipelines/proposals/p/execute", headers=headers),
        lambda: client.post("/api/pipelines/proposals/p/withdraw", headers=headers),
        lambda: client.get("/api/pipelines/versions/v", headers=headers),
        lambda: client.get("/api/pipelines/runs/r", headers=headers),
        lambda: client.get("/api/pipelines/runs/r/timeline", headers=headers),
        lambda: client.post("/api/pipelines/runs/r/cancel", headers=headers),
        lambda: client.get("/api/pipelines/scheduler/due?maxRuns=1", headers=headers),
        lambda: client.post("/api/pipelines/scheduler/tick?maxRuns=1", headers=headers),
        lambda: client.get("/api/pipelines/p/versions", headers=headers),
        lambda: client.post("/api/pipelines/p/deploy/v", json={"options": {}}, headers=headers),
        lambda: client.post("/api/pipelines/p/runs", json={}, headers=headers),
        lambda: client.put(
            "/api/pipelines/p/schedule",
            json={"versionId": "v", "schedule": {"kind": "manual"}, "enabled": True},
            headers=headers,
        ),
        lambda: client.get("/api/pipelines/p/schedule", headers=headers),
        lambda: client.delete("/api/pipelines/p/schedule", headers=headers),
    ]

    for request in requests:
        response = request()
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["code"] == "VALIDATION_FAILED"


class _FailingPipelines:
    def __getattr__(self, _name: str):
        def _raise(*_args, **_kwargs):
            raise ValidationFailed("pipeline route failure")

        return _raise


def _create_graph_branch(
    client: TestClient,
    headers: dict[str, str],
    *,
    pipeline_id: str,
    name: str,
    branch_key: str,
    output_ref: str,
) -> dict[str, Any]:
    branch = _ok(
        client.post(
            "/api/pipelines/branches",
            json={"pipelineId": pipeline_id, "name": name},
            headers={**headers, "Idempotency-Key": branch_key},
        )
    )
    return _ok(
        client.post(
            f"/api/pipelines/branches/{branch['id']}/graph",
            json={
                "graph": _orders_pipeline_graph(output_ref=output_ref),
                "expectedFingerprint": branch["graphFingerprint"],
            },
            headers=headers,
        )
    )


def _ok(response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    return response.json()


def _orders_pipeline_graph(*, output_ref: str) -> dict[str, object]:
    output_schema = [_column("order_id"), _column("amount", "int")]
    return {
        "nodes": [
            {
                "id": "raw_orders",
                "type": "dataset",
                "config": {
                    "datasetRef": "raw.pipeline_orders",
                    "schema": [_column("order_id"), _column("amount", "int")],
                },
            },
            {
                "id": "clean_sql",
                "type": "sql",
                "config": {
                    "sql": "select order_id, amount from {{ input('raw.pipeline_orders') }} order by order_id",
                    "outputDatasetRef": "work.pipeline_orders_clean",
                    "schema": output_schema,
                },
            },
            {
                "id": "out",
                "type": "output_dataset",
                "config": {"outputDatasetRef": output_ref},
            },
        ],
        "edges": [
            {"source": "raw_orders", "target": "clean_sql"},
            {"source": "clean_sql", "target": "out"},
        ],
        "layout": {
            "raw_orders": {"x": 0, "y": 0},
            "clean_sql": {"x": 260, "y": 0},
            "out": {"x": 520, "y": 0},
        },
        "outputContract": {"columns": output_schema},
        "tests": [{"name": "schema contract", "expected": {"columns": output_schema}}],
        "schedule": {"kind": "manual"},
    }


def _column(name: str, column_type: str = "string") -> dict[str, object]:
    return {"name": name, "type": column_type, "nullable": False}
