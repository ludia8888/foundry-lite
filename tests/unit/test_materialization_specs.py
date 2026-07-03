"""Declarative object-snapshot materializations resolved from ontology YAML."""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import NotFound, ValidationFailed
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app
from pytest import MonkeyPatch, raises

from tests.conftest import prepare_indexed_demo

CUSTOMERS_CSV = "customer_id,customer_name,risk_score\nC-1,Acme,0.4\nC-2,Globex,0.7\n"

ADMIN_HEADERS = {
    "X-Tenant-ID": "tenant-demo",
    "X-User-ID": "user-demo-admin",
    "X-Roles": "admin,data_engineer,ops_manager",
}


def _customer_definition(
    *,
    materialization: dict[str, object] | None = None,
    declare: bool = True,
) -> dict[str, object]:
    customer: dict[str, object] = {
        "apiName": "Customer",
        "primaryKey": "customerId",
        "backing": {"dataset": "clean.customers", "mode": "snapshot", "primaryKeyColumns": ["customer_id"]},
        "properties": [
            {"apiName": "customerId", "column": "customer_id", "type": "string", "nullable": False, "indexed": True},
            {"apiName": "name", "column": "customer_name", "type": "string"},
            {"apiName": "riskScore", "column": "risk_score", "type": "float", "classification": "finance"},
            {"apiName": "note", "type": "string", "editable": True, "source": "edit_layer"},
        ],
    }
    if declare:
        customer["materialization"] = materialization or {
            "dataset": "ops.customer_current",
            "mode": "object_snapshot",
        }
    return {
        "objectTypes": [customer],
        "actionTypes": [
            {
                "apiName": "TagCustomer",
                "target": "Customer",
                "parameters": [{"apiName": "note", "type": "string", "required": True}],
                "mutations": [{"type": "setProperty", "property": "note", "valueFrom": "params.note"}],
            }
        ],
    }


def _yaml_text(definition: dict[str, object]) -> str:
    return yaml.safe_dump(definition, sort_keys=False)


def _prepare_customer_ontology(
    foundry: FoundryLite,
    tmp_path: Path,
    ctx: RequestContext,
    definition: dict[str, object] | None = None,
) -> None:
    csv_path = tmp_path / "customers.csv"
    csv_path.write_text(CUSTOMERS_CSV, encoding="utf-8")
    foundry.datasets.ensure("clean.customers", ctx=ctx, primary_key=["customer_id"])
    foundry.datasets.upload_csv("clean.customers", str(csv_path), ctx=ctx)
    foundry.ontology.apply_text(_yaml_text(definition or _customer_definition()), ctx=ctx)
    foundry.datasets.ensure("ops.customer_current", ctx=ctx, primary_key=["customerId"])
    foundry.objects.reindex("Customer", ctx=ctx)


def _tag_customer(foundry: FoundryLite, ctx: RequestContext, customer_id: str, note: str) -> None:
    customer = foundry.objects.get("Customer", customer_id, ctx=ctx)
    foundry.actions.apply(
        "TagCustomer",
        object_type="Customer",
        object_id=customer_id,
        expected_object_version=customer["objectVersion"],
        params={"note": note},
        idempotency_key=f"tag-{customer_id}-{note}",
        ctx=ctx,
    )


def _materialization_run_id(foundry: FoundryLite, ctx: RequestContext, version_id: str) -> str:
    return str(
        next(
            row
            for row in foundry.operations.query_runs(ctx=ctx, run_type="materialization")["materializationRuns"]
            if row["target_dataset_version_id"] == version_id
        )["id"]
    )


def test_declared_customer_materialization_writes_snapshot_rows_with_object_version(foundry, tmp_path) -> None:
    ctx = demo_admin_context()
    _prepare_customer_ontology(foundry, tmp_path, ctx)

    result = foundry.materialization.run("customer_current", ctx=ctx)
    rows = foundry.datasets.preview("ops.customer_current", ctx=ctx, version=result.version_id)

    assert result.dataset_ref == "ops.customer_current"
    assert result.row_count == 2
    assert [row["customerId"] for row in rows] == ["C-1", "C-2"]
    assert rows[0]["name"] == "Acme"
    assert all(isinstance(row["objectVersion"], int) for row in rows)
    # Classified (finance) properties are a data-plane concern for materialized
    # datasets and must not be dropped or masked, matching order_current.
    assert rows[0]["riskScore"] == 0.4
    assert rows[1]["riskScore"] == 0.7


def test_catalog_exposes_declared_materialization(foundry, tmp_path) -> None:
    ctx = demo_admin_context()
    _prepare_customer_ontology(foundry, tmp_path, ctx)

    catalog = foundry.ontology.catalog(ctx=ctx)

    assert catalog["objectTypes"][0]["materialization"] == {
        "dataset": "ops.customer_current",
        "mode": "object_snapshot",
    }


def test_declared_materialization_includes_action_edits_and_replays_deterministically(foundry, tmp_path) -> None:
    ctx = demo_admin_context()
    _prepare_customer_ontology(foundry, tmp_path, ctx)

    first = foundry.materialization.run("customer_current", ctx=ctx)
    first_run_id = _materialization_run_id(foundry, ctx, first.version_id)
    first_replay = foundry.materialization.replay_rows(first_run_id, ctx=ctx)

    _tag_customer(foundry, ctx, "C-1", "VIP")

    replay_after_edit = foundry.materialization.replay_rows(first_run_id, ctx=ctx)
    second = foundry.materialization.run("customer_current", ctx=ctx)
    second_rows = foundry.datasets.preview("ops.customer_current", ctx=ctx, version=second.version_id)
    second_replay = foundry.materialization.replay_rows(
        _materialization_run_id(foundry, ctx, second.version_id), ctx=ctx
    )
    third = foundry.materialization.run("customer_current", ctx=ctx)
    third_replay = foundry.materialization.replay_rows(_materialization_run_id(foundry, ctx, third.version_id), ctx=ctx)
    note_by_customer = {row["customerId"]: row["note"] for row in second_rows}

    # The first run's watermark stays pinned: replaying it after the edit
    # reproduces the original rows byte-for-byte.
    assert replay_after_edit.row_hash == first_replay.row_hash
    assert all(row["note"] is None for row in first_replay.rows)
    # The next run picks up the action edit through the edit layer.
    assert note_by_customer == {"C-1": "VIP", "C-2": None}
    # Re-running with no new data is deterministic.
    assert third_replay.row_hash == second_replay.row_hash


def test_order_current_and_action_log_still_materialize_from_demo_yaml(foundry) -> None:
    ctx = prepare_indexed_demo(foundry)

    action_log = foundry.materialization.run("action_log", ctx=ctx)
    order_current = foundry.materialization.run("order_current", ctx=ctx)
    rows = foundry.datasets.preview("ops.order_current", ctx=ctx, version=order_current.version_id)
    catalog = foundry.ontology.catalog(ctx=ctx)
    order = next(item for item in catalog["objectTypes"] if item["apiName"] == "Order")

    assert action_log.dataset_ref == "ops.action_log"
    assert order_current.dataset_ref == "ops.order_current"
    assert order_current.row_count == 3
    assert [row["orderId"] for row in rows] == ["O-1001", "O-1002", "O-1003"]
    assert all(isinstance(row["objectVersion"], int) for row in rows)
    assert order["materialization"] == {"dataset": "ops.order_current", "mode": "object_snapshot"}


def test_legacy_order_current_still_runs_without_yaml_declaration(foundry, tmp_path) -> None:
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,status\nO-1,PENDING\n", encoding="utf-8")
    foundry.datasets.ensure("clean.orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("clean.orders", str(csv_path), ctx=ctx)
    foundry.ontology.apply_text(
        _yaml_text(
            {
                "objectTypes": [
                    {
                        "apiName": "Order",
                        "primaryKey": "orderId",
                        "backing": {
                            "dataset": "clean.orders",
                            "mode": "snapshot",
                            "primaryKeyColumns": ["order_id"],
                        },
                        "properties": [
                            {
                                "apiName": "orderId",
                                "column": "order_id",
                                "type": "string",
                                "nullable": False,
                                "indexed": True,
                            },
                            {"apiName": "status", "column": "status", "type": "string"},
                        ],
                    }
                ]
            }
        ),
        ctx=ctx,
    )
    foundry.datasets.ensure("ops.order_current", ctx=ctx, primary_key=["orderId"])
    foundry.objects.reindex("Order", ctx=ctx)

    result = foundry.materialization.run("order_current", ctx=ctx)
    rows = foundry.datasets.preview("ops.order_current", ctx=ctx, version=result.version_id)
    catalog = foundry.ontology.catalog(ctx=ctx)

    assert result.row_count == 1
    assert rows[0]["orderId"] == "O-1"
    assert rows[0]["status"] == "PENDING"
    assert isinstance(rows[0]["objectVersion"], int)
    assert catalog["objectTypes"][0]["materialization"] is None


def test_unknown_materialization_spec_raises_not_found(foundry) -> None:
    with raises(NotFound) as exc_info:
        foundry.materialization.run("missing", ctx=demo_admin_context())

    assert exc_info.value.details == {"api_name": "missing"}


def test_materialization_declaration_rejects_malformed_dataset_ref(foundry) -> None:
    definition = _customer_definition(materialization={"dataset": "opscustomer_current"})

    with raises(ValidationFailed, match="materialization dataset must be of the form") as exc_info:
        foundry.ontology.validate(_yaml_text(definition), ctx=demo_admin_context())

    assert exc_info.value.details == {"objectType": "Customer", "dataset": "opscustomer_current"}


def test_materialization_declaration_rejects_unknown_mode(foundry) -> None:
    definition = _customer_definition(materialization={"dataset": "ops.customer_current", "mode": "table"})

    with raises(ValidationFailed, match="unsupported materialization mode") as exc_info:
        foundry.ontology.validate(_yaml_text(definition), ctx=demo_admin_context())

    assert exc_info.value.details["value"] == "table"
    assert exc_info.value.details["allowed"] == ["object_snapshot"]


def test_materialization_declaration_rejects_duplicate_spec_names(foundry) -> None:
    definition = _customer_definition()
    object_types = definition["objectTypes"]
    assert isinstance(object_types, list)
    object_types.append(
        {
            "apiName": "Account",
            "primaryKey": "customerId",
            "backing": {"dataset": "clean.customers", "mode": "snapshot", "primaryKeyColumns": ["customer_id"]},
            # Different namespace, same dataset name: the derived spec name
            # would collide with Customer's and shadow it at run time.
            "materialization": {"dataset": "archive.customer_current"},
            "properties": [
                {"apiName": "customerId", "column": "customer_id", "type": "string", "nullable": False},
            ],
        }
    )

    with raises(ValidationFailed, match="duplicate materialization spec name") as exc_info:
        foundry.ontology.validate(_yaml_text(definition), ctx=demo_admin_context())

    assert exc_info.value.details["specName"] == "customer_current"
    assert exc_info.value.details["conflictsWith"] == "Customer"


def test_materialization_spec_listing_reports_origin_and_targets(foundry, tmp_path) -> None:
    ctx = demo_admin_context()
    _prepare_customer_ontology(foundry, tmp_path, ctx)

    specs = {spec["apiName"]: spec for spec in foundry.materialization.list_specs(ctx=ctx)}

    assert specs["action_log"]["origin"] == "builtin"
    assert specs["action_log"]["targetDataset"] == "ops.action_log"
    assert specs["customer_current"] == {
        "apiName": "customer_current",
        "materializationType": "object_snapshot",
        "targetDataset": "ops.customer_current",
        "source": {"objectType": "Customer"},
        "origin": "ontology",
    }
    assert specs["order_current"]["origin"] == "legacy"


def test_api_lists_materialization_specs(foundry, tmp_path, monkeypatch: MonkeyPatch) -> None:
    ctx = demo_admin_context()
    _prepare_customer_ontology(foundry, tmp_path, ctx)
    monkeypatch.setattr(api_runtime, "foundry", foundry)

    response = TestClient(app).get("/api/materializations", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    specs = {spec["apiName"]: spec for spec in response.json()["specs"]}
    assert specs["customer_current"]["origin"] == "ontology"
    assert specs["customer_current"]["targetDataset"] == "ops.customer_current"
