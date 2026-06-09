from __future__ import annotations

from typing import Any

from foundry_lite.application.demo_assets import (
    SUPPLY_CHAIN_DEMO_ROOT,
    ensure_supply_chain_demo_files,
    register_supply_chain_demo_transforms,
)
from foundry_lite.application.services.base import CoreServiceMixin
from foundry_lite.domain.context import RequestContext, demo_admin_context


class DemoServiceMixin(CoreServiceMixin):
    def run_supply_chain_demo(self, *, ctx: RequestContext | None = None) -> dict[str, Any]:
        ctx = ctx or demo_admin_context()
        self.seed_supply_chain_demo_files()
        self.ensure_dataset("raw.erp_orders", ctx=ctx, primary_key=["order_id"])
        self.ensure_dataset("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])
        self.ensure_dataset("clean.orders", ctx=ctx, primary_key=["order_id"])
        self.ensure_dataset("clean.customers", ctx=ctx, primary_key=["customer_id"])
        self.ensure_dataset("ops.action_log", ctx=ctx, primary_key=["action_run_id"])
        self.ensure_dataset("ops.order_current", ctx=ctx, primary_key=["orderId"])
        self._register_demo_transforms(ctx)

        orders_raw = self.upload_csv(
            "raw.erp_orders",
            SUPPLY_CHAIN_DEMO_ROOT / "data" / "orders.csv",
            ctx=ctx,
            sync_name="sync_orders_pg",
        )
        customers_raw = self.upload_csv(
            "raw.crm_customers",
            SUPPLY_CHAIN_DEMO_ROOT / "data" / "customers.csv",
            ctx=ctx,
            sync_name="upload_customers_csv",
        )
        clean_orders = self.run_transform("clean_orders", ctx=ctx)
        clean_customers = self.run_transform("clean_customers", ctx=ctx)
        ontology = self.apply_ontology(SUPPLY_CHAIN_DEMO_ROOT / "ontology" / "order-customer.yaml", ctx=ctx)
        order_index = self.index_rebuild("Order", ctx=ctx)
        customer_index = self.index_rebuild("Customer", ctx=ctx)
        order_before = self.get_object("Order", "O-1001", ctx=ctx)
        action = self.apply_action(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order_before["objectVersion"],
            params={"reason": "Inventory confirmed"},
            idempotency_key="approve-O-1001-demo",
            ctx=ctx,
        )
        action_log = self.materialize("action_log", ctx=ctx)
        order_current = self.materialize("order_current", ctx=ctx)
        customer_risk = self.run_transform("customer_risk", ctx=ctx)
        customer_reindex = self.index_rebuild("Customer", ctx=ctx)
        customer = self.get_object("Customer", "C-100", ctx=ctx, explain=True)
        return {
            "rawOrdersVersion": orders_raw.version_id,
            "rawCustomersVersion": customers_raw.version_id,
            "cleanOrdersVersion": clean_orders.version_id,
            "cleanCustomersVersion": clean_customers.version_id,
            "ontology": ontology,
            "orderIndex": order_index,
            "customerIndex": customer_index,
            "action": action,
            "actionLogVersion": action_log.version_id,
            "orderCurrentVersion": order_current.version_id,
            "customerRiskVersion": customer_risk.version_id,
            "customerReindex": customer_reindex,
            "customer": customer,
        }

    def register_supply_chain_demo_transforms(self, ctx: RequestContext | None = None) -> None:
        self._register_demo_transforms(ctx or demo_admin_context())

    def seed_supply_chain_demo_files(self) -> None:
        ensure_supply_chain_demo_files()

    def _register_demo_transforms(self, ctx: RequestContext) -> None:
        register_supply_chain_demo_transforms(self.register_transform, ctx)
