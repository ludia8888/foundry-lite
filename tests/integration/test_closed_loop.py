from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import DatasetStagedFile, TransactionContext
from foundry_lite.application.ports.compute_adapter import SqlTransformPlan, TransformExecutionResult, TransformPlan
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.materialization_service import MaterializationRunPlan, MaterializationService
from foundry_lite.application.services.transform_run_service import TransformRunService
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ConflictDetected, ExternalSystemError, InvariantViolation, ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters import DuckDBComputeAdapter
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from sqlalchemy import select

from tests.conftest import prepare_indexed_demo


@pytest.mark.integration_scenario("transform_clean_dataset")
def test_supply_chain_closed_loop_updates_customer_risk_and_records_replay_state(
    foundry: FoundryLite,
) -> None:
    result = foundry.demo.run()

    assert result["action"]["status"] == "succeeded"
    assert result["customer"]["properties"]["customerId"] == "C-100"
    assert result["customer"]["properties"]["riskScore"] == 0.1
    assert result["customer"]["properties"]["approvedOrderCount"] == 2

    linked = foundry.objects.links("Order", "O-1001", "OrderCustomer")
    assert linked[0]["to"]["objectId"] == "C-100"

    clean_orders = foundry.datasets.inspect("clean.orders")
    clean_order_lineage = foundry.operations.lineage(result["cleanOrdersVersion"], ctx=demo_admin_context())
    assert clean_orders["manifest"]["files"][0]["row_count"] == 3
    assert any(
        edge["from_resource_id"] == result["rawOrdersVersion"]
        and edge["to_resource_id"] == result["cleanOrdersVersion"]
        for edge in clean_order_lineage
    )

    runs = foundry.operations.list_runs(ctx=demo_admin_context())
    assert any(run["status"] == "SUCCESS" for run in runs["transformRuns"])
    assert any(run["status"] == "succeeded" for run in runs["materializationRuns"])
    assert any(event["event_type"] == "action.run.committed" for event in runs["outboxEvents"])
    assert result["customer"]["explain"]["lineage"]


@pytest.mark.integration_scenario("materialization_downstream_transform")
def test_action_materialization_writes_dataset_versions_and_manifest_rows(
    foundry: FoundryLite,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    action = foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key="materialization-proof",
        ctx=ctx,
    )

    action_log = foundry.materialization.run("action_log", ctx=ctx)
    order_current = foundry.materialization.run("order_current", ctx=ctx)
    customer_risk = foundry.transforms.run("customer_risk", ctx=ctx)

    action_log_versions = foundry.datasets.list_versions("ops.action_log", ctx=ctx)
    order_current_versions = foundry.datasets.list_versions("ops.order_current", ctx=ctx)
    action_log_manifest = foundry.datasets.inspect("ops.action_log", ctx=ctx)["manifest"]
    order_current_manifest = foundry.datasets.inspect("ops.order_current", ctx=ctx)["manifest"]
    customer_risk_rows = foundry.datasets.preview("clean.customers", ctx=ctx)
    action_detail = foundry.operations.run_detail("action", action["actionRunId"], ctx=ctx)
    order_current_run = _materialization_run_for_version(foundry, ctx, order_current.version_id)
    order_current_detail = foundry.operations.run_detail("materialization", str(order_current_run["id"]), ctx=ctx)
    downstream_lineage = foundry.operations.lineage(order_current.version_id, ctx=ctx)
    order_current_watermark = order_current_run["object_store_watermark"]
    action_relations = {
        (row["target_run_type"], row["relation"], row["metadata"].get("eventType"))
        for row in action_detail["runRelations"]
    }
    materialization_relations = {
        (row["target_run_type"], row["relation"], row["resource_id"]) for row in order_current_detail["runRelations"]
    }

    assert action_log.version_id == action_log_versions[0]["id"]
    assert order_current.version_id == order_current_versions[0]["id"]
    assert action["status"] == "succeeded"
    assert ("action_writeback", "writeback_attempt", None) in action_relations
    assert ("outbox", "emitted", "action.run.committed") in action_relations
    assert ("outbox", "emitted", order_current.version_id) in materialization_relations
    assert action_log.row_count == action_log_manifest["files"][0]["row_count"] == 1
    assert order_current.row_count == order_current_manifest["files"][0]["row_count"] == 3
    assert customer_risk.row_count == 2
    assert any(row["customer_id"] == "C-100" and row["risk_score"] == 0.1 for row in customer_risk_rows)
    assert any(
        edge["from_resource_id"] == order_current.version_id and edge["to_resource_id"] == customer_risk.version_id
        for edge in downstream_lineage
    )
    assert isinstance(order_current_watermark["object_change_sequence_lte"], int)
    assert isinstance(order_current_watermark["active_index_version"], str)
    assert any(
        edge["from_resource_type"] == "materialization"
        and edge["from_resource_id"] == order_current_run["materialization_id"]
        and edge["to_resource_id"] == order_current.version_id
        and edge["relation"] == "materializes_to"
        and edge["created_by_run_id"] == order_current_run["id"]
        for edge in downstream_lineage
    )


def test_transform_input_latest_is_pinned_to_version_id(tmp_path: Path) -> None:
    compute = _BeforeExecuteTransformAdapter()
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "transform-pinning")
    foundry = FoundryLite(dependencies=replace(dependencies, compute_adapter=compute))
    ctx = RequestContext(roles=("admin", "data_engineer"))
    foundry.datasets.ensure("raw.pin_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.pin_orders", ctx=ctx, primary_key=["order_id"])
    first_input = foundry.datasets.upload_csv("raw.pin_orders", _csv(tmp_path, "orders_v1.csv", "O-1", 100), ctx=ctx)
    sql_path = tmp_path / "pin_orders.sql"
    sql_path.write_text("select order_id, amount from {{ input('raw.pin_orders') }}", encoding="utf-8")
    foundry.transforms.register(
        "pin_orders",
        entrypoint=sql_path,
        inputs={"orders": "raw.pin_orders"},
        output_dataset_ref="clean.pin_orders",
        ctx=ctx,
    )

    def commit_new_latest() -> None:
        foundry.datasets.upload_csv("raw.pin_orders", _csv(tmp_path, "orders_v2.csv", "O-1", 999), ctx=ctx)

    compute.before_execute_transform = commit_new_latest
    result = foundry.transforms.run("pin_orders", ctx=ctx)

    output_rows = foundry.datasets.preview("clean.pin_orders", ctx=ctx, version=result.version_id)
    latest_input_rows = foundry.datasets.preview("raw.pin_orders", ctx=ctx)
    transform_run = next(
        run
        for run in foundry.operations.list_runs(ctx=ctx)["transformRuns"]
        if run["output_version_id"] == result.version_id
    )
    assert [(row["order_id"], row["amount"]) for row in output_rows] == [("O-1", 100)]
    assert [(row["order_id"], row["amount"]) for row in latest_input_rows] == [("O-1", 999)]
    assert transform_run["input_versions"] == {"raw.pin_orders": first_input.version_id}


@pytest.mark.integration_scenario("materialization_downstream_transform")
def test_downstream_transform_consumes_materialized_version_id_not_latest(tmp_path: Path) -> None:
    compute = _BeforeExecuteTransformAdapter()
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "materialized-transform-pinning")
    foundry = FoundryLite(dependencies=replace(dependencies, compute_adapter=compute))
    ctx = prepare_indexed_demo(foundry)
    first_materialized = foundry.materialization.run("order_current", ctx=ctx)
    foundry.datasets.ensure("clean.pinned_order_current", ctx=ctx, primary_key=["orderId"])
    sql_path = tmp_path / "pinned_order_current.sql"
    sql_path.write_text(
        "select \"orderId\", status from {{ input('ops.order_current') }}",
        encoding="utf-8",
    )
    foundry.transforms.register(
        "pinned_order_current",
        entrypoint=sql_path,
        inputs={"orders": "ops.order_current"},
        output_dataset_ref="clean.pinned_order_current",
        ctx=ctx,
    )
    newer_materialized_version_ids: list[str] = []

    def commit_new_latest_materialization() -> None:
        order = foundry.objects.get("Order", "O-1001", ctx=ctx)
        _approve_order(
            foundry,
            ctx,
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            idempotency_key="latest-materialization-after-transform-plan",
        )
        newer_materialized_version_ids.append(foundry.materialization.run("order_current", ctx=ctx).version_id)

    compute.before_execute_transform = commit_new_latest_materialization
    result = foundry.transforms.run("pinned_order_current", ctx=ctx)

    output_status = _status_by_order(
        foundry.datasets.preview("clean.pinned_order_current", ctx=ctx, version=result.version_id)
    )
    newer_run = _materialization_run_for_version(foundry, ctx, newer_materialized_version_ids[0])
    latest_status = _status_by_order(foundry.materialization.replay_rows(str(newer_run["id"]), ctx=ctx).rows)
    transform_run = next(
        run
        for run in foundry.operations.list_runs(ctx=ctx)["transformRuns"]
        if run["output_version_id"] == result.version_id
    )
    materialization_event = next(
        event
        for event in foundry.operations.list_runs(ctx=ctx)["outboxEvents"]
        if event["event_type"] == "materialization.completed" and event["aggregate_id"] == first_materialized.version_id
    )

    assert newer_materialized_version_ids and newer_materialized_version_ids[0] != first_materialized.version_id
    assert output_status["O-1001"] == "PENDING"
    assert latest_status["O-1001"] == "APPROVED"
    assert transform_run["input_versions"] == {"ops.order_current": first_materialized.version_id}
    assert materialization_event["payload"]["versionId"] == first_materialized.version_id


def test_transform_graph_runs_downstream_transform_and_records_relation(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "transform-graph"))
    ctx = RequestContext(roles=("admin", "data_engineer"))
    foundry.datasets.ensure("raw.graph_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("stage.graph_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("mart.graph_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("raw.graph_orders", _csv(tmp_path, "graph_orders.csv", "O-1", 100), ctx=ctx)
    stage_sql = tmp_path / "graph_stage.sql"
    mart_sql = tmp_path / "graph_mart.sql"
    stage_sql.write_text("select order_id, amount from {{ input('raw.graph_orders') }}", encoding="utf-8")
    mart_sql.write_text(
        "select order_id, amount * 2 as doubled from {{ input('stage.graph_orders') }}",
        encoding="utf-8",
    )
    foundry.transforms.register(
        "graph_stage",
        entrypoint=stage_sql,
        inputs={"orders": "raw.graph_orders"},
        output_dataset_ref="stage.graph_orders",
        ctx=ctx,
    )
    foundry.transforms.register(
        "graph_mart",
        entrypoint=mart_sql,
        inputs={"stage": "stage.graph_orders"},
        output_dataset_ref="mart.graph_orders",
        ctx=ctx,
    )

    graph = foundry.transforms.run_graph("graph_stage", ctx=ctx)

    downstream = graph["downstream"]
    assert isinstance(downstream, list)
    assert downstream[0]["apiName"] == "graph_mart"
    assert foundry.datasets.preview("mart.graph_orders", ctx=ctx)[0]["doubled"] == 200
    detail = foundry.operations.run_detail("transform", str(graph["root"]["transformRunId"]), ctx=ctx)
    assert any(
        row["target_run_id"] == downstream[0]["transformRunId"] and row["relation"] == "triggered_downstream"
        for row in detail["runRelations"]
    )


def test_failed_action_not_included_in_success_action_log_materialization(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    with pytest.raises(ExternalSystemError, match="mock before-commit writeback failed"):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "Simulated failed action"},
            idempotency_key="action-log-failed-writeback",
            simulate_writeback_failure=True,
            ctx=ctx,
        )
    success = foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Successful action for action log"},
        idempotency_key="action-log-success",
        ctx=ctx,
    )

    action_log = foundry.materialization.run("action_log", ctx=ctx)
    action_log_run = _materialization_run_for_version(foundry, ctx, action_log.version_id)
    action_log_rows = foundry.materialization.replay_rows(str(action_log_run["id"]), ctx=ctx).rows

    assert [row["action_run_id"] for row in action_log_rows] == [success["actionRunId"]]
    assert {row["status"] for row in action_log_rows} == {"succeeded"}


def test_action_log_same_cursor_rerun_does_not_duplicate_rows(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    action = _approve_order(
        foundry,
        ctx,
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        idempotency_key="action-log-rerun",
    )

    first = foundry.materialization.run("action_log", ctx=ctx)
    second = foundry.materialization.run("action_log", ctx=ctx)
    first_run = _materialization_run_for_version(foundry, ctx, first.version_id)
    second_run = _materialization_run_for_version(foundry, ctx, second.version_id)
    first_rows = foundry.materialization.replay_rows(str(first_run["id"]), ctx=ctx).rows
    second_rows = foundry.materialization.replay_rows(str(second_run["id"]), ctx=ctx).rows

    assert [row["action_run_id"] for row in first_rows] == [action["actionRunId"]]
    assert [row["action_run_id"] for row in second_rows] == [action["actionRunId"]]
    assert first_run["object_store_watermark"] == second_run["object_store_watermark"]


def test_unknown_materialization_type_fails_closed(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "bad-materialization"))
    ctx = RequestContext(roles=("admin", "data_engineer"))
    foundry.datasets.ensure("ops.order_current", ctx=ctx, primary_key=["orderId"])
    with foundry.engine.begin() as conn:
        conn.execute(
            db.materializations.insert().values(
                id="mat_unknown_type",
                tenant_id=ctx.tenant_id,
                api_name="order_current",
                materialization_type="object_snapsh0t",
                source_ref={"objectType": "Order"},
                target_ref={"dataset": "ops.order_current"},
                trigger_config={"type": "manual"},
                enabled=True,
            )
        )

    with pytest.raises(ValidationFailed, match="unsupported materialization type"):
        foundry.materialization.run("order_current", ctx=ctx)


def test_transform_retry_after_commit_does_not_create_second_output_version(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "transform-retry"))
    ctx = RequestContext(roles=("admin", "data_engineer"))
    foundry.datasets.ensure("raw.retry_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.retry_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("raw.retry_orders", _csv(tmp_path, "retry_orders.csv", "O-1", 100), ctx=ctx)
    sql_path = tmp_path / "retry_orders.sql"
    sql_path.write_text("select order_id, amount from {{ input('raw.retry_orders') }}", encoding="utf-8")
    foundry.transforms.register(
        "retry_orders",
        entrypoint=sql_path,
        inputs={"orders": "raw.retry_orders"},
        output_dataset_ref="clean.retry_orders",
        ctx=ctx,
    )
    first = foundry.transforms.run("retry_orders", ctx=ctx)
    transform_run = next(
        run
        for run in foundry.operations.list_runs(ctx=ctx)["transformRuns"]
        if run["output_version_id"] == first.version_id
    )

    with pytest.raises(ValidationFailed, match="transform run is not failed"):
        foundry.transforms.retry_run(transform_run["id"], ctx=ctx)

    versions = foundry.datasets.list_versions("clean.retry_orders", ctx=ctx)
    assert [version["id"] for version in versions] == [first.version_id]


def test_transform_append_retry_is_fenced_against_double_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An append-mode transform has no snapshot-base guard, so two retries of the
    # same FAILED run would each open an output-producing transaction and commit
    # a duplicate output version (Tier 1: transform retry creates two outputs).
    # The retry claim must be atomically fenced: only the first retry runs.
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "append-retry-fence")
    foundry = FoundryLite(dependencies=dependencies)
    ctx = RequestContext(roles=("admin", "data_engineer"))
    foundry.datasets.ensure("raw.fence_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.fence_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("raw.fence_orders", _csv(tmp_path, "fence_orders.csv", "O-1", 100), ctx=ctx)
    sql_path = tmp_path / "fence_orders.sql"
    sql_path.write_text("select order_id, amount from {{ input('raw.fence_orders') }}", encoding="utf-8")
    foundry.transforms.register(
        "fence_orders",
        entrypoint=sql_path,
        inputs={"orders": "raw.fence_orders"},
        output_dataset_ref="clean.fence_orders",
        mode="append",
        ctx=ctx,
    )

    # Force the first run to FAIL after the output commit via a lineage failure,
    # leaving a FAILED run whose cause is gone once the patch is undone.
    def fail_lineage(self: TransformRunService, *_args: object) -> None:
        del self, _args
        raise RuntimeError("lineage insert exploded after output commit")

    monkeypatch.setattr(TransformRunService, "_record_transform_lineage", fail_lineage)
    with pytest.raises(InvariantViolation, match="dataset commit metadata persistence failed"):
        foundry.transforms.run("fence_orders", ctx=ctx)
    monkeypatch.undo()

    failed_run = next(
        run for run in foundry.operations.list_runs(ctx=ctx)["transformRuns"] if run["status"] == "FAILED"
    )

    foundry.transforms.retry_run(failed_run["id"], ctx=ctx)
    with pytest.raises(ConflictDetected, match="retry already claimed"):
        foundry.transforms.retry_run(failed_run["id"], ctx=ctx)

    versions = foundry.datasets.list_versions("clean.fence_orders", ctx=ctx)
    assert len(versions) == 1


def test_transform_output_and_lineage_commit_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "transform-lineage-atomic")
    foundry = FoundryLite(dependencies=dependencies)
    ctx = RequestContext(roles=("admin", "data_engineer"))
    foundry.datasets.ensure("raw.atomic_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.atomic_orders", ctx=ctx, primary_key=["order_id"])
    input_version = foundry.datasets.upload_csv(
        "raw.atomic_orders", _csv(tmp_path, "atomic_orders.csv", "O-1", 100), ctx=ctx
    )
    sql_path = tmp_path / "atomic_orders.sql"
    sql_path.write_text("select order_id, amount from {{ input('raw.atomic_orders') }}", encoding="utf-8")
    foundry.transforms.register(
        "atomic_orders",
        entrypoint=sql_path,
        inputs={"orders": "raw.atomic_orders"},
        output_dataset_ref="clean.atomic_orders",
        ctx=ctx,
    )

    def fail_lineage(self: TransformRunService, *_args: object) -> None:
        del self, _args
        raise RuntimeError("lineage insert exploded after output commit")

    monkeypatch.setattr(TransformRunService, "_record_transform_lineage", fail_lineage)

    with pytest.raises(InvariantViolation, match="dataset commit metadata persistence failed") as exc_info:
        foundry.transforms.run("atomic_orders", ctx=ctx)

    cleanup = exc_info.value.details["orphan_cleanup"]
    failed_run = next(
        run for run in foundry.operations.list_runs(ctx=ctx)["transformRuns"] if run["status"] == "FAILED"
    )
    assert cleanup["removed"] is True
    assert not Path(str(cleanup["manifest_uri"])).exists()
    assert not list(dependencies.storage_root.glob(f"**/version={cleanup['version_id']}"))
    assert foundry.datasets.list_versions("clean.atomic_orders", ctx=ctx) == []
    assert foundry.operations.lineage(input_version.version_id, ctx=ctx) == []
    assert failed_run["output_version_id"] is None
    assert failed_run["error"]["details"]["orphan_cleanup"]["version_id"] == cleanup["version_id"]


def test_transform_quality_block_preserves_failure_evidence_without_output_version(tmp_path: Path) -> None:
    foundry = FoundryLite(
        dependencies=create_local_core_dependencies(storage_root=tmp_path / "transform-quality-block")
    )
    ctx = RequestContext(roles=("admin", "data_engineer"))
    foundry.datasets.ensure("raw.blocked_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.blocked_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("raw.blocked_orders", _csv(tmp_path, "blocked_orders.csv", "O-1", 100), ctx=ctx)
    sql_path = tmp_path / "blocked_orders.sql"
    sql_path.write_text(
        "select order_id, amount from {{ input('raw.blocked_orders') }} "
        "union all select order_id, amount from {{ input('raw.blocked_orders') }}",
        encoding="utf-8",
    )
    foundry.transforms.register(
        "blocked_orders",
        entrypoint=sql_path,
        inputs={"orders": "raw.blocked_orders"},
        output_dataset_ref="clean.blocked_orders",
        ctx=ctx,
    )

    with pytest.raises(ValidationFailed, match="dataset checks failed"):
        foundry.transforms.run("blocked_orders", ctx=ctx)

    failed_run = next(
        run for run in foundry.operations.list_runs(ctx=ctx)["transformRuns"] if run["status"] == "FAILED"
    )
    assert foundry.datasets.list_versions("clean.blocked_orders", ctx=ctx) == []
    assert failed_run["output_version_id"] is None


def test_transform_retry_uses_failed_run_definition_snapshot(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "retry-quality-block"))
    ctx = RequestContext(roles=("admin", "data_engineer"))
    foundry.datasets.ensure("raw.retry_blocked_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.retry_blocked_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv(
        "raw.retry_blocked_orders", _csv(tmp_path, "retry_blocked_orders.csv", "O-1", 100), ctx=ctx
    )
    sql_path = tmp_path / "retry_blocked_orders.sql"
    sql_path.write_text("select missing_column from {{ input('raw.retry_blocked_orders') }}", encoding="utf-8")
    foundry.transforms.register(
        "retry_blocked_orders",
        entrypoint=sql_path,
        inputs={"orders": "raw.retry_blocked_orders"},
        output_dataset_ref="clean.retry_blocked_orders",
        ctx=ctx,
    )
    with pytest.raises(ValidationFailed):
        foundry.transforms.run("retry_blocked_orders", ctx=ctx)
    failed_run = next(
        run for run in foundry.operations.list_runs(ctx=ctx)["transformRuns"] if run["status"] == "FAILED"
    )
    sql_path.write_text("select order_id, amount from {{ input('raw.retry_blocked_orders') }}", encoding="utf-8")
    foundry.transforms.register(
        "retry_blocked_orders",
        entrypoint=sql_path,
        inputs={"orders": "raw.retry_blocked_orders"},
        output_dataset_ref="clean.retry_blocked_orders",
        ctx=ctx,
    )

    with pytest.raises(ValidationFailed, match="transform failed"):
        foundry.transforms.retry_run(failed_run["id"], ctx=ctx)

    failed_runs = [run for run in foundry.operations.list_runs(ctx=ctx)["transformRuns"] if run["status"] == "FAILED"]
    assert foundry.datasets.list_versions("clean.retry_blocked_orders", ctx=ctx) == []
    assert len(failed_runs) == 2
    assert all(run["output_version_id"] is None for run in failed_runs)


def test_duckdb_oom_aborts_output_transaction(tmp_path: Path) -> None:
    compute = _FailingTransformAdapter()
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "transform-oom")
    foundry = FoundryLite(dependencies=replace(dependencies, compute_adapter=compute))
    ctx = RequestContext(roles=("admin", "data_engineer"))
    foundry.datasets.ensure("raw.oom_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.oom_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("raw.oom_orders", _csv(tmp_path, "oom_orders.csv", "O-1", 100), ctx=ctx)
    sql_path = tmp_path / "oom_orders.sql"
    sql_path.write_text("select order_id, amount from {{ input('raw.oom_orders') }}", encoding="utf-8")
    foundry.transforms.register(
        "oom_orders",
        entrypoint=sql_path,
        inputs={"orders": "raw.oom_orders"},
        output_dataset_ref="clean.oom_orders",
        ctx=ctx,
    )

    with pytest.raises(ValidationFailed, match="transform failed"):
        foundry.transforms.run("oom_orders", ctx=ctx)

    failed_run = next(
        run for run in foundry.operations.list_runs(ctx=ctx)["transformRuns"] if run["status"] == "FAILED"
    )
    assert foundry.datasets.list_versions("clean.oom_orders", ctx=ctx) == []
    assert failed_run["output_version_id"] is None
    assert failed_run["error"]["type"] == "MemoryError"
    assert failed_run["error"]["message"] == "simulated DuckDB OOM"
    assert compute.staged_path is not None
    assert not compute.staged_path.exists()


def test_sql_transform_cannot_read_arbitrary_filesystem_path(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "sql-guard"))
    ctx = RequestContext(roles=("admin", "data_engineer"))
    foundry.datasets.ensure("raw.guard_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.guard_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("raw.guard_orders", _csv(tmp_path, "guard_orders.csv", "O-1", 100), ctx=ctx)
    raw_path = tmp_path / "bypass.csv"
    raw_path.write_text("order_id,amount\nO-2,999\n", encoding="utf-8")
    sql_path = tmp_path / "bypass.sql"
    sql_path.write_text(f"select * from read_csv('{raw_path}')", encoding="utf-8")
    foundry.transforms.register(
        "raw_path_bypass",
        entrypoint=sql_path,
        inputs={"orders": "raw.guard_orders"},
        output_dataset_ref="clean.guard_orders",
        ctx=ctx,
    )

    with pytest.raises(ValidationFailed, match="declared input datasets"):
        foundry.transforms.run("raw_path_bypass", ctx=ctx)

    failed_run = next(
        run for run in foundry.operations.list_runs(ctx=ctx)["transformRuns"] if run["status"] == "FAILED"
    )
    assert foundry.datasets.list_versions("clean.guard_orders", ctx=ctx) == []
    assert failed_run["output_version_id"] is None
    assert failed_run["error"]["details"]["function"] == "read_csv"


def test_sql_transform_cannot_reference_undeclared_input_dataset(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "input-allowlist"))
    ctx = RequestContext(roles=("admin", "data_engineer"))
    foundry.datasets.ensure("raw.allowed_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("raw.secret_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.allowlisted_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("raw.allowed_orders", _csv(tmp_path, "allowed_orders.csv", "O-1", 100), ctx=ctx)
    foundry.datasets.upload_csv("raw.secret_orders", _csv(tmp_path, "secret_orders.csv", "O-2", 999), ctx=ctx)
    sql_path = tmp_path / "undeclared_input.sql"
    sql_path.write_text("select order_id, amount from {{ input('raw.secret_orders') }}", encoding="utf-8")
    foundry.transforms.register(
        "undeclared_input",
        entrypoint=sql_path,
        inputs={"orders": "raw.allowed_orders"},
        output_dataset_ref="clean.allowlisted_orders",
        ctx=ctx,
    )

    with pytest.raises(ValidationFailed, match="undeclared input datasets") as exc_info:
        foundry.transforms.run("undeclared_input", ctx=ctx)

    assert exc_info.value.details == {"dataset_refs": ["raw.secret_orders"]}
    assert foundry.datasets.list_versions("clean.allowlisted_orders", ctx=ctx) == []


def test_python_transform_runs_through_sdk_handles_without_raw_storage_path(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "python-guard"))
    ctx = RequestContext(roles=("admin", "data_engineer"))
    foundry.datasets.ensure("raw.python_guard_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.python_guard_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("raw.python_guard_orders", _csv(tmp_path, "python_orders.csv", "O-1", 100), ctx=ctx)
    entrypoint = tmp_path / "unsafe_transform.py"
    entrypoint.write_text(
        "\n".join(
            [
                "from foundry_lite.transforms_sdk import Input, Output, transform",
                "",
                "@transform(orders=Input('raw.python_guard_orders'), out=Output('clean.python_guard_orders'))",
                "def compute(orders, out):",
                "    if hasattr(orders, '_parquet_path'):",
                "        raise RuntimeError('raw storage path leaked')",
                "    rows = orders.read_rows()",
                "    out.write_rows([{'order_id': row['order_id'], 'amount': row['amount'] + 1} for row in rows])",
            ]
        ),
        encoding="utf-8",
    )

    foundry.transforms.register(
        "safe_python_transform",
        entrypoint=entrypoint,
        inputs={"orders": "raw.python_guard_orders"},
        output_dataset_ref="clean.python_guard_orders",
        language="python",
        ctx=ctx,
    )
    result = foundry.transforms.run("safe_python_transform", ctx=ctx)

    output_rows = foundry.datasets.preview("clean.python_guard_orders", ctx=ctx, version=result.version_id)
    transform_run = next(
        run
        for run in foundry.operations.list_runs(ctx=ctx)["transformRuns"]
        if run["output_version_id"] == result.version_id
    )
    assert [(row["order_id"], row["amount"]) for row in output_rows] == [("O-1", 101)]
    assert transform_run["definition_snapshot"]["language"] == "python"
    assert "python_source_sha256" in transform_run["definition_snapshot"]


def test_transform_commit_preserves_safe_execution_runtime_evidence(tmp_path: Path) -> None:
    compute = _RuntimeEvidenceTransformAdapter()
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "transform-runtime-evidence")
    foundry = FoundryLite(dependencies=replace(dependencies, compute_adapter=compute))
    ctx = RequestContext(roles=("admin", "data_engineer"))
    foundry.datasets.ensure("raw.evidence_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.evidence_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("raw.evidence_orders", _csv(tmp_path, "evidence.csv", "O-1", 100), ctx=ctx)
    foundry.transforms.register_sql(
        "evidence_orders",
        sql="select order_id, amount from {{ input('raw.evidence_orders') }}",
        inputs={"orders": "raw.evidence_orders"},
        output_dataset_ref="clean.evidence_orders",
        ctx=ctx,
    )

    result = foundry.transforms.run("evidence_orders", ctx=ctx)
    with foundry.engine.begin() as conn:
        transaction = (
            conn.execute(select(db.dataset_transactions).where(db.dataset_transactions.c.id == result.transaction_id))
            .mappings()
            .one()
        )

    assert transaction["metadata"]["runtimeEvidence"] == compute.runtime_evidence


def test_python_transform_row_errors_are_quarantined_without_failing_run(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "transform-record-dlq"))
    ctx = RequestContext(roles=("admin", "data_engineer"))
    foundry.datasets.ensure("raw.row_error_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.row_error_orders", ctx=ctx, primary_key=["order_id"])
    csv_path = tmp_path / "row_error_orders.csv"
    csv_path.write_text("order_id,amount\nO-1,100\nO-BAD,not-a-number\nO-2,200\n", encoding="utf-8")
    input_version = foundry.datasets.upload_csv("raw.row_error_orders", csv_path, ctx=ctx)
    entrypoint = tmp_path / "row_error_transform.py"
    entrypoint.write_text(
        "\n".join(
            [
                "from foundry_lite.transforms_sdk import Input, Output, transform",
                "",
                "@transform(orders=Input('raw.row_error_orders'), out=Output('clean.row_error_orders'))",
                "def compute(orders, out):",
                "    def clean(row):",
                "        amount = int(row['amount'])",
                "        return {'order_id': row['order_id'], 'amount': amount + 1}",
                "    out.write_rows(orders.map_rows(clean, on_error='quarantine'))",
            ]
        ),
        encoding="utf-8",
    )
    foundry.transforms.register(
        "row_error_orders",
        entrypoint=entrypoint,
        inputs={"orders": "raw.row_error_orders"},
        output_dataset_ref="clean.row_error_orders",
        language="python",
        ctx=ctx,
    )

    result = foundry.transforms.run("row_error_orders", ctx=ctx)

    output_rows = foundry.datasets.preview("clean.row_error_orders", ctx=ctx, version=result.version_id)
    transform_run = next(
        run
        for run in foundry.operations.list_runs(ctx=ctx)["transformRuns"]
        if run["output_version_id"] == result.version_id
    )
    with foundry.engine.begin() as conn:
        dead_letters = (
            conn.execute(
                select(db.dead_letter_records).where(db.dead_letter_records.c.source_run_id == transform_run["id"])
            )
            .mappings()
            .all()
        )
        transaction = (
            conn.execute(select(db.dataset_transactions).where(db.dataset_transactions.c.id == result.transaction_id))
            .mappings()
            .one()
        )

    assert [(row["order_id"], row["amount"]) for row in output_rows] == [("O-1", 101), ("O-2", 201)]
    assert result.row_count == 2
    assert len(dead_letters) == 1
    assert dead_letters[0]["source_dataset_version_id"] == input_version.version_id
    assert dead_letters[0]["payload"]["order_id"] == "O-BAD"
    assert dead_letters[0]["error_kind"] == "PYTHON_ROW_ERROR"
    assert dead_letters[0]["metadata"]["recordDlqKind"] == "transform_execution"
    assert dead_letters[0]["metadata"]["transformRunId"] == transform_run["id"]
    assert transaction["metadata"]["transformRecordDlq"]["quarantinedRows"] == 1
    audit_types = {event["event_type"] for event in foundry.operations.list_runs(ctx=ctx)["auditEvents"]}
    assert "dead_letter_record.quarantined" in audit_types
    with pytest.raises(ConflictDetected, match="rerunning the transform"):
        foundry.operations.retry_dead_letter_record(
            dead_letters[0]["id"],
            idempotency_key="retry-transform-row-error",
            ctx=ctx,
        )


def test_transform_record_dlq_retry_rebuilds_snapshot_after_fix(tmp_path: Path) -> None:
    foundry, ctx, dead_letter = _create_transform_record_dlq(tmp_path / "transform-record-dlq-success")
    _register_row_error_transform(foundry, ctx, tmp_path, should_fix_bad_row=True)

    replay = foundry.transforms.retry_dead_letter_record(
        dead_letter["id"],
        idempotency_key="transform-record-dlq-success",
        ctx=ctx,
    )
    duplicate = foundry.transforms.retry_dead_letter_record(
        dead_letter["id"],
        idempotency_key="transform-record-dlq-success",
        ctx=ctx,
    )
    rows = foundry.datasets.preview("clean.row_error_orders", ctx=ctx, version=replay["replayDatasetVersionId"])
    detail = foundry.operations.run_detail("transform", dead_letter["metadata"]["transformRunId"], ctx=ctx)
    audit_types = {event["event_type"] for event in foundry.operations.list_runs(ctx=ctx)["auditEvents"]}

    assert replay["status"] == "RESOLVED"
    assert replay["replayStatus"] == "SUCCEEDED"
    assert replay["transformRunId"] != dead_letter["metadata"]["transformRunId"]
    assert replay["replayDatasetVersionId"] is not None
    assert replay["rowCount"] == 3
    assert [(row["order_id"], row["amount"]) for row in rows] == [("O-1", 101), ("O-BAD", 1), ("O-2", 201)]
    assert duplicate["isIdempotentReplay"] is True
    assert duplicate["transformRunId"] == replay["transformRunId"]
    assert duplicate["replayDatasetVersionId"] == replay["replayDatasetVersionId"]
    assert any(
        row["target_run_id"] == replay["transformRunId"] and row["relation"] == "record_dlq_replay_of"
        for row in detail["runRelations"]
    )
    assert "dead_letter_record.transform_replay_requested" in audit_types
    assert "dead_letter_record.transform_replayed" in audit_types
    with pytest.raises(ConflictDetected, match="different key"):
        foundry.transforms.retry_dead_letter_record(dead_letter["id"], idempotency_key="new-transform-key", ctx=ctx)


def test_transform_record_dlq_retry_fails_without_fix_and_does_not_commit_output(tmp_path: Path) -> None:
    foundry, ctx, dead_letter = _create_transform_record_dlq(tmp_path / "transform-record-dlq-failure")

    replay = foundry.transforms.retry_dead_letter_record(
        dead_letter["id"],
        idempotency_key="transform-record-dlq-failure",
        ctx=ctx,
    )
    versions = foundry.datasets.list_versions("clean.row_error_orders", ctx=ctx)
    audit_types = {event["event_type"] for event in foundry.operations.list_runs(ctx=ctx)["auditEvents"]}

    assert replay["status"] == "QUARANTINED"
    assert replay["replayStatus"] == "FAILED"
    assert replay["transformRunId"] is not None
    assert replay["replayDatasetVersionId"] is None
    assert replay["error"]["type"] == "ValidationFailed"
    assert "new row quarantines" in replay["error"]["message"]
    assert len(versions) == 1
    assert "dead_letter_record.transform_replay_failed" in audit_types


def test_transform_record_dlq_retry_rejects_append_mode_fail_closed(tmp_path: Path) -> None:
    foundry, ctx, dead_letter = _create_transform_record_dlq(
        tmp_path / "transform-record-dlq-append",
        mode="append",
    )

    replay = foundry.transforms.retry_dead_letter_record(
        dead_letter["id"],
        idempotency_key="transform-record-dlq-append",
        ctx=ctx,
    )
    versions = foundry.datasets.list_versions("clean.row_error_orders", ctx=ctx)

    assert replay["status"] == "QUARANTINED"
    assert replay["replayStatus"] == "FAILED"
    assert replay["transformRunId"] is None
    assert replay["replayDatasetVersionId"] is None
    assert "duplicate-safe merge policy" in replay["error"]["message"]
    assert len(versions) == 1


def test_transform_append_output_mode_commits_append_transaction(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "transform-mode"))
    ctx = RequestContext(roles=("admin", "data_engineer"))
    foundry.datasets.ensure("raw.mode_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.mode_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("raw.mode_orders", _csv(tmp_path, "mode_orders.csv", "O-1", 100), ctx=ctx)
    sql_path = tmp_path / "mode_orders.sql"
    sql_path.write_text("select order_id, amount from {{ input('raw.mode_orders') }}", encoding="utf-8")
    foundry.transforms.register(
        "mode_orders",
        entrypoint=sql_path,
        inputs={"orders": "raw.mode_orders"},
        output_dataset_ref="clean.mode_orders",
        mode="append",
        ctx=ctx,
    )

    result = foundry.transforms.run("mode_orders", ctx=ctx)
    output_rows = foundry.datasets.preview("clean.mode_orders", ctx=ctx, version=result.version_id)
    transform_run = next(
        run
        for run in foundry.operations.list_runs(ctx=ctx)["transformRuns"]
        if run["output_version_id"] == result.version_id
    )
    with foundry.engine.begin() as conn:
        tx = (
            conn.execute(select(db.dataset_transactions).where(db.dataset_transactions.c.id == result.transaction_id))
            .mappings()
            .one()
        )

    assert [(row["order_id"], row["amount"]) for row in output_rows] == [("O-1", 100)]
    assert transform_run["definition_snapshot"]["mode"] == "append"
    assert tx["tx_type"] == "APPEND"


def test_transform_scheduler_tick_rebuilds_snapshot_when_input_version_changes(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "transform-scheduler"))
    ctx = RequestContext(roles=("admin", "data_engineer"))
    foundry.datasets.ensure("raw.scheduler_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.scheduler_orders", ctx=ctx, primary_key=["order_id"])
    first_input = foundry.datasets.upload_csv(
        "raw.scheduler_orders",
        _csv(tmp_path, "sched_v1.csv", "O-1", 100),
        ctx=ctx,
    )
    sql_path = tmp_path / "scheduler_orders.sql"
    sql_path.write_text("select order_id, amount from {{ input('raw.scheduler_orders') }}", encoding="utf-8")
    foundry.transforms.register(
        "scheduler_orders",
        entrypoint=sql_path,
        inputs={"orders": "raw.scheduler_orders"},
        output_dataset_ref="clean.scheduler_orders",
        ctx=ctx,
    )
    first_output = foundry.transforms.run("scheduler_orders", ctx=ctx)
    second_input = foundry.datasets.upload_csv(
        "raw.scheduler_orders",
        _csv(tmp_path, "sched_v2.csv", "O-1", 125),
        ctx=ctx,
    )

    preview = foundry.transforms.preview_due(ctx=ctx, max_runs=5)
    due = next(row for row in preview["due"] if row["apiName"] == "scheduler_orders")
    tick = foundry.transforms.run_due(ctx=ctx, max_runs=5)
    started = next(row for row in tick["started"] if row["decision"]["apiName"] == "scheduler_orders")
    repeat = foundry.transforms.run_due(ctx=ctx, max_runs=5)
    repeat_skip = next(row for row in repeat["skipped"] if row["apiName"] == "scheduler_orders")
    rows = foundry.datasets.preview("clean.scheduler_orders", ctx=ctx, version=started["run"]["versionId"])
    detail = foundry.operations.run_detail("transform", str(started["run"]["transformRunId"]), ctx=ctx)

    assert due["reason"] == "input_version_changed"
    assert due["changedInputRefs"] == ["raw.scheduler_orders"]
    assert due["lastSuccessfulInputVersions"] == {"raw.scheduler_orders": first_input.version_id}
    assert due["latestInputVersions"] == {"raw.scheduler_orders": second_input.version_id}
    assert started["run"]["versionId"] != first_output.version_id
    assert [(row["order_id"], row["amount"]) for row in rows] == [("O-1", 125)]
    assert repeat["started"] == []
    assert repeat_skip["reason"] == "up_to_date"
    assert any(row["relation"] == "scheduled_rebuild_of" for row in detail["runRelations"])


def test_transform_scheduler_skips_append_mode_until_merge_policy_exists(tmp_path: Path) -> None:
    foundry = FoundryLite(
        dependencies=create_local_core_dependencies(storage_root=tmp_path / "transform-scheduler-append")
    )
    ctx = RequestContext(roles=("admin", "data_engineer"))
    foundry.datasets.ensure("raw.scheduler_append_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.scheduler_append_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("raw.scheduler_append_orders", _csv(tmp_path, "append_v1.csv", "O-1", 100), ctx=ctx)
    sql_path = tmp_path / "scheduler_append_orders.sql"
    sql_path.write_text("select order_id, amount from {{ input('raw.scheduler_append_orders') }}", encoding="utf-8")
    foundry.transforms.register(
        "scheduler_append_orders",
        entrypoint=sql_path,
        inputs={"orders": "raw.scheduler_append_orders"},
        output_dataset_ref="clean.scheduler_append_orders",
        mode="append",
        ctx=ctx,
    )

    preview = foundry.transforms.preview_due(ctx=ctx, max_runs=5)
    skipped = next(row for row in preview["skipped"] if row["apiName"] == "scheduler_append_orders")
    tick = foundry.transforms.run_due(ctx=ctx, max_runs=5)

    assert skipped["reason"] == "append_mode_requires_manual_run"
    assert tick["started"] == []
    assert foundry.datasets.list_versions("clean.scheduler_append_orders", ctx=ctx) == []


def test_transform_scheduler_skips_snapshot_until_input_version_exists(tmp_path: Path) -> None:
    foundry = FoundryLite(
        dependencies=create_local_core_dependencies(storage_root=tmp_path / "transform-scheduler-missing-input")
    )
    ctx = RequestContext(roles=("admin", "data_engineer"))
    foundry.datasets.ensure("raw.scheduler_empty_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.scheduler_empty_orders", ctx=ctx, primary_key=["order_id"])
    sql_path = tmp_path / "scheduler_empty_orders.sql"
    sql_path.write_text("select order_id, amount from {{ input('raw.scheduler_empty_orders') }}", encoding="utf-8")
    foundry.transforms.register(
        "scheduler_empty_orders",
        entrypoint=sql_path,
        inputs={"orders": "raw.scheduler_empty_orders"},
        output_dataset_ref="clean.scheduler_empty_orders",
        ctx=ctx,
    )

    preview = foundry.transforms.preview_due(ctx=ctx, max_runs=5)
    skipped = next(row for row in preview["skipped"] if row["apiName"] == "scheduler_empty_orders")
    tick = foundry.transforms.run_due(ctx=ctx, max_runs=5)

    assert skipped["reason"] == "input_version_missing"
    assert skipped["missingInputRefs"] == ["raw.scheduler_empty_orders"]
    assert tick["started"] == []
    assert foundry.datasets.list_versions("clean.scheduler_empty_orders", ctx=ctx) == []


def test_transform_scheduler_skips_snapshot_until_input_dataset_exists(tmp_path: Path) -> None:
    foundry = FoundryLite(
        dependencies=create_local_core_dependencies(storage_root=tmp_path / "transform-scheduler-missing-dataset")
    )
    ctx = RequestContext(roles=("admin", "data_engineer"))
    foundry.datasets.ensure("clean.scheduler_missing_dataset_orders", ctx=ctx, primary_key=["order_id"])
    sql_path = tmp_path / "scheduler_missing_dataset_orders.sql"
    sql_path.write_text(
        "select order_id, amount from {{ input('raw.scheduler_missing_dataset_orders') }}",
        encoding="utf-8",
    )
    foundry.transforms.register(
        "scheduler_missing_dataset_orders",
        entrypoint=sql_path,
        inputs={"orders": "raw.scheduler_missing_dataset_orders"},
        output_dataset_ref="clean.scheduler_missing_dataset_orders",
        ctx=ctx,
    )

    preview = foundry.transforms.preview_due(ctx=ctx, max_runs=5)
    skipped = next(row for row in preview["skipped"] if row["apiName"] == "scheduler_missing_dataset_orders")
    tick = foundry.transforms.run_due(ctx=ctx, max_runs=5)

    assert skipped["reason"] == "input_version_missing"
    assert skipped["missingInputRefs"] == ["raw.scheduler_missing_dataset_orders"]
    assert tick["started"] == []
    assert foundry.datasets.list_versions("clean.scheduler_missing_dataset_orders", ctx=ctx) == []


def test_sql_transform_pushes_partition_predicate_to_storage_adapter(tmp_path: Path) -> None:
    compute = _TransformInputPlanSpy()
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "transform-partition-pushdown")
    storage = _PartitionFilterSpy(dependencies.dataset_storage)
    foundry = FoundryLite(dependencies=replace(dependencies, compute_adapter=compute, dataset_storage=storage))
    ctx = demo_admin_context()
    dataset = foundry.datasets.ensure("raw.partitioned_orders", ctx=ctx, primary_key=["id"])
    foundry.datasets.ensure("clean.partitioned_orders", ctx=ctx, primary_key=["id"])
    transaction = foundry._services.dataset.transaction
    with foundry.engine.begin() as conn:
        tx_id = transaction._open_dataset_transaction(conn, ctx, dataset, "SNAPSHOT")
    first = foundry.dataset_storage.staging_file(
        tenant_id=ctx.tenant_id,
        dataset_id=dataset["id"],
        transaction_id=tx_id,
        file_name="bucket-a.parquet",
    )
    second = foundry.dataset_storage.staging_file(
        tenant_id=ctx.tenant_id,
        dataset_id=dataset["id"],
        transaction_id=tx_id,
        file_name="bucket-b.parquet",
    )
    validation = foundry.dataset_storage.staging_file(
        tenant_id=ctx.tenant_id,
        dataset_id=dataset["id"],
        transaction_id=tx_id,
        file_name="_validation.parquet",
    )
    foundry.compute_adapter.rows_to_parquet(
        [{"id": "O-1", "bucket": "a", "amount": 100}], first, ["id", "bucket", "amount"]
    )
    foundry.compute_adapter.rows_to_parquet(
        [{"id": "O-2", "bucket": "b", "amount": 200}], second, ["id", "bucket", "amount"]
    )
    foundry.compute_adapter.rows_to_parquet(
        [{"id": "O-1", "bucket": "a", "amount": 100}, {"id": "O-2", "bucket": "b", "amount": 200}],
        validation,
        ["id", "bucket", "amount"],
    )
    with foundry.engine.begin() as conn:
        transaction._finalize_open_transaction_parts(
            conn,
            ctx,
            dataset=dataset,
            transaction_id=tx_id,
            validation_parquet=validation,
            staged_files=(
                DatasetStagedFile(path=first, row_count=1, partition_values={"bucket": "a"}),
                DatasetStagedFile(path=second, row_count=1, partition_values={"bucket": "b"}),
            ),
            run_id="sync_partitioned_orders",
            audit_action="multi_part_dataset_commit",
            outbox_event_type="dataset.version.committed",
        )
    foundry.transforms.register_sql(
        "partitioned_orders",
        sql="select id, bucket, amount from {{ input('raw.partitioned_orders') }} where bucket = 'b'",
        inputs={"orders": "raw.partitioned_orders"},
        output_dataset_ref="clean.partitioned_orders",
        ctx=ctx,
    )

    storage.partition_filters.clear()
    result = foundry.transforms.run("partitioned_orders", ctx=ctx)
    filters_after_run = list(storage.partition_filters)
    rows = foundry.datasets.preview("clean.partitioned_orders", ctx=ctx, version=result.version_id)

    assert filters_after_run == [{"bucket": "b"}]
    assert compute.input_file_counts == [1]
    assert [(row["id"], row["bucket"], row["amount"]) for row in rows] == [("O-2", "b", 200)]


class _TransformInputPlanSpy(DuckDBComputeAdapter):
    def __init__(self) -> None:
        self.input_file_counts: list[int] = []

    def execute_transform(self, plan: TransformPlan) -> TransformExecutionResult:
        if isinstance(plan, SqlTransformPlan):
            self.input_file_counts.append(
                sum(_input_path_count(input_paths) for input_paths in plan.input_paths_by_ref.values())
            )
        return super().execute_transform(plan)


class _RuntimeEvidenceTransformAdapter(DuckDBComputeAdapter):
    runtime_evidence = {
        "executionMode": "kubernetes-job",
        "imageDigest": "sha256:" + "c" * 64,
        "networkDisabled": True,
    }

    def execute_transform(self, plan: TransformPlan) -> TransformExecutionResult:
        super().execute_transform(plan)
        return TransformExecutionResult(runtime_evidence=self.runtime_evidence)


class _PartitionFilterSpy:
    def __init__(self, wrapped: object) -> None:
        self._wrapped = wrapped
        self.partition_filters: list[Mapping[str, object] | None] = []

    def data_file_paths(
        self,
        manifest_uri: str,
        *,
        partition_filter: Mapping[str, object] | None = None,
    ) -> list[Path]:
        self.partition_filters.append(dict(partition_filter) if partition_filter is not None else None)
        return self._wrapped.data_file_paths(manifest_uri, partition_filter=partition_filter)  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)


def _input_path_count(input_paths: object) -> int:
    if isinstance(input_paths, Path):
        return 1
    if isinstance(input_paths, tuple):
        return len(input_paths)
    return 0


class _BeforeExecuteTransformAdapter(DuckDBComputeAdapter):
    def __init__(self) -> None:
        self.before_execute_transform: Callable[[], None] | None = None

    def execute_transform(self, plan: TransformPlan) -> None:
        callback = self.before_execute_transform
        self.before_execute_transform = None
        if callback is not None:
            callback()
        super().execute_transform(plan)


class _FailingTransformAdapter(DuckDBComputeAdapter):
    def __init__(self) -> None:
        self.staged_path: Path | None = None

    def execute_transform(self, plan: TransformPlan) -> None:
        self.staged_path = plan.target_path
        plan.target_path.write_bytes(b"partial parquet bytes")
        raise MemoryError("simulated DuckDB OOM")


def _csv(tmp_path: Path, name: str, order_id: str, amount: int) -> Path:
    path = tmp_path / name
    path.write_text(f"order_id,amount\n{order_id},{amount}\n", encoding="utf-8")
    return path


def _create_transform_record_dlq(
    root: Path,
    *,
    mode: str = "snapshot",
) -> tuple[FoundryLite, RequestContext, Mapping[str, object]]:
    root.mkdir(parents=True, exist_ok=True)
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=root / "runtime"))
    ctx = RequestContext(roles=("admin", "data_engineer"))
    foundry.datasets.ensure("raw.row_error_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.row_error_orders", ctx=ctx, primary_key=["order_id"])
    csv_path = root / "row_error_orders.csv"
    csv_path.write_text("order_id,amount\nO-1,100\nO-BAD,not-a-number\nO-2,200\n", encoding="utf-8")
    foundry.datasets.upload_csv("raw.row_error_orders", csv_path, ctx=ctx)
    _register_row_error_transform(foundry, ctx, root, mode=mode)
    result = foundry.transforms.run("row_error_orders", ctx=ctx)
    transform_run = next(
        run
        for run in foundry.operations.list_runs(ctx=ctx)["transformRuns"]
        if run["output_version_id"] == result.version_id
    )
    return foundry, ctx, _dead_letter_for_transform_run(foundry, str(transform_run["id"]))


def _register_row_error_transform(
    foundry: FoundryLite,
    ctx: RequestContext,
    root: Path,
    *,
    should_fix_bad_row: bool = False,
    mode: str = "snapshot",
) -> None:
    entrypoint = root / ("row_error_transform_fixed.py" if should_fix_bad_row else "row_error_transform.py")
    entrypoint.write_text(_row_error_transform_source(should_fix_bad_row), encoding="utf-8")
    foundry.transforms.register(
        "row_error_orders",
        entrypoint=entrypoint,
        inputs={"orders": "raw.row_error_orders"},
        output_dataset_ref="clean.row_error_orders",
        language="python",
        mode=mode,
        ctx=ctx,
    )


def _row_error_transform_source(should_fix_bad_row: bool) -> str:
    amount_line = (
        "        amount = int(row['amount']) if str(row['amount']).isdigit() else 0"
        if should_fix_bad_row
        else "        amount = int(row['amount'])"
    )
    return "\n".join(
        [
            "from foundry_lite.transforms_sdk import Input, Output, transform",
            "",
            "@transform(orders=Input('raw.row_error_orders'), out=Output('clean.row_error_orders'))",
            "def compute(orders, out):",
            "    def clean(row):",
            amount_line,
            "        return {'order_id': row['order_id'], 'amount': amount + 1}",
            "    out.write_rows(orders.map_rows(clean, on_error='quarantine'))",
        ]
    )


def _dead_letter_for_transform_run(foundry: FoundryLite, transform_run_id: str) -> Mapping[str, object]:
    with foundry.engine.begin() as conn:
        return (
            conn.execute(
                select(db.dead_letter_records).where(db.dead_letter_records.c.source_run_id == transform_run_id)
            )
            .mappings()
            .one()
        )


def test_materialization_late_commit_action_not_skipped(
    foundry: FoundryLite,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    initial_order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    _approve_order(
        foundry,
        ctx,
        object_id="O-1001",
        expected_object_version=initial_order["objectVersion"],
        idempotency_key="initial-action-before-cursor",
    )
    action_result: dict[str, object] = {}
    original_write = MaterializationService._write_materialization_rows

    def write_after_late_action(self: MaterializationService, ctx: RequestContext, plan: MaterializationRunPlan):
        if plan.api_name == "action_log" and not action_result:
            order = foundry.objects.get("Order", "O-1002", ctx=ctx)
            action_result.update(
                foundry.actions.apply(
                    "ApproveOrder",
                    object_type="Order",
                    object_id="O-1002",
                    expected_object_version=order["objectVersion"],
                    params={"reason": "Late action after materialization cursor"},
                    idempotency_key="late-action-after-cursor",
                    ctx=ctx,
                )
            )
        return original_write(self, ctx, plan)

    monkeypatch.setattr(MaterializationService, "_write_materialization_rows", write_after_late_action)

    first = foundry.materialization.run("action_log", ctx=ctx)
    second = foundry.materialization.run("action_log", ctx=ctx)

    assert first.row_count == 1
    assert second.row_count == 2
    assert action_result["status"] == "succeeded"


def test_materialization_cursor_not_advanced_before_dataset_commit(
    foundry: FoundryLite,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    initial_order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    _approve_order(
        foundry,
        ctx,
        object_id="O-1001",
        expected_object_version=initial_order["objectVersion"],
        idempotency_key="materialization-fails-before-commit",
    )
    original_write = MaterializationService._write_materialization_rows

    def fail_before_dataset_commit(self: MaterializationService, ctx: RequestContext, plan: MaterializationRunPlan):
        if plan.api_name == "action_log":
            raise RuntimeError("injected materialization write failure")
        return original_write(self, ctx, plan)

    monkeypatch.setattr(MaterializationService, "_write_materialization_rows", fail_before_dataset_commit)

    with pytest.raises(RuntimeError, match="injected materialization write failure"):
        foundry.materialization.run("action_log", ctx=ctx)

    failed_run = next(
        row for row in foundry.operations.list_runs(ctx=ctx)["materializationRuns"] if row["api_name"] == "action_log"
    )
    assert foundry.datasets.list_versions("ops.action_log", ctx=ctx) == []
    assert failed_run["status"] == "FAILED"
    assert failed_run["target_dataset_version_id"] is None
    assert failed_run["error"]["message"] == "injected materialization write failure"


def test_materialization_retry_after_commit_metadata_failure_does_not_duplicate_output(
    foundry: FoundryLite,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    original_lineage = MaterializationService._record_materialization_lineage
    failed_once = False

    def fail_first_lineage(
        self: MaterializationService,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: MaterializationRunPlan,
        result: CommitResult,
    ) -> None:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("injected materialization lineage failure")
        original_lineage(self, conn, ctx, plan, result)

    monkeypatch.setattr(MaterializationService, "_record_materialization_lineage", fail_first_lineage)

    with pytest.raises(InvariantViolation, match="dataset commit metadata persistence failed") as exc_info:
        foundry.materialization.run("order_current", ctx=ctx)

    retry = foundry.materialization.run("order_current", ctx=ctx)
    runs = foundry.operations.list_runs(ctx=ctx)
    failed_run = next(run for run in runs["materializationRuns"] if run["status"] == "FAILED")
    completed_events = [event for event in runs["outboxEvents"] if event["event_type"] == "materialization.completed"]
    cleanup = exc_info.value.details["orphan_cleanup"]

    assert cleanup["removed"] is True
    assert not Path(str(cleanup["manifest_uri"])).exists()
    assert failed_run["target_dataset_version_id"] is None
    assert [version["id"] for version in foundry.datasets.list_versions("ops.order_current", ctx=ctx)] == [
        retry.version_id
    ]
    assert all(event["correlation_id"] != failed_run["id"] for event in completed_events)
    assert [event["aggregate_id"] for event in completed_events] == [retry.version_id]


def test_object_snapshot_mid_run_action_not_mixed(
    foundry: FoundryLite,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    original_write = MaterializationService._write_materialization_rows
    action_result: dict[str, object] = {}
    captured_rows: list[list[dict[str, object]]] = []

    def write_after_snapshot_capture(
        self: MaterializationService,
        ctx: RequestContext,
        plan: MaterializationRunPlan,
    ):
        if plan.api_name == "order_current":
            captured_rows.append([dict(row) for row in plan.rows])
        if plan.api_name == "order_current" and not action_result:
            order = foundry.objects.get("Order", "O-1001", ctx=ctx)
            action_result.update(
                _approve_order(
                    foundry,
                    ctx,
                    object_id="O-1001",
                    expected_object_version=order["objectVersion"],
                    idempotency_key="snapshot-mid-run-action",
                )
            )
        return original_write(self, ctx, plan)

    monkeypatch.setattr(MaterializationService, "_write_materialization_rows", write_after_snapshot_capture)

    first = foundry.materialization.run("order_current", ctx=ctx)
    second = foundry.materialization.run("order_current", ctx=ctx)
    first_status_by_order = {row["orderId"]: row["status"] for row in captured_rows[0]}
    second_status_by_order = {row["orderId"]: row["status"] for row in captured_rows[1]}

    assert action_result["status"] == "succeeded"
    assert first.row_count == 3
    assert first_status_by_order["O-1001"] == "PENDING"
    assert second.row_count == 3
    assert second_status_by_order["O-1001"] == "APPROVED"


def test_object_snapshot_fixed_watermark_hash_reproducible(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)

    first = foundry.materialization.run("order_current", ctx=ctx)
    first_run = _materialization_run_for_version(foundry, ctx, first.version_id)
    first_replay = foundry.materialization.replay_rows(str(first_run["id"]), ctx=ctx)

    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    _approve_order(
        foundry,
        ctx,
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        idempotency_key="fixed-watermark-after-snapshot",
    )

    replay_after_change = foundry.materialization.replay_rows(str(first_run["id"]), ctx=ctx)
    second = foundry.materialization.run("order_current", ctx=ctx)
    second_run = _materialization_run_for_version(foundry, ctx, second.version_id)
    second_replay = foundry.materialization.replay_rows(str(second_run["id"]), ctx=ctx)

    assert replay_after_change.row_hash == first_replay.row_hash
    assert _status_by_order(replay_after_change.rows)["O-1001"] == "PENDING"
    assert _status_by_order(second_replay.rows)["O-1001"] == "APPROVED"


def _approve_order(
    foundry: FoundryLite,
    ctx: RequestContext,
    *,
    object_id: str,
    expected_object_version: int,
    idempotency_key: str,
) -> dict[str, object]:
    result = foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id=object_id,
        expected_object_version=expected_object_version,
        params={"reason": "Snapshot captured before this action"},
        idempotency_key=idempotency_key,
        ctx=ctx,
    )
    return dict(result)


def _materialization_run_for_version(
    foundry: FoundryLite,
    ctx: RequestContext,
    version_id: str,
) -> dict[str, object]:
    return next(
        dict(row)
        for row in foundry.operations.list_runs(ctx=ctx)["materializationRuns"]
        if row["target_dataset_version_id"] == version_id
    )


def _status_by_order(rows: Sequence[Mapping[str, object]]) -> dict[object, object]:
    return {row["orderId"]: row["status"] for row in rows}
