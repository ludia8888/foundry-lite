#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH=".:libs:apps/cli:apps/api"
export FOUNDRY_LITE_HOME="${FOUNDRY_LITE_HOME:-.foundry-lite-e2e}"
export FOUNDRY_LITE_OTEL_DISABLED="${FOUNDRY_LITE_OTEL_DISABLED:-1}"

rm -rf "$FOUNDRY_LITE_HOME"
uv run python - <<'PY' >/tmp/foundry-lite-e2e-seed.json
import json
import os

from foundry_lite.application.core import FoundryLiteCore
from foundry_lite.domain.context import demo_admin_context

ctx = demo_admin_context()
core = FoundryLiteCore(storage_root=os.environ["FOUNDRY_LITE_HOME"])
core.seed_supply_chain_demo_files()
core.ensure_dataset("raw.erp_orders", ctx=ctx, primary_key=["order_id"])
core.ensure_dataset("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])
core.ensure_dataset("clean.orders", ctx=ctx, primary_key=["order_id"])
core.ensure_dataset("clean.customers", ctx=ctx, primary_key=["customer_id"])
core.ensure_dataset("ops.action_log", ctx=ctx, primary_key=["action_run_id"])
core.ensure_dataset("ops.order_current", ctx=ctx, primary_key=["orderId"])
core.register_supply_chain_demo_transforms(ctx)
orders_raw = core.upload_csv("raw.erp_orders", "examples/supply-chain-demo/data/orders.csv", ctx=ctx)
customers_raw = core.upload_csv("raw.crm_customers", "examples/supply-chain-demo/data/customers.csv", ctx=ctx)
clean_orders = core.run_transform("clean_orders", ctx=ctx)
clean_customers = core.run_transform("clean_customers", ctx=ctx)
ontology = core.apply_ontology("examples/supply-chain-demo/ontology/order-customer.yaml", ctx=ctx)
order_index = core.index_rebuild("Order", ctx=ctx)
customer_index = core.index_rebuild("Customer", ctx=ctx)
print(
    json.dumps(
        {
            "rawOrdersVersion": orders_raw.version_id,
            "rawCustomersVersion": customers_raw.version_id,
            "cleanOrdersVersion": clean_orders.version_id,
            "cleanCustomersVersion": clean_customers.version_id,
            "ontology": ontology,
            "orderIndex": order_index,
            "customerIndex": customer_index,
        },
        sort_keys=True,
    )
)
PY
uv run python -m foundry_lite_api.main
