"""Public-path proof for Action Contract v3 discovery and execution."""

from __future__ import annotations

from pathlib import Path

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import RequestContext, demo_admin_context

from tests.conftest import DEMO_ROOT

_V3_ACTION = """
  - apiName: ExpediteOrder
    contractVersion: 3
    displayName: Expedite order
    description: Expedite one pending order with a deterministic note.
    target: Order
    riskLevel: low
    agentExecutionPolicy: autonomous
    agentToolDescription: Expedite a pending order after validating its version.
    parameters:
      - apiName: mode
        type: string
        required: true
        constraints: {enum: [standard, urgent]}
      - apiName: note
        type: string
        default: {kind: literal, value: Standard handling}
        overrides:
          - when:
              op: eq
              left: {kind: parameter, parameter: mode}
              right: {kind: literal, value: urgent}
            config:
              default: {kind: literal, value: Urgent handling}
          - when:
              op: eq
              left: {kind: parameter, parameter: mode}
              right: {kind: literal, value: urgent}
            config: {visible: false}
    submissionCriteria:
      any:
        - op: eq
          left: {kind: objectProperty, property: status}
          right: {kind: literal, value: PENDING}
        - op: eq
          left: {kind: objectProperty, property: status}
          right: {kind: literal, value: REVIEW}
    permissions:
      allowedRoles: [ops_manager]
    rules:
      - kind: modifyObject
        ruleId: expedite
        objectType: Order
        target: {kind: parameter, parameter: __target__}
        assignments:
          - property: operatorNote
            value: {kind: parameter, parameter: note}
"""


def _v3_ontology(tmp_path: Path) -> Path:
    source = (DEMO_ROOT / "ontology" / "order-customer.yaml").read_text(encoding="utf-8")
    target = tmp_path / "order-customer-v3.yaml"
    target.write_text(source + _V3_ACTION, encoding="utf-8")
    return target


def _prepare_v3_demo(foundry: FoundryLite, tmp_path: Path) -> RequestContext:
    ctx = demo_admin_context()
    foundry.demo.seed_files()
    for dataset_ref, primary_key in (
        ("raw.erp_orders", "order_id"),
        ("raw.crm_customers", "customer_id"),
        ("clean.orders", "order_id"),
        ("clean.order_finance", "order_id"),
        ("clean.customers", "customer_id"),
    ):
        foundry.datasets.ensure(dataset_ref, ctx=ctx, primary_key=[primary_key])
    foundry.demo.register_transforms(ctx)
    foundry.datasets.upload_csv("raw.erp_orders", str(DEMO_ROOT / "data" / "orders.csv"), ctx=ctx)
    foundry.datasets.upload_csv("raw.crm_customers", str(DEMO_ROOT / "data" / "customers.csv"), ctx=ctx)
    for transform in ("clean_orders", "clean_order_finance", "clean_customers"):
        foundry.transforms.run(transform, ctx=ctx)
    foundry.ontology.apply(str(_v3_ontology(tmp_path)), ctx=ctx)
    foundry.objects.reindex("Order", ctx=ctx)
    foundry.objects.reindex("Customer", ctx=ctx)
    return ctx


def test_v3_catalog_schema_cursor_and_defaulted_apply_share_one_contract(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = _prepare_v3_demo(foundry, tmp_path)

    first_page = foundry.actions.list(limit=1, ctx=ctx)
    assert [item["apiName"] for item in first_page["items"]] == ["ApproveOrder"]
    assert first_page["nextCursor"] is not None
    second_page = foundry.actions.list(cursor=first_page["nextCursor"], limit=1, ctx=ctx)
    assert [item["apiName"] for item in second_page["items"]] == ["ExpediteOrder"]
    assert second_page["nextCursor"] is None

    action = foundry.actions.get("ExpediteOrder", ctx=ctx)
    schema = foundry.actions.schema("ExpediteOrder", ctx=ctx)
    assert action["contractVersion"] == 3
    assert action["riskLevel"] == "low"
    assert action["agentExecutionPolicy"] == "autonomous"
    assert action["contractFingerprint"] == schema["x-foundry-contract-fingerprint"]
    assert schema["required"] == ["mode"]

    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    response = foundry.actions.apply(
        "ExpediteOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"mode": "urgent"},
        idempotency_key="v3-expedite-urgent",
        ctx=ctx,
    )

    assert response["status"] == "succeeded"
    updated = foundry.objects.get("Order", "O-1001", ctx=ctx)
    assert updated["properties"]["operatorNote"] == "Urgent handling"
