"""Application service helpers for demo service workflows."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.action_types import ActionApplyResponse
from foundry_lite.application.demo_assets import (
    SUPPLY_CHAIN_DEMO_ROOT,
    ensure_supply_chain_demo_files,
    register_supply_chain_demo_transforms,
)
from foundry_lite.application.demo_types import SupplyChainDemoResult
from foundry_lite.application.ports import ObjectIndexRebuildResult, ObjectPayload, OntologyApplyResult
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.demo_protocols import (
    DemoActionApplier,
    DemoDatasetIngest,
    DemoDatasetRegistry,
    DemoMaterializer,
    DemoObjectIndexer,
    DemoObjectQuery,
    DemoOntologyApplier,
    DemoTransformRunner,
)
from foundry_lite.domain.context import RequestContext, demo_admin_context


@dataclass(frozen=True)
class DemoIndexArtifacts:
    ontology: OntologyApplyResult
    order_index: ObjectIndexRebuildResult
    customer_index: ObjectIndexRebuildResult


@dataclass(frozen=True)
class DemoCustomerRiskArtifacts:
    customer_risk: CommitResult
    customer_reindex: ObjectIndexRebuildResult
    customer: ObjectPayload


class DemoService(CoreService):
    required_dependencies = ()
    required_collaborators = (
        "action_service",
        "dataset_ingest_service",
        "dataset_registry_service",
        "materialization_service",
        "object_indexing_service",
        "object_query_service",
        "ontology_service",
        "transform_service",
    )
    action_service: DemoActionApplier
    dataset_ingest_service: DemoDatasetIngest
    dataset_registry_service: DemoDatasetRegistry
    materialization_service: DemoMaterializer
    object_indexing_service: DemoObjectIndexer
    object_query_service: DemoObjectQuery
    ontology_service: DemoOntologyApplier
    transform_service: DemoTransformRunner

    def run_supply_chain_demo(self, *, ctx: RequestContext | None = None) -> SupplyChainDemoResult:
        ctx = ctx or demo_admin_context()
        self.seed_supply_chain_demo_files()
        self._ensure_demo_datasets(ctx)
        self._register_demo_transforms(ctx)

        orders_raw, customers_raw = self._ingest_raw_demo_data(ctx)
        clean_orders, clean_order_finance, clean_customers = self._run_clean_demo_transforms(ctx)
        index_artifacts = self._index_demo_ontology(ctx)
        action = self._approve_demo_order(ctx)
        action_log, order_current = self._materialize_demo_outputs(ctx)
        risk_artifacts = self._refresh_demo_customer_risk(ctx)
        return {
            "rawOrdersVersion": orders_raw.version_id,
            "rawCustomersVersion": customers_raw.version_id,
            "cleanOrdersVersion": clean_orders.version_id,
            "cleanOrderFinanceVersion": clean_order_finance.version_id,
            "cleanCustomersVersion": clean_customers.version_id,
            "ontology": index_artifacts.ontology,
            "orderIndex": index_artifacts.order_index,
            "customerIndex": index_artifacts.customer_index,
            "action": action,
            "actionLogVersion": action_log.version_id,
            "orderCurrentVersion": order_current.version_id,
            "customerRiskVersion": risk_artifacts.customer_risk.version_id,
            "customerReindex": risk_artifacts.customer_reindex,
            "customer": risk_artifacts.customer,
        }

    def _ensure_demo_datasets(self, ctx: RequestContext) -> None:
        self.dataset_registry_service.ensure_dataset("raw.erp_orders", ctx=ctx, primary_key=["order_id"])
        self.dataset_registry_service.ensure_dataset("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])
        self.dataset_registry_service.ensure_dataset("clean.orders", ctx=ctx, primary_key=["order_id"])
        self.dataset_registry_service.ensure_dataset("clean.order_finance", ctx=ctx, primary_key=["order_id"])
        self.dataset_registry_service.ensure_dataset("clean.customers", ctx=ctx, primary_key=["customer_id"])
        self.dataset_registry_service.ensure_dataset("ops.action_log", ctx=ctx, primary_key=["action_run_id"])
        self.dataset_registry_service.ensure_dataset("ops.order_current", ctx=ctx, primary_key=["orderId"])

    def _ingest_raw_demo_data(self, ctx: RequestContext) -> tuple[CommitResult, CommitResult]:
        orders_raw = self.dataset_ingest_service.upload_csv(
            "raw.erp_orders",
            SUPPLY_CHAIN_DEMO_ROOT / "data" / "orders.csv",
            ctx=ctx,
            sync_name="sync_orders_pg",
        )
        customers_raw = self.dataset_ingest_service.upload_csv(
            "raw.crm_customers",
            SUPPLY_CHAIN_DEMO_ROOT / "data" / "customers.csv",
            ctx=ctx,
            sync_name="upload_customers_csv",
        )
        return orders_raw, customers_raw

    def _run_clean_demo_transforms(self, ctx: RequestContext) -> tuple[CommitResult, CommitResult, CommitResult]:
        clean_orders = self.transform_service.run_transform("clean_orders", ctx=ctx)
        clean_order_finance = self.transform_service.run_transform("clean_order_finance", ctx=ctx)
        clean_customers = self.transform_service.run_transform("clean_customers", ctx=ctx)
        return clean_orders, clean_order_finance, clean_customers

    def _index_demo_ontology(self, ctx: RequestContext) -> DemoIndexArtifacts:
        ontology = self.ontology_service.apply_ontology(
            SUPPLY_CHAIN_DEMO_ROOT / "ontology" / "order-customer.yaml", ctx=ctx
        )
        order_index = self.object_indexing_service.index_rebuild("Order", ctx=ctx)
        customer_index = self.object_indexing_service.index_rebuild("Customer", ctx=ctx)
        return DemoIndexArtifacts(ontology=ontology, order_index=order_index, customer_index=customer_index)

    def _approve_demo_order(self, ctx: RequestContext) -> ActionApplyResponse:
        order_before = self.object_query_service.get_object("Order", "O-1001", ctx=ctx)
        return self.action_service.apply_action(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order_before["objectVersion"],
            params={"reason": "Inventory confirmed"},
            idempotency_key="approve-O-1001-demo",
            ctx=ctx,
        )

    def _materialize_demo_outputs(self, ctx: RequestContext) -> tuple[CommitResult, CommitResult]:
        action_log = self.materialization_service.materialize("action_log", ctx=ctx)
        order_current = self.materialization_service.materialize("order_current", ctx=ctx)
        return action_log, order_current

    def _refresh_demo_customer_risk(self, ctx: RequestContext) -> DemoCustomerRiskArtifacts:
        customer_risk = self.transform_service.run_transform("customer_risk", ctx=ctx)
        customer_reindex = self.object_indexing_service.index_rebuild("Customer", ctx=ctx)
        customer = self.object_query_service.get_object("Customer", "C-100", ctx=ctx, include_explain=True)
        return DemoCustomerRiskArtifacts(
            customer_risk=customer_risk,
            customer_reindex=customer_reindex,
            customer=customer,
        )

    def register_supply_chain_demo_transforms(self, ctx: RequestContext | None = None) -> None:
        self._register_demo_transforms(ctx or demo_admin_context())

    def seed_supply_chain_demo_files(self) -> None:
        ensure_supply_chain_demo_files()

    def _register_demo_transforms(self, ctx: RequestContext) -> None:
        register_supply_chain_demo_transforms(self.transform_service.register_transform, ctx)
