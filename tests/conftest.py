from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.core import FoundryLiteCore
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


@pytest.fixture
def core(tmp_path: Path) -> FoundryLiteCore:
    return FoundryLiteCore(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))


def prepare_indexed_demo(core: FoundryLiteCore) -> RequestContext:
    ctx = demo_admin_context()
    core.seed_supply_chain_demo_files()
    core.ensure_dataset("raw.erp_orders", ctx=ctx, primary_key=["order_id"])
    core.ensure_dataset("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])
    core.ensure_dataset("clean.orders", ctx=ctx, primary_key=["order_id"])
    core.ensure_dataset("clean.customers", ctx=ctx, primary_key=["customer_id"])
    core.ensure_dataset("ops.action_log", ctx=ctx, primary_key=["action_run_id"])
    core.ensure_dataset("ops.order_current", ctx=ctx, primary_key=["orderId"])
    core.register_supply_chain_demo_transforms(ctx)
    core.upload_csv("raw.erp_orders", "examples/supply-chain-demo/data/orders.csv", ctx=ctx)
    core.upload_csv("raw.crm_customers", "examples/supply-chain-demo/data/customers.csv", ctx=ctx)
    core.run_transform("clean_orders", ctx=ctx)
    core.run_transform("clean_customers", ctx=ctx)
    core.apply_ontology("examples/supply-chain-demo/ontology/order-customer.yaml", ctx=ctx)
    core.index_rebuild("Order", ctx=ctx)
    core.index_rebuild("Customer", ctx=ctx)
    return ctx
