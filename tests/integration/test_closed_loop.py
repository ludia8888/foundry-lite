from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.compute_adapter import TransformPlan
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.materialization_service import MaterializationRunPlan, MaterializationService
from foundry_lite.application.services.transform_service import TransformService
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ExternalSystemError, InvariantViolation, NotFound, ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters import DuckDBComputeAdapter
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies

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
    clean_order_lineage = foundry.operations.lineage(result["cleanOrdersVersion"])
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

    def fail_lineage(self: TransformService, *_args: object) -> None:
        del self, _args
        raise RuntimeError("lineage insert exploded after output commit")

    monkeypatch.setattr(TransformService, "_record_transform_lineage", fail_lineage)

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


def test_python_transform_cannot_access_raw_storage_path(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "python-guard"))
    ctx = RequestContext(roles=("admin", "data_engineer"))
    foundry.datasets.ensure("raw.python_guard_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.python_guard_orders", ctx=ctx, primary_key=["order_id"])
    entrypoint = tmp_path / "unsafe_transform.py"
    entrypoint.write_text("open('/tmp/raw-storage-path').read()\n", encoding="utf-8")

    with pytest.raises(ValidationFailed, match="unsupported transform language"):
        foundry.transforms.register(
            "unsafe_python_transform",
            entrypoint=entrypoint,
            inputs={"orders": "raw.python_guard_orders"},
            output_dataset_ref="clean.python_guard_orders",
            language="python",
            ctx=ctx,
        )

    with pytest.raises(NotFound):
        foundry.transforms.run("unsafe_python_transform", ctx=ctx)


def test_transform_output_mode_fails_closed_without_changing_definition(tmp_path: Path) -> None:
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
        ctx=ctx,
    )

    with pytest.raises(ValidationFailed, match="unsupported transform output mode") as exc_info:
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

    assert exc_info.value.details == {"mode": "append", "supported_modes": ["snapshot"]}
    assert [(row["order_id"], row["amount"]) for row in output_rows] == [("O-1", 100)]
    assert transform_run["definition_snapshot"]["mode"] == "snapshot"


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
