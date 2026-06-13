from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from foundry_lite.application.ports.transform_repository import TransformRow
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SUPPLY_CHAIN_DEMO_ROOT = PROJECT_ROOT / "examples" / "supply-chain-demo"
RegisterTransform = Callable[..., TransformRow]

REQUIRED_SUPPLY_CHAIN_DEMO_FILES = (
    "data/orders.csv",
    "data/customers.csv",
    "transforms/clean_orders.sql",
    "transforms/clean_customers.sql",
    "transforms/customer_risk.sql",
    "ontology/order-customer.yaml",
)


def supply_chain_demo_path(*parts: str) -> Path:
    return SUPPLY_CHAIN_DEMO_ROOT.joinpath(*parts)


def ensure_supply_chain_demo_files() -> None:
    missing = [
        relative_path
        for relative_path in REQUIRED_SUPPLY_CHAIN_DEMO_FILES
        if not supply_chain_demo_path(relative_path).exists()
    ]
    if missing:
        raise ValidationFailed(
            "supply-chain demo files are missing",
            details={"root": str(SUPPLY_CHAIN_DEMO_ROOT), "missing": missing},
        )


def register_supply_chain_demo_transforms(register_transform: RegisterTransform, ctx: RequestContext) -> None:
    ensure_supply_chain_demo_files()
    register_transform(
        "clean_orders",
        entrypoint=supply_chain_demo_path("transforms", "clean_orders.sql"),
        inputs={"orders": "raw.erp_orders"},
        output_dataset_ref="clean.orders",
        checks=[
            {"type": "unique", "column": "order_id"},
            {"type": "not_null", "columns": ["order_id", "customer_id"]},
        ],
        ctx=ctx,
    )
    register_transform(
        "clean_customers",
        entrypoint=supply_chain_demo_path("transforms", "clean_customers.sql"),
        inputs={"customers": "raw.crm_customers"},
        output_dataset_ref="clean.customers",
        checks=[
            {"type": "unique", "column": "customer_id"},
            {"type": "not_null", "columns": ["customer_id"]},
        ],
        ctx=ctx,
    )
    register_transform(
        "customer_risk",
        entrypoint=supply_chain_demo_path("transforms", "customer_risk.sql"),
        inputs={"customers": "clean.customers", "orders": "ops.order_current"},
        output_dataset_ref="clean.customers",
        checks=[
            {"type": "unique", "column": "customer_id"},
            {"type": "not_null", "columns": ["customer_id"]},
        ],
        ctx=ctx,
    )
