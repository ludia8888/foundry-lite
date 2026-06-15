from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.connector_adapter import ConnectorSnapshot
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.infrastructure.adapters import LocalConnectorAdapter
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


@pytest.mark.integration_scenario("connector_sync")
def test_testcontainers_connector_snapshot_reaches_materialized_closed_loop(postgres_fixture, tmp_path: Path) -> None:
    core = _postgres_core_with_connector(postgres_fixture, tmp_path)
    ctx = _prepare_supply_chain_metadata(core)

    raw_orders = core.datasets.sync_connector_snapshot(
        "raw.erp_orders", connector_name="postgres", resource_name="erp_orders", ctx=ctx
    )
    raw_customers = core.datasets.sync_connector_snapshot(
        "raw.crm_customers", connector_name="postgres", resource_name="crm_customers", ctx=ctx
    )
    clean_orders = core.transforms.run("clean_orders", ctx=ctx)
    core.transforms.run("clean_customers", ctx=ctx)
    core.ontology.apply("examples/supply-chain-demo/ontology/order-customer.yaml", ctx=ctx)
    order_index = core.objects.reindex("Order", ctx=ctx)
    core.objects.reindex("Customer", ctx=ctx)
    action = _approve_order(core, ctx)
    action_log = core.materialization.run("action_log", ctx=ctx)
    order_current = core.materialization.run("order_current", ctx=ctx)

    assert raw_orders.row_count == 3
    assert raw_customers.row_count == 2
    assert clean_orders.row_count == 3
    assert order_index["objects_upserted"] == 3
    assert action["status"] == "succeeded"
    assert action_log.row_count == 1
    assert order_current.row_count == 3
    assert _committed_connector_runs(core) == ["connector.postgres", "connector.postgres"]


def _postgres_core_with_connector(postgres_fixture, tmp_path: Path) -> FoundryLite:
    dependencies = create_local_core_dependencies(
        db_url=postgres_fixture.engine.url.render_as_string(hide_password=False),
        storage_root=tmp_path / "postgres-closed-loop",
    )
    return FoundryLite(dependencies=replace(dependencies, connector_adapter=_connector_adapter()))


def _connector_adapter() -> LocalConnectorAdapter:
    return LocalConnectorAdapter(
        {
            ("postgres", "erp_orders"): ConnectorSnapshot(
                connector_name="postgres",
                resource_name="erp_orders",
                rows=_order_rows(),
                schema={
                    "columns": ["order_id", "customer_id", "source_status", "amount", "margin", "order_ts", "region"]
                },
                cursor={"snapshot": "orders-1"},
                source_watermark="orders-watermark-1",
            ),
            ("postgres", "crm_customers"): ConnectorSnapshot(
                connector_name="postgres",
                resource_name="crm_customers",
                rows=_customer_rows(),
                schema={
                    "columns": [
                        "customer_id",
                        "customer_name",
                        "segment",
                        "region",
                        "risk_score",
                        "approved_order_count",
                    ]
                },
                cursor={"snapshot": "customers-1"},
                source_watermark="customers-watermark-1",
            ),
        }
    )


def _prepare_supply_chain_metadata(core: FoundryLite) -> RequestContext:
    ctx = demo_admin_context()
    core.demo.seed_files()
    core.datasets.ensure("raw.erp_orders", ctx=ctx, primary_key=["order_id"])
    core.datasets.ensure("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])
    core.datasets.ensure("clean.orders", ctx=ctx, primary_key=["order_id"])
    core.datasets.ensure("clean.customers", ctx=ctx, primary_key=["customer_id"])
    core.datasets.ensure("ops.action_log", ctx=ctx, primary_key=["action_run_id"])
    core.datasets.ensure("ops.order_current", ctx=ctx, primary_key=["orderId"])
    core.demo.register_transforms(ctx)
    return ctx


def _approve_order(core: FoundryLite, ctx: RequestContext) -> dict[str, object]:
    order = core.objects.get("Order", "O-1001", ctx=ctx)
    return core.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Testcontainers connector sync"},
        idempotency_key="testcontainers-connector-approve",
        ctx=ctx,
    )


def _committed_connector_runs(core: FoundryLite) -> list[str]:
    return [
        str(row["source_type"])
        for row in core.operations.list_runs()["syncRuns"]
        if row["status"] == "COMMITTED" and str(row["source_type"]).startswith("connector.")
    ]


def _order_rows() -> tuple[dict[str, object], ...]:
    return (
        _order("O-1001", "C-100", "PENDING", 1200.0, 230.0, "NA"),
        _order("O-1002", "C-101", "REVIEW", 800.0, 80.0, "EU"),
        _order("O-1003", "C-100", "APPROVED", 300.0, 20.0, "NA"),
    )


def _order(
    order_id: str,
    customer_id: str,
    source_status: str,
    amount: float,
    margin: float,
    region: str,
) -> dict[str, object]:
    return {
        "order_id": order_id,
        "customer_id": customer_id,
        "source_status": source_status,
        "amount": amount,
        "margin": margin,
        "order_ts": "2026-06-09T10:00:00Z",
        "region": region,
    }


def _customer_rows() -> tuple[dict[str, object], ...]:
    return (
        _customer("C-100", "Acme Supply", "enterprise", "NA", 0.2),
        _customer("C-101", "Blue River Retail", "midmarket", "EU", 0.5),
    )


def _customer(
    customer_id: str,
    customer_name: str,
    segment: str,
    region: str,
    risk_score: float,
) -> dict[str, object]:
    return {
        "customer_id": customer_id,
        "customer_name": customer_name,
        "segment": segment,
        "region": region,
        "risk_score": risk_score,
        "approved_order_count": 0,
    }
