from __future__ import annotations

from pathlib import Path

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


def test_pipeline_builder_graph_preview_review_deploy_and_run(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,amount\nO-1,10\nO-2,20\n", encoding="utf-8")
    foundry.datasets.ensure("raw.pipeline_orders", ctx=ctx)
    foundry.datasets.upload_csv("raw.pipeline_orders", csv_path, ctx=ctx)

    branch = foundry.pipelines.create_branch(
        pipeline_id="orders_readiness",
        name="dataset-output-v1",
        idempotency_key="pipeline-branch-orders-readiness",
        ctx=ctx,
    )
    updated = foundry.pipelines.update_graph(
        str(branch["id"]),
        graph=_orders_pipeline_graph(),
        expected_fingerprint=str(branch["graphFingerprint"]),
        ctx=ctx,
    )
    validation = foundry.pipelines.validate(str(branch["id"]), ctx=ctx)
    preview = foundry.pipelines.preview_node(str(branch["id"]), "clean_sql", options={"limit": 25}, ctx=ctx)
    test_result = foundry.pipelines.run_tests(str(branch["id"]), ctx=ctx)
    proposal = foundry.pipelines.propose(
        str(branch["id"]),
        title="Deploy orders readiness",
        idempotency_key="pipeline-proposal-orders-readiness",
        ctx=ctx,
    )
    approved = foundry.pipelines.approve(str(proposal["id"]), ctx=ctx)
    version = foundry.pipelines.execute(str(proposal["id"]), ctx=ctx)
    deployed = foundry.pipelines.deploy("orders_readiness", str(version["id"]), ctx=ctx)
    run = foundry.pipelines.run("orders_readiness", version_id=str(version["id"]), ctx=ctx)

    rows = foundry.datasets.preview("clean.orders_readiness", ctx=ctx)
    timeline = foundry.pipelines.timeline(str(run["id"]), ctx=ctx)

    assert updated["graphFingerprint"] != branch["graphFingerprint"]
    assert validation["valid"] is True
    assert preview["noCommit"] is True
    assert test_result["status"] == "passed"
    assert approved["status"] == "approved"
    assert version["versionNumber"] == 1
    assert deployed["version"]["deployedAt"] is not None
    assert run["status"] == "succeeded"
    assert [row["order_id"] for row in rows] == ["O-1", "O-2"]
    assert any(item["event"] == "pipeline.run.succeeded" for item in timeline["timeline"])


def _orders_pipeline_graph() -> dict[str, object]:
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
                    "schema": [_column("order_id"), _column("amount", "int")],
                },
            },
            {
                "id": "out",
                "type": "output_dataset",
                "config": {"outputDatasetRef": "clean.orders_readiness"},
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
        "outputContract": {"columns": [_column("order_id"), _column("amount", "int")]},
        "tests": [
            {
                "name": "schema contract",
                "expected": {"columns": [_column("order_id"), _column("amount", "int")]},
            }
        ],
        "schedule": {"kind": "manual"},
    }


def _column(name: str, column_type: str = "string") -> dict[str, object]:
    return {"name": name, "type": column_type, "nullable": False}
