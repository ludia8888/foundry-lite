from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.pipeline_payloads import run_record
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed
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
    replayed_proposal = foundry.pipelines.propose(
        str(branch["id"]),
        title="Deploy orders readiness",
        idempotency_key="pipeline-proposal-orders-readiness-replay",
        ctx=ctx,
    )
    assigned = foundry.pipelines.assign(str(proposal["id"]), assignee_user_id="reviewer-a", ctx=ctx)
    approved = foundry.pipelines.approve(str(proposal["id"]), ctx=ctx)
    version = foundry.pipelines.execute(str(proposal["id"]), ctx=ctx)
    deployed = foundry.pipelines.deploy("orders_readiness", str(version["id"]), options={"mode": "manual"}, ctx=ctx)
    run = foundry.pipelines.run("orders_readiness", ctx=ctx)
    schedule = foundry.pipelines.upsert_schedule(
        "orders_readiness",
        version_id=str(version["id"]),
        schedule={"kind": "interval", "everyMinutes": 15},
        ctx=ctx,
    )
    loaded_schedule = foundry.pipelines.get_schedule("orders_readiness", ctx=ctx)
    due = foundry.pipelines.preview_due(max_runs=0, ctx=ctx)
    ticked = foundry.pipelines.tick(max_runs=1, ctx=ctx)

    rows = foundry.datasets.preview("clean.orders_readiness", ctx=ctx)
    branch_detail = foundry.pipelines.get_branch(str(branch["id"]), ctx=ctx)
    merged_branches = foundry.pipelines.list_branches(status="merged", limit=10, ctx=ctx)
    versions = foundry.pipelines.list_versions("orders_readiness", ctx=ctx)
    version_detail = foundry.pipelines.get_version(str(version["id"]), ctx=ctx)
    proposal_detail = foundry.pipelines.get_proposal(str(proposal["id"]), ctx=ctx)
    executed_proposals = foundry.pipelines.list_proposals(status="executed", ctx=ctx)
    run_detail = foundry.pipelines.get_run(str(run["id"]), ctx=ctx)
    timeline = foundry.pipelines.timeline(str(run["id"]), ctx=ctx)
    deleted_schedule = foundry.pipelines.delete_schedule("orders_readiness", ctx=ctx)
    with foundry.engine.begin() as transaction:
        cancellable_run = foundry.pipeline_repository.insert_run(
            transaction=transaction,
            record=run_record(ctx, pipeline_id="orders_readiness", version_id=str(version["id"]), now=_now()),
        )
    cancelled = foundry.pipelines.cancel(str(cancellable_run["id"]), ctx=ctx)

    assert updated["graphFingerprint"] != branch["graphFingerprint"]
    assert validation["valid"] is True
    assert preview["noCommit"] is True
    assert test_result["status"] == "passed"
    assert replayed_proposal["id"] == proposal["id"]
    assert assigned["assignedTo"] == "reviewer-a"
    assert approved["status"] == "approved"
    assert version["versionNumber"] == 1
    assert deployed["version"]["deployedAt"] is not None
    assert deployed["options"] == {"mode": "manual"}
    assert run["status"] == "succeeded"
    assert schedule["enabled"] is True
    assert loaded_schedule is not None
    assert loaded_schedule["id"] == schedule["id"]
    assert due["maxRuns"] == 1
    assert due["items"][0]["pipelineId"] == "orders_readiness"
    assert ticked["started"][0]["status"] == "succeeded"
    assert [row["order_id"] for row in rows] == ["O-1", "O-2"]
    assert branch_detail["status"] == "merged"
    assert merged_branches["items"][0]["id"] == branch["id"]
    assert versions["items"][0]["id"] == version["id"]
    assert version_detail["pipelineId"] == "orders_readiness"
    assert proposal_detail["status"] == "executed"
    assert executed_proposals["items"][0]["id"] == proposal["id"]
    assert run_detail["outputDatasetRef"] == "clean.orders_readiness"
    assert any(item["event"] == "pipeline.run.succeeded" for item in timeline["timeline"])
    assert deleted_schedule == {"deleted": True}
    assert cancelled["status"] == "cancelled"
    assert cancelled["timeline"][-1]["event"] == "pipeline.run.cancelled"

    with pytest.raises(ConflictDetected, match="pipeline run is already terminal"):
        foundry.pipelines.cancel(str(run["id"]), ctx=ctx)


def test_pipeline_builder_output_node_dataset_ref_alias_deploys(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,amount\nO-1,10\n", encoding="utf-8")
    foundry.datasets.ensure("raw.pipeline_orders", ctx=ctx)
    foundry.datasets.upload_csv("raw.pipeline_orders", csv_path, ctx=ctx)

    branch = foundry.pipelines.create_branch(
        pipeline_id="orders_dataset_ref_alias",
        name="dataset-ref-output",
        idempotency_key="pipeline-branch-output-dataset-ref-alias",
        ctx=ctx,
    )
    graph = _orders_pipeline_graph(output_ref="clean.orders_dataset_ref_alias")
    output_node = graph["nodes"][2]["config"]
    output_node["datasetRef"] = output_node.pop("outputDatasetRef")
    foundry.pipelines.update_graph(
        str(branch["id"]),
        graph=graph,
        expected_fingerprint=str(branch["graphFingerprint"]),
        ctx=ctx,
    )
    proposal = foundry.pipelines.propose(
        str(branch["id"]),
        title="Deploy datasetRef output alias",
        idempotency_key="pipeline-proposal-output-dataset-ref-alias",
        ctx=ctx,
    )
    foundry.pipelines.approve(str(proposal["id"]), ctx=ctx)
    version = foundry.pipelines.execute(str(proposal["id"]), ctx=ctx)
    deployed = foundry.pipelines.deploy("orders_dataset_ref_alias", str(version["id"]), ctx=ctx)
    run = foundry.pipelines.run("orders_dataset_ref_alias", ctx=ctx)

    assert deployed["version"]["deployedAt"] is not None
    assert run["status"] == "succeeded"
    assert run["outputDatasetRef"] == "clean.orders_dataset_ref_alias"


def test_pipeline_builder_branch_rebase_abandon_reject_and_withdraw(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,amount\nO-1,10\n", encoding="utf-8")
    foundry.datasets.ensure("raw.pipeline_orders", ctx=ctx)
    foundry.datasets.upload_csv("raw.pipeline_orders", csv_path, ctx=ctx)

    seed_branch = foundry.pipelines.create_branch(
        pipeline_id="orders_rebase",
        name="seed",
        idempotency_key="pipeline-branch-orders-rebase-seed",
        ctx=ctx,
    )
    foundry.pipelines.update_graph(
        str(seed_branch["id"]),
        graph=_orders_pipeline_graph(output_ref="clean.orders_rebase_seed"),
        expected_fingerprint=str(seed_branch["graphFingerprint"]),
        ctx=ctx,
    )
    seed_proposal = foundry.pipelines.propose(
        str(seed_branch["id"]),
        title="Seed version",
        idempotency_key="pipeline-proposal-orders-rebase-seed",
        ctx=ctx,
    )
    foundry.pipelines.approve(str(seed_proposal["id"]), ctx=ctx)
    seed_version = foundry.pipelines.execute(str(seed_proposal["id"]), ctx=ctx)
    with pytest.raises(ValidationFailed, match="different pipeline"):
        foundry.pipelines.deploy("wrong_pipeline", str(seed_version["id"]), ctx=ctx)
    with pytest.raises(ConflictDetected, match="pipeline version is not deployed"):
        foundry.pipelines.run("orders_rebase", version_id=str(seed_version["id"]), ctx=ctx)
    with pytest.raises(NotFound, match="deployed pipeline version not found"):
        foundry.pipelines.run("missing_pipeline", ctx=ctx)
    with pytest.raises(NotFound, match="pipeline version not found"):
        foundry.pipelines.deploy("orders_rebase", "missing-version", ctx=ctx)
    with pytest.raises(NotFound, match="pipeline run not found"):
        foundry.pipelines.get_run("missing-run", ctx=ctx)
    with pytest.raises(NotFound, match="pipeline run not found"):
        foundry.pipelines.cancel("missing-run", ctx=ctx)

    branch = foundry.pipelines.create_branch(
        pipeline_id="orders_rebase",
        name="candidate",
        idempotency_key="pipeline-branch-orders-rebase-candidate",
        ctx=ctx,
    )
    duplicate = foundry.pipelines.create_branch(
        pipeline_id="orders_rebase",
        name="candidate",
        idempotency_key="pipeline-branch-orders-rebase-candidate-replay",
        ctx=ctx,
    )
    cast_graph = _orders_pipeline_graph(
        output_ref="clean.orders_rebase_candidate",
        sql_schema=[_column("order_id"), _column("amount", "string"), _column("created_at")],
    )
    updated = foundry.pipelines.update_graph(
        str(branch["id"]),
        graph=cast_graph,
        expected_fingerprint=str(branch["graphFingerprint"]),
        ctx=ctx,
    )
    with pytest.raises(ConflictDetected, match="pipeline graph fingerprint mismatch"):
        foundry.pipelines.update_graph(
            str(branch["id"]),
            graph=cast_graph,
            expected_fingerprint=str(branch["graphFingerprint"]),
            ctx=ctx,
        )
    diff = foundry.pipelines.diff(str(branch["id"]), ctx=ctx)
    with pytest.raises(ConflictDetected, match="pipeline graph fingerprint mismatch"):
        foundry.pipelines.rebase(
            str(branch["id"]),
            expected_fingerprint=str(branch["graphFingerprint"]),
            ctx=ctx,
        )
    rebased = foundry.pipelines.rebase(
        str(branch["id"]),
        expected_fingerprint=str(updated["graphFingerprint"]),
        ctx=ctx,
    )
    suggestions = foundry.pipelines.suggest_casts(str(branch["id"]), "clean_sql", ctx=ctx)
    stats = foundry.pipelines.node_stats(str(branch["id"]), "clean_sql", options={"sample": True}, ctx=ctx)
    abandoned = foundry.pipelines.abandon(str(branch["id"]), ctx=ctx)
    abandoned_again = foundry.pipelines.abandon(str(branch["id"]), ctx=ctx)
    with pytest.raises(NotFound, match="pipeline branch not found"):
        foundry.pipelines.get_branch("missing-branch", ctx=ctx)

    reject_branch = foundry.pipelines.create_branch(
        pipeline_id="orders_rebase",
        name="reject-me",
        idempotency_key="pipeline-branch-orders-rebase-reject",
        ctx=ctx,
    )
    reject_updated = foundry.pipelines.update_graph(
        str(reject_branch["id"]),
        graph=_orders_pipeline_graph(output_ref="clean.orders_rebase_reject"),
        expected_fingerprint=str(reject_branch["graphFingerprint"]),
        ctx=ctx,
    )
    reject_proposal = foundry.pipelines.propose(
        str(reject_updated["id"]),
        title="Reject candidate",
        idempotency_key="pipeline-proposal-orders-rebase-reject",
        ctx=ctx,
    )
    rejected = foundry.pipelines.decide(str(reject_proposal["id"]), decision="reject", comment="needs work", ctx=ctx)
    with pytest.raises(ConflictDetected, match="pipeline proposal is not in the required state"):
        foundry.pipelines.assign(str(reject_proposal["id"]), assignee_user_id="reviewer-b", ctx=ctx)
    with pytest.raises(ConflictDetected, match="pipeline proposal is not in the required state"):
        foundry.pipelines.decide(str(reject_proposal["id"]), decision="approve", ctx=ctx)
    with pytest.raises(ConflictDetected, match="pipeline proposal is not in the required state"):
        foundry.pipelines.withdraw(str(reject_proposal["id"]), ctx=ctx)
    with pytest.raises(ValidationFailed, match="unsupported pipeline proposal decision"):
        foundry.pipelines.decide(str(reject_proposal["id"]), decision="punt", ctx=ctx)
    with pytest.raises(NotFound, match="pipeline proposal not found"):
        foundry.pipelines.get_proposal("missing-proposal", ctx=ctx)

    withdraw_branch = foundry.pipelines.create_branch(
        pipeline_id="orders_rebase",
        name="withdraw-me",
        idempotency_key="pipeline-branch-orders-rebase-withdraw",
        ctx=ctx,
    )
    withdraw_updated = foundry.pipelines.update_graph(
        str(withdraw_branch["id"]),
        graph=_orders_pipeline_graph(output_ref="clean.orders_rebase_withdraw"),
        expected_fingerprint=str(withdraw_branch["graphFingerprint"]),
        ctx=ctx,
    )
    withdraw_proposal = foundry.pipelines.propose(
        str(withdraw_updated["id"]),
        title="Withdraw candidate",
        idempotency_key="pipeline-proposal-orders-rebase-withdraw",
        ctx=ctx,
    )
    withdrawn = foundry.pipelines.withdraw(str(withdraw_proposal["id"]), ctx=ctx)

    invalid_branch = foundry.pipelines.create_branch(
        pipeline_id="orders_rebase",
        name="invalid",
        idempotency_key="pipeline-branch-orders-rebase-invalid",
        ctx=ctx,
    )
    invalid_graph = _orders_pipeline_graph(output_ref="clean.orders_rebase_invalid")
    invalid_graph["nodes"] = []
    foundry.pipelines.update_graph(
        str(invalid_branch["id"]),
        graph=invalid_graph,
        expected_fingerprint=str(invalid_branch["graphFingerprint"]),
        ctx=ctx,
    )
    with pytest.raises(ValidationFailed, match="pipeline graph is invalid"):
        foundry.pipelines.propose(
            str(invalid_branch["id"]),
            title="Invalid candidate",
            idempotency_key="pipeline-proposal-orders-rebase-invalid",
            ctx=ctx,
        )

    assert seed_version["versionNumber"] == 1
    assert duplicate["id"] == branch["id"]
    assert diff["baseStale"] is True
    assert rebased["baseVersionId"] == seed_version["id"]
    assert {"column": "created_at", "from": "string", "to": "timestamp", "confidence": "medium"} in suggestions[
        "suggestions"
    ]
    assert stats["columnCount"] == 3
    assert stats["options"] == {"sample": True}
    assert abandoned["status"] == "abandoned"
    assert abandoned_again["status"] == "abandoned"
    assert rejected["status"] == "rejected"
    assert rejected["decisionComment"] == "needs work"
    assert withdrawn["status"] == "withdrawn"


def _orders_pipeline_graph(
    *,
    output_ref: str = "clean.orders_readiness",
    sql_schema: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    output_schema = sql_schema or [_column("order_id"), _column("amount", "int")]
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
        "tests": [
            {
                "name": "schema contract",
                "expected": {"columns": output_schema},
            }
        ],
        "schedule": {"kind": "manual"},
    }


def _column(name: str, column_type: str = "string") -> dict[str, object]:
    return {"name": name, "type": column_type, "nullable": False}
