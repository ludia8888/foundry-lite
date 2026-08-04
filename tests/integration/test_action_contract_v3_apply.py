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
    branchPolicy:
      enabled: true
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

_INTERFACE_ACTION = """
  - apiName: SetAssetRisk
    contractVersion: 3
    displayName: Set asset risk
    target: Asset
    targetKind: interface
    riskLevel: low
    agentExecutionPolicy: approval_required
    permissions:
      allowedRoles: [ops_manager]
    parameters:
      - apiName: riskScore
        type: float
        required: true
    rules:
      - kind: modifyObject
        ruleId: set-risk
        objectType: Order
        onInterface: Asset
        target: {kind: parameter, parameter: __target__}
        assignments:
          - property: riskScore
            value: {kind: parameter, parameter: riskScore}
"""


def _v3_ontology(tmp_path: Path) -> Path:
    source = (DEMO_ROOT / "ontology" / "order-customer.yaml").read_text(encoding="utf-8")
    target = tmp_path / "order-customer-v3.yaml"
    target.write_text(source + _V3_ACTION, encoding="utf-8")
    return target


def _interface_ontology(tmp_path: Path) -> Path:
    source = (DEMO_ROOT / "ontology" / "order-customer.yaml").read_text(encoding="utf-8")
    source = source.replace(
        "        indexed: true\n      - apiName: operatorNote",
        "        indexed: true\n        editable: true\n        editPolicy: edit_wins\n      - apiName: operatorNote",
    )
    source = source.replace(
        "        indexed: true\n      - apiName: approvedOrderCount",
        "        indexed: true\n        editable: true\n        editPolicy: edit_wins\n"
        "      - apiName: approvedOrderCount",
    )
    target = tmp_path / "order-customer-interface-action.yaml"
    target.write_text(source + _INTERFACE_ACTION, encoding="utf-8")
    return target


def _prepare_v3_demo(foundry: FoundryLite, tmp_path: Path) -> RequestContext:
    return _prepare_demo(foundry, _v3_ontology(tmp_path))


def _prepare_demo(foundry: FoundryLite, ontology_path: Path) -> RequestContext:
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
    foundry.ontology.apply(str(ontology_path), ctx=ctx)
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


def test_v3_plan_is_deterministic_authorized_and_does_not_mutate(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = _prepare_v3_demo(foundry, tmp_path)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    version = order["objectVersion"]

    plan = foundry.actions.plan(
        "ExpediteOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=version,
        params={"mode": "urgent"},
        ctx=ctx,
    )
    replay = foundry.actions.plan(
        "ExpediteOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=version,
        params={"mode": "urgent"},
        ctx=ctx,
    )
    dry_run = foundry.actions.dry_run(
        "ExpediteOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=version,
        params={"mode": "urgent"},
        ctx=ctx,
    )

    assert plan["planHash"] == replay["planHash"] == dry_run["planHash"]
    assert plan["parameters"]["note"] == "Urgent handling"
    assert plan["risk"]["effectiveLevel"] == "low"
    assert plan["approval"]["canAgentExecuteAutonomously"] is True
    assert plan["diffs"][0]["before"] == {"operatorNote": None}
    assert plan["diffs"][0]["after"] == {"operatorNote": "Urgent handling"}
    assert plan["isDryRun"] is False
    assert dry_run["isDryRun"] is True
    unchanged = foundry.objects.get("Order", "O-1001", ctx=ctx)
    assert unchanged["objectVersion"] == version
    assert unchanged["properties"].get("operatorNote") is None


def test_interface_action_resolves_each_concrete_implementer_and_commits_through_one_contract(
    foundry: FoundryLite, tmp_path: Path
) -> None:
    ctx = _prepare_demo(foundry, _interface_ontology(tmp_path))
    customer = foundry.objects.get("Customer", "C-100", ctx=ctx)

    plan = foundry.actions.plan(
        "SetAssetRisk",
        object_type="Customer",
        object_id="C-100",
        expected_object_version=customer["objectVersion"],
        params={"riskScore": 0.17},
        ctx=ctx,
    )
    assert plan["target"]["objectType"] == "Customer"
    assert plan["editManifest"]["objectModifies"][0]["objectType"] == "Customer"

    result = foundry.actions.apply(
        "SetAssetRisk",
        object_type="Customer",
        object_id="C-100",
        expected_object_version=customer["objectVersion"],
        params={"riskScore": 0.17},
        idempotency_key="interface-customer-risk",
        ctx=ctx,
    )
    assert result["status"] == "succeeded"
    assert foundry.objects.get("Customer", "C-100", ctx=ctx)["properties"]["riskScore"] == 0.17


def test_branch_action_commits_overlay_and_preserves_main(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = _prepare_v3_demo(foundry, tmp_path)
    branch = foundry.ontology.create_branch(name="expedite-scenario", idempotency_key="branch-expedite", ctx=ctx)
    branch_id = str(branch["id"])
    main_before = foundry.objects.get("Order", "O-1001", ctx=ctx)

    result = foundry.actions.execute_branch(
        "ExpediteOrder",
        branch_id=branch_id,
        object_type="Order",
        object_id="O-1001",
        expected_object_version=main_before["objectVersion"],
        params={"mode": "urgent"},
        idempotency_key="branch-expedite-order",
        ctx=ctx,
    )
    assert result["status"] == "succeeded"
    overlay = foundry.actions.branch_object(branch_id, "Order", "O-1001", ctx=ctx)
    assert overlay["properties"]["operatorNote"] == "Urgent handling"
    assert overlay["objectVersion"] == main_before["objectVersion"] + 1

    replay = foundry.actions.execute_branch(
        "ExpediteOrder",
        branch_id=branch_id,
        object_type="Order",
        object_id="O-1001",
        expected_object_version=main_before["objectVersion"],
        params={"mode": "urgent"},
        idempotency_key="branch-expedite-order",
        ctx=ctx,
    )
    assert replay["actionRunId"] == result["actionRunId"]
    assert replay["idempotentReplay"] is True
    assert foundry.actions.branch_diff(branch_id, ctx=ctx)["editCount"] == 1

    second = foundry.actions.execute_branch(
        "ExpediteOrder",
        branch_id=branch_id,
        object_type="Order",
        object_id="O-1001",
        expected_object_version=overlay["objectVersion"],
        params={"mode": "standard"},
        idempotency_key="branch-expedite-order-again",
        ctx=ctx,
    )
    assert second["status"] == "succeeded"
    updated_overlay = foundry.actions.branch_object(branch_id, "Order", "O-1001", ctx=ctx)
    assert updated_overlay["properties"]["operatorNote"] == "Standard handling"
    assert updated_overlay["objectVersion"] == overlay["objectVersion"] + 1

    main_after = foundry.objects.get("Order", "O-1001", ctx=ctx)
    assert main_after["objectVersion"] == main_before["objectVersion"]
    assert main_after["properties"].get("operatorNote") is None
    diff = foundry.actions.branch_diff(branch_id, ctx=ctx)
    assert diff["editCount"] == 2
    assert diff["items"][0]["hasMainDrift"] is False
