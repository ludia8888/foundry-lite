"""Public-path proof for Action Contract v3 discovery and execution."""

from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path

import yaml
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.osdk_actions import action_type
from foundry_lite.domain.action_runtime.action_execution_plan import edit_plan_from_manifest
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ConflictDetected, NotFound, PermissionDenied, ValidationFailed
from foundry_lite.infrastructure import schema as db
from pytest import raises
from sqlalchemy import func, select, update

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
      viewRoles: [ops_manager, data_engineer]
      editRoles: [data_engineer]
      applyRoles: [ops_manager]
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

_INTERFACE_LINK_ACTIONS = """
  - apiName: CreateAsset
    contractVersion: 3
    displayName: Create asset
    target: Asset
    targetKind: interface
    riskLevel: medium
    agentExecutionPolicy: approval_required
    permissions:
      allowedRoles: [ops_manager]
    parameters:
      - apiName: riskScore
        type: float
        required: true
    rules:
      - kind: createObject
        ruleId: create-asset
        objectType: Asset
        onInterface: Asset
        primaryKey: {kind: parameter, parameter: __target__}
        assignments:
          - property: riskScore
            value: {kind: parameter, parameter: riskScore}
  - apiName: RemoveAssetCustomer
    contractVersion: 3
    displayName: Remove asset customer
    target: Asset
    targetKind: interface
    riskLevel: medium
    agentExecutionPolicy: approval_required
    permissions:
      allowedRoles: [ops_manager]
    parameters:
      - apiName: customer
        type: object
        objectType: Customer
        required: true
    rules:
      - kind: deleteLink
        ruleId: unlink-customer
        onInterface: Asset
        interfaceLinkConstraint: customer
        source: {kind: parameter, parameter: __target__}
        target: {kind: parameter, parameter: customer}
  - apiName: SetAssetCustomer
    contractVersion: 3
    displayName: Set asset customer
    target: Asset
    targetKind: interface
    riskLevel: medium
    agentExecutionPolicy: approval_required
    permissions:
      allowedRoles: [ops_manager]
    parameters:
      - apiName: customer
        type: object
        objectType: Customer
        required: true
    rules:
      - kind: createLink
        ruleId: link-customer
        onInterface: Asset
        interfaceLinkConstraint: customer
        source: {kind: parameter, parameter: __target__}
        target: {kind: parameter, parameter: customer}
"""

_MEDIA_ACTION = """
  - apiName: AttachOrderReceipt
    contractVersion: 3
    displayName: Attach order receipt
    target: Order
    riskLevel: medium
    agentExecutionPolicy: approval_required
    revert: {enabled: true}
    permissions:
      allowedRoles: [ops_manager]
    parameters:
      - apiName: receipt
        type: attachment
        required: true
        mediaSet: legal.receipts
        allowedMimeTypes: [application/pdf]
        maxBytes: 209715200
        render: filePicker
    rules:
      - kind: modifyObject
        ruleId: attach-receipt
        objectType: Order
        target: {kind: parameter, parameter: __target__}
        assignments:
          - property: receipt
            value: {kind: parameter, parameter: receipt}
"""

_LINKED_CRITERIA_ACTION = """
  - apiName: ExpediteEnterpriseOrder
    contractVersion: 3
    displayName: Expedite enterprise order
    target: Order
    riskLevel: low
    agentExecutionPolicy: autonomous
    branchPolicy:
      enabled: true
    permissions:
      allowedRoles: [ops_manager]
    submissionCriteria:
      all:
        - op: eq
          left: {kind: objectProperty, property: status}
          right: {kind: literal, value: PENDING}
        - op: contains
          left:
            kind: linkedObjectProperty
            linkType: OrderCustomer
            direction: outgoing
            property: segment
          right: {kind: literal, value: enterprise}
        - op: gte
          left:
            kind: linkedObjectProperty
            linkType: OrderCustomer
            direction: outgoing
            property: customerId
            aggregation: count
          right: {kind: literal, value: 1}
        - op: contains
          left: {kind: currentUser, attribute: groups}
          right: {kind: literal, value: ops_manager}
    rules:
      - kind: modifyObject
        ruleId: expedite-enterprise
        objectType: Order
        target: {kind: parameter, parameter: __target__}
        assignments:
          - property: operatorNote
            value: {kind: literal, value: Enterprise expedited}
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
    source = source.replace(
        "        indexed: true\nobjectTypes:",
        "        indexed: true\n"
        "    linkConstraints:\n"
        "      - apiName: customer\n"
        "        displayName: Customer\n"
        "        targetKind: object\n"
        "        target: Customer\n"
        "        cardinality: one\n"
        "        required: false\n"
        "objectTypes:",
        1,
    )
    target = tmp_path / "order-customer-interface-action.yaml"
    target.write_text(source + _INTERFACE_ACTION + _INTERFACE_LINK_ACTIONS, encoding="utf-8")
    return target


def _media_ontology(tmp_path: Path) -> Path:
    source = (DEMO_ROOT / "ontology" / "order-customer.yaml").read_text(encoding="utf-8")
    source = source.replace(
        "        editPolicy: edit_only\n  - apiName: Customer",
        "        editPolicy: edit_only\n"
        "      - apiName: receipt\n"
        "        type: attachment\n"
        "        mediaSet: legal.receipts\n"
        "        editable: true\n"
        "        source: edit_layer\n"
        "        editPolicy: edit_only\n"
        "  - apiName: Customer",
        1,
    )
    target = tmp_path / "order-customer-media-action.yaml"
    target.write_text(source + _MEDIA_ACTION, encoding="utf-8")
    return target


def _linked_criteria_ontology(tmp_path: Path) -> Path:
    source = (DEMO_ROOT / "ontology" / "order-customer.yaml").read_text(encoding="utf-8")
    target = tmp_path / "order-customer-linked-criteria.yaml"
    target.write_text(source + _LINKED_CRITERIA_ACTION, encoding="utf-8")
    return target


def _linked_masked_criteria_ontology(tmp_path: Path) -> Path:
    source = (DEMO_ROOT / "ontology" / "order-customer.yaml").read_text(encoding="utf-8")
    source = source.replace(
        "      - apiName: segment\n        column: segment\n        type: string",
        "      - apiName: segment\n        column: segment\n        type: string\n        classification: pii",
        1,
    )
    target = tmp_path / "order-customer-linked-masked-criteria.yaml"
    target.write_text(source + _LINKED_CRITERIA_ACTION, encoding="utf-8")
    return target


def _linked_async_criteria_ontology(tmp_path: Path) -> Path:
    payload = yaml.safe_load((DEMO_ROOT / "ontology" / "order-customer.yaml").read_text(encoding="utf-8"))
    payload["functionTypes"].append(_linked_criteria_function())
    action = yaml.safe_load(f"actionTypes:\n{_LINKED_CRITERIA_ACTION}")["actionTypes"][0]
    action["apiName"] = "ExpediteEnterpriseOrderAsync"
    action["riskLevel"] = "high"
    action["agentExecutionPolicy"] = "approval_required"
    action.pop("rules")
    action["function"] = {"apiName": "enterpriseOrderEdits", "version": "1.0.0"}
    payload["actionTypes"].append(action)
    target = tmp_path / "order-customer-linked-criteria-async.yaml"
    target.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return target


def _linked_criteria_function() -> dict[str, object]:
    return {
        "apiName": "enterpriseOrderEdits",
        "version": "1.0.0",
        "runtime": "logic_dag",
        "inputs": [],
        "output": {"type": "ontology_edit_batch"},
        "permissions": {"allowedRoles": ["ops_manager"]},
        "definition": {
            "tools": [],
            "blocks": [
                {
                    "blockId": "batch",
                    "kind": "Input",
                    "inputs": {
                        "edits": [
                            {
                                "kind": "modifyObject",
                                "objectType": "Order",
                                "objectId": "O-1001",
                                "expectedVersion": 1,
                                "patch": {"operatorNote": "placeholder"},
                            }
                        ],
                        "readSetVersions": {"Order:O-1001": 1},
                        "provenance": {"adapter": "logic_dag"},
                    },
                },
                {
                    "blockId": "output",
                    "kind": "Output",
                    "dependsOn": ["batch"],
                    "inputs": {"fromBlock": "batch"},
                },
            ],
        },
    }


def _ambiguous_interface_link_ontology(tmp_path: Path) -> Path:
    source = _interface_ontology(tmp_path).read_text(encoding="utf-8")
    source = source.replace(
        "functionTypes:\n",
        "  - apiName: OrderCustomerSecondary\n"
        "    displayName: Secondary order customer\n"
        "    from: Order\n"
        "    to: Customer\n"
        "    cardinality: many_to_one\n"
        "    backing:\n"
        "      dataset: clean.orders\n"
        "      fromKey: order_id\n"
        "      toKey: customer_id\n"
        "functionTypes:\n",
        1,
    )
    target = tmp_path / "order-customer-interface-link-ambiguous.yaml"
    target.write_text(source, encoding="utf-8")
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
    assert action["access"] == {"canView": True, "canEdit": True, "canApply": True}
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


def test_action_view_edit_apply_roles_filter_catalog_and_execution_independently(
    foundry: FoundryLite, tmp_path: Path
) -> None:
    admin = _prepare_v3_demo(foundry, tmp_path)
    ops = RequestContext(tenant_id=admin.tenant_id, actor_user_id="ops", roles=("ops_manager",))
    engineer = RequestContext(
        tenant_id=admin.tenant_id,
        actor_user_id="engineer",
        roles=("data_engineer",),
    )
    viewer = RequestContext(tenant_id=admin.tenant_id, actor_user_id="viewer", roles=("viewer",))

    assert foundry.actions.get("ExpediteOrder", ctx=ops)["access"] == {
        "canView": True,
        "canEdit": False,
        "canApply": True,
    }
    assert foundry.actions.get("ExpediteOrder", ctx=engineer)["access"] == {
        "canView": True,
        "canEdit": True,
        "canApply": False,
    }
    assert "ExpediteOrder" not in {item["apiName"] for item in foundry.actions.list(ctx=viewer)["items"]}
    with raises(PermissionDenied, match="view Action Type"):
        foundry.actions.get("ExpediteOrder", ctx=viewer)

    order = foundry.objects.get("Order", "O-1001", ctx=engineer)
    with raises(PermissionDenied):
        foundry.actions.plan(
            "ExpediteOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"mode": "urgent"},
            ctx=engineer,
        )


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


def test_submission_criteria_returns_a_redacted_per_clause_explanation(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = _prepare_v3_demo(foundry, tmp_path)
    pending = foundry.objects.get("Order", "O-1001", ctx=ctx)
    approved = foundry.objects.get("Order", "O-1003", ctx=ctx)

    passed = foundry.actions.validate(
        "ExpediteOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=pending["objectVersion"],
        params={"mode": "standard"},
        ctx=ctx,
    )
    failed = foundry.actions.validate(
        "ExpediteOrder",
        object_type="Order",
        object_id="O-1003",
        expected_object_version=approved["objectVersion"],
        params={"mode": "standard"},
        ctx=ctx,
    )

    assert passed["submissionCriteriaEvaluation"]["status"] == "PASSED"
    passed_tree = passed["submissionCriteriaEvaluation"]["tree"]
    assert passed_tree is not None
    assert [child["isSatisfied"] for child in passed_tree["children"]] == [True, False]
    assert failed["submissionCriteriaEvaluation"]["status"] == "FAILED"
    failed_tree = failed["submissionCriteriaEvaluation"]["tree"]
    assert failed_tree is not None
    assert [child["isSatisfied"] for child in failed_tree["children"]] == [False, False]
    assert failed_tree["children"][0]["left"] == {
        "kind": "objectProperty",
        "reference": "status",
    }
    assert "APPROVED" not in str(failed["submissionCriteriaEvaluation"])


def test_linked_object_and_group_submission_criteria_use_visible_transaction_snapshot(
    foundry: FoundryLite, tmp_path: Path
) -> None:
    ctx = _prepare_demo(foundry, _linked_criteria_ontology(tmp_path))
    enterprise = foundry.objects.get("Order", "O-1001", ctx=ctx)
    midmarket = foundry.objects.get("Order", "O-1002", ctx=ctx)

    passed = foundry.actions.validate(
        "ExpediteEnterpriseOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=enterprise["objectVersion"],
        params={},
        ctx=ctx,
    )
    failed = foundry.actions.validate(
        "ExpediteEnterpriseOrder",
        object_type="Order",
        object_id="O-1002",
        expected_object_version=midmarket["objectVersion"],
        params={},
        ctx=ctx,
    )

    assert passed["submissionCriteriaEvaluation"]["status"] == "PASSED"
    assert failed["submissionCriteriaEvaluation"]["status"] == "FAILED"
    linked_node = passed["submissionCriteriaEvaluation"]["tree"]["children"][1]
    assert linked_node["left"]["reference"] == {
        "linkType": "OrderCustomer",
        "direction": "outgoing",
        "property": "segment",
        "aggregation": "values",
    }
    assert "Acme Supply" not in str(passed["submissionCriteriaEvaluation"])

    result = foundry.actions.apply(
        "ExpediteEnterpriseOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=enterprise["objectVersion"],
        params={},
        idempotency_key="linked-criteria-enterprise-order",
        ctx=ctx,
    )
    assert result["status"] == "succeeded"
    assert foundry.objects.get("Order", "O-1001", ctx=ctx)["properties"]["operatorNote"] == "Enterprise expedited"


def test_linked_criteria_plan_seals_opaque_read_set_and_rejects_drift(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = _prepare_demo(foundry, _linked_criteria_ontology(tmp_path))
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    plan = foundry.actions.plan(
        "ExpediteEnterpriseOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={},
        ctx=ctx,
    )
    criteria_reads = plan["editManifest"]["criteriaReadSet"]
    assert len(criteria_reads) == 2
    assert all(str(item["snapshotFingerprint"]).startswith("sha256:") for item in criteria_reads)
    assert "enterprise" not in str(criteria_reads)

    with foundry.engine.begin() as transaction:
        transaction.execute(
            update(db.object_records)
            .where(
                db.object_records.c.tenant_id == ctx.tenant_id,
                db.object_records.c.object_type_api_name == "Customer",
                db.object_records.c.object_id == "C-100",
                db.object_records.c.is_active == True,  # noqa: E712
            )
            .values(object_version=db.object_records.c.object_version + 1)
        )
    edit_plan = edit_plan_from_manifest(plan["editManifest"])
    with (
        foundry.engine.begin() as transaction,
        raises(ConflictDetected, match="submission criteria changed after planning"),
    ):
        foundry._services.action.apply._verify_plan_criteria(transaction, ctx, edit_plan)


def test_distributed_function_action_rejects_linked_criteria_drift_before_commit(
    foundry: FoundryLite, tmp_path: Path
) -> None:
    ctx = _prepare_demo(foundry, _linked_async_criteria_ontology(tmp_path))
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    customer = foundry.objects.get("Customer", "C-100", ctx=ctx)

    def concurrent_change(_request) -> dict[str, object]:
        changed = {**customer["properties"], "segment": "consumer"}
        with foundry.engine.begin() as transaction:
            transaction.execute(
                update(db.object_records)
                .where(
                    db.object_records.c.tenant_id == ctx.tenant_id,
                    db.object_records.c.object_type_api_name == "Customer",
                    db.object_records.c.object_id == "C-100",
                    db.object_records.c.is_active == True,  # noqa: E712
                )
                .values(properties=changed, object_version=db.object_records.c.object_version + 1)
            )
        return {
            "output": {
                "edits": [
                    {
                        "kind": "modifyObject",
                        "objectType": "Order",
                        "objectId": "O-1001",
                        "expectedVersion": order["objectVersion"],
                        "patch": {"operatorNote": "must-not-commit"},
                    }
                ],
                "readSetVersions": {"Order:O-1001": order["objectVersion"]},
                "provenance": {"test": "linked-criteria-drift"},
            },
            "logicRunId": "linked-criteria-drift-function",
        }

    foundry._services.action.distributed.action_function_executor.register_driver(concurrent_change)
    run = foundry.actions.start_run(
        "ExpediteEnterpriseOrderAsync",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={},
        idempotency_key="linked-criteria-drift-run",
        wait_seconds=5,
        ctx=ctx,
    )

    assert run["status"] == "conflict"
    assert run["error"]["message"] == "linked-object submission criteria changed after planning"
    assert foundry.objects.get("Order", "O-1001", ctx=ctx)["properties"].get("operatorNote") is None


def test_linked_criteria_reads_branch_link_overlay_instead_of_main(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = _prepare_demo(foundry, _linked_criteria_ontology(tmp_path))
    branch = foundry.ontology.create_branch(
        name="linked-criteria-scenario", idempotency_key="linked-criteria-scenario", ctx=ctx
    )
    branch_id = str(branch["id"])
    foundry.ontology.create_branch_action_type(
        branch_id,
        definition=_branch_link_action("BranchUnlinkCriteriaCustomer", "deleteLink"),
        expected_fingerprint=str(branch["contentFingerprint"]),
        idempotency_key="branch-unlink-criteria-definition",
        ctx=ctx,
    )
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    foundry.actions.execute_branch(
        "BranchUnlinkCriteriaCustomer",
        branch_id=branch_id,
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"customerId": "C-100"},
        idempotency_key="branch-unlink-criteria-customer",
        ctx=ctx,
    )

    main_plan = foundry.actions.plan(
        "ExpediteEnterpriseOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={},
        ctx=ctx,
    )
    assert main_plan["planHash"].startswith("sha256:")
    with raises(ValidationFailed, match="submission criteria failed"):
        foundry.actions.plan(
            "ExpediteEnterpriseOrder",
            branch_id=branch_id,
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={},
            ctx=ctx,
        )


def test_linked_submission_criteria_intersects_token_app_link_and_object_scopes(
    foundry: FoundryLite, tmp_path: Path
) -> None:
    ctx = _prepare_demo(foundry, _linked_criteria_ontology(tmp_path))
    scopes = (
        "osdk:action:ExpediteEnterpriseOrder:validate",
        "osdk:object:Order:read",
        "osdk:link:OrderCustomer:read",
        "osdk:object:Customer:read",
    )
    app = foundry.developer_console.create_osdk_application(
        app_api_name="linkedCriteriaClient",
        display_name="Linked criteria client",
        client_id="linked-criteria-client",
        resources=[
            {"resourceType": "action", "resourceApiName": "ExpediteEnterpriseOrder", "scopes": [scopes[0]]},
            {"resourceType": "object", "resourceApiName": "Order", "scopes": [scopes[1]]},
            {"resourceType": "link", "resourceApiName": "OrderCustomer", "scopes": [scopes[2]]},
            {"resourceType": "object", "resourceApiName": "Customer", "scopes": [scopes[3]]},
        ],
        idempotency_key="create-linked-criteria-client",
        ctx=ctx,
    )
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    scoped = replace(
        ctx,
        application_id=str(app["application"]["id"]),
        client_id="linked-criteria-client",
    )

    with raises(PermissionDenied, match="OSDK application scope denied"):
        foundry.actions.validate(
            "ExpediteEnterpriseOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={},
            ctx=replace(scoped, token_scopes=scopes[:2]),
        )
    with raises(PermissionDenied, match="OSDK application scope denied"):
        foundry.actions.validate(
            "ExpediteEnterpriseOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={},
            ctx=replace(scoped, token_scopes=scopes[:3]),
        )

    allowed = foundry.actions.validate(
        "ExpediteEnterpriseOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={},
        ctx=replace(scoped, token_scopes=scopes),
    )
    assert allowed["result"] == "VALID"


def test_linked_submission_criteria_cannot_infer_a_masked_property(foundry: FoundryLite, tmp_path: Path) -> None:
    admin = _prepare_demo(foundry, _linked_masked_criteria_ontology(tmp_path))
    ctx = replace(
        admin,
        actor_user_id="linked-criteria-operator",
        request_id="linked-criteria-masked-property",
        roles=("ops_manager",),
    )
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)

    with raises(PermissionDenied, match="masked property"):
        foundry.actions.validate(
            "ExpediteEnterpriseOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={},
            ctx=ctx,
        )


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


def test_python_osdk_interface_action_requires_and_forwards_concrete_object_type(
    foundry: FoundryLite, tmp_path: Path
) -> None:
    ctx = _prepare_demo(foundry, _interface_ontology(tmp_path))
    customer = foundry.objects.get("Customer", "C-100", ctx=ctx)
    action = action_type(
        "SetAssetRisk",
        target_object_type="Asset",
        target_kind="interface",
        parameter_names=("riskScore",),
        required_parameters=("riskScore",),
    )
    invoker = foundry(action, ctx=ctx)

    plan = invoker.plan_action(
        {"riskScore": 0.23},
        object_type="Customer",
        object_id="C-100",
        expected_object_version=customer["objectVersion"],
    )

    assert plan["target"]["objectType"] == "Customer"
    assert plan["target"]["objectId"] == "C-100"
    assert plan["target"]["expectedObjectVersion"] == customer["objectVersion"]
    with raises(ValidationFailed, match="requires object_type"):
        invoker.validate_action(
            {"riskScore": 0.23},
            object_id="C-100",
            expected_object_version=customer["objectVersion"],
        )


def test_interface_create_selects_concrete_type_and_uses_version_zero_target_contract(
    foundry: FoundryLite, tmp_path: Path
) -> None:
    ctx = _prepare_demo(foundry, _interface_ontology(tmp_path))
    action_names = {item["apiName"] for item in foundry.actions.list(ctx=ctx)["items"]}
    assert {"CreateAsset", "RemoveAssetCustomer", "SetAssetCustomer", "SetAssetRisk"} <= action_names
    log_names = {
        item["apiName"]
        for item in foundry.ontology.catalog(ctx=ctx)["objectTypes"]
        if item["apiName"].startswith("[LOG] ")
    }
    assert "[LOG] CreateAsset" in log_names

    validation = foundry.actions.validate(
        "CreateAsset",
        object_type="Customer",
        object_id="C-NEW",
        expected_object_version=0,
        params={"riskScore": 0.42},
        ctx=ctx,
    )
    plan = foundry.actions.plan(
        "CreateAsset",
        object_type="Customer",
        object_id="C-NEW",
        expected_object_version=0,
        params={"riskScore": 0.42},
        ctx=ctx,
    )
    assert validation["result"] == "VALID"
    assert plan["target"] == {
        "objectType": "Customer",
        "objectId": "C-NEW",
        "expectedObjectVersion": 0,
        "readObjectVersion": 0,
    }
    assert plan["editManifest"]["objectCreates"][0]["objectType"] == "Customer"
    assert plan["editManifest"]["objectCreates"][0]["objectId"] == "C-NEW"

    created = foundry.actions.apply(
        "CreateAsset",
        object_type="Customer",
        object_id="C-NEW",
        expected_object_version=0,
        params={"riskScore": 0.42},
        idempotency_key="interface-create-c-new",
        ctx=ctx,
    )
    assert created["status"] == "succeeded"
    assert created["plan"]["createdObjectIds"] == ["C-NEW"]
    customer = foundry.objects.get("Customer", "C-NEW", ctx=ctx)
    assert customer["properties"]["riskScore"] == 0.42
    foundry.objects.reindex("Customer", ctx=ctx)
    reindexed_customer = foundry.objects.get("Customer", "C-NEW", ctx=ctx)
    assert reindexed_customer["properties"]["riskScore"] == 0.42
    assert reindexed_customer["objectVersion"] == customer["objectVersion"]

    with raises(ConflictDetected):
        foundry.actions.apply(
            "CreateAsset",
            object_type="Customer",
            object_id="C-NEW",
            expected_object_version=0,
            params={"riskScore": 0.9},
            idempotency_key="interface-create-c-new-conflict",
            ctx=ctx,
        )


def test_interface_link_actions_resolve_constraint_to_concrete_link_and_accept_typed_reference(
    foundry: FoundryLite, tmp_path: Path
) -> None:
    ctx = _prepare_demo(foundry, _interface_ontology(tmp_path))
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    typed_customer = {"objectType": "Customer", "objectId": "C-100"}

    removed = foundry.actions.apply(
        "RemoveAssetCustomer",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"customer": typed_customer},
        idempotency_key="interface-unlink-customer",
        ctx=ctx,
    )
    assert removed["status"] == "succeeded"
    assert foundry.objects.links("Order", "O-1001", "OrderCustomer", ctx=ctx) == []

    restored = foundry.actions.apply(
        "SetAssetCustomer",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"customer": typed_customer},
        idempotency_key="interface-link-customer",
        ctx=ctx,
    )
    assert restored["status"] == "succeeded"
    assert [item["to"]["objectId"] for item in foundry.objects.links("Order", "O-1001", "OrderCustomer", ctx=ctx)] == [
        "C-100"
    ]


def test_interface_link_create_rejects_multiple_implementations_and_delete_removes_all(
    foundry: FoundryLite, tmp_path: Path
) -> None:
    ctx = _prepare_demo(foundry, _ambiguous_interface_link_ontology(tmp_path))
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    customer = {"objectType": "Customer", "objectId": "C-100"}

    with raises(ValidationFailed, match="exactly one concrete implementation"):
        foundry.actions.plan(
            "SetAssetCustomer",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"customer": customer},
            ctx=ctx,
        )

    removed = foundry.actions.apply(
        "RemoveAssetCustomer",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"customer": customer},
        idempotency_key="interface-delete-all-customer-links",
        ctx=ctx,
    )
    assert removed["status"] == "succeeded"
    assert removed["plan"]["editCount"] == 2
    assert foundry.objects.links("Order", "O-1001", "OrderCustomer", ctx=ctx) == []
    assert foundry.objects.links("Order", "O-1001", "OrderCustomerSecondary", ctx=ctx) == []


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


def test_branch_authored_action_executes_without_activation_and_pins_definition(
    foundry: FoundryLite, tmp_path: Path
) -> None:
    ctx = _prepare_v3_demo(foundry, tmp_path)
    branch = foundry.ontology.create_branch(
        name="branch-authored-action", idempotency_key="branch-authored-action", ctx=ctx
    )
    branch_id = str(branch["id"])
    definition = _branch_only_action("Branch note v1")
    created = foundry.ontology.create_branch_action_type(
        branch_id,
        definition=definition,
        expected_fingerprint=str(branch["contentFingerprint"]),
        idempotency_key="create-branch-only-action",
        ctx=ctx,
    )
    with raises(NotFound, match="action type not found"):
        foundry.actions.get("BranchSetOperatorNote", ctx=ctx)

    main = foundry.objects.get("Order", "O-1001", ctx=ctx)
    result = foundry.actions.execute_branch(
        "BranchSetOperatorNote",
        branch_id=branch_id,
        object_type="Order",
        object_id="O-1001",
        expected_object_version=main["objectVersion"],
        params={"note": "branch-only"},
        idempotency_key="branch-only-run",
        ctx=ctx,
    )
    overlay = foundry.actions.branch_object(branch_id, "Order", "O-1001", ctx=ctx)
    assert result["status"] == "succeeded"
    assert overlay["properties"]["operatorNote"] == "branch-only"
    assert foundry.objects.get("Order", "O-1001", ctx=ctx)["properties"].get("operatorNote") is None

    foundry.ontology.update_branch_action_type(
        branch_id,
        "BranchSetOperatorNote",
        definition=_branch_only_action("Branch note v2"),
        expected_fingerprint=str(created["branch"]["contentFingerprint"]),
        idempotency_key="update-branch-only-action",
        ctx=ctx,
    )
    with raises(ConflictDetected, match="idempotency key"):
        foundry.actions.execute_branch(
            "BranchSetOperatorNote",
            branch_id=branch_id,
            object_type="Order",
            object_id="O-1001",
            expected_object_version=main["objectVersion"],
            params={"note": "branch-only"},
            idempotency_key="branch-only-run",
            ctx=ctx,
        )


def test_branch_actions_overlay_link_create_and_delete_without_changing_main(
    foundry: FoundryLite, tmp_path: Path
) -> None:
    ctx = _prepare_v3_demo(foundry, tmp_path)
    branch = foundry.ontology.create_branch(name="link-scenario", idempotency_key="branch-link-scenario", ctx=ctx)
    branch_id = str(branch["id"])
    created = foundry.ontology.create_branch_action_type(
        branch_id,
        definition=_branch_link_action("BranchLinkCustomer", "createLink"),
        expected_fingerprint=str(branch["contentFingerprint"]),
        idempotency_key="branch-link-create-definition",
        ctx=ctx,
    )
    foundry.ontology.create_branch_action_type(
        branch_id,
        definition=_branch_link_action("BranchUnlinkCustomer", "deleteLink"),
        expected_fingerprint=str(created["branch"]["contentFingerprint"]),
        idempotency_key="branch-link-delete-definition",
        ctx=ctx,
    )
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)

    linked = foundry.actions.execute_branch(
        "BranchLinkCustomer",
        branch_id=branch_id,
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"customerId": "C-101"},
        idempotency_key="branch-create-o1001-c101",
        ctx=ctx,
    )
    unlinked = foundry.actions.execute_branch(
        "BranchUnlinkCustomer",
        branch_id=branch_id,
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"customerId": "C-100"},
        idempotency_key="branch-delete-o1001-c100",
        ctx=ctx,
    )

    assert linked["result"]["editCount"] == 1
    assert unlinked["result"]["editCount"] == 1
    created_link = foundry.actions.branch_link(branch_id, "OrderCustomer", "O-1001", "C-101", ctx=ctx)
    deleted_link = foundry.actions.branch_link(branch_id, "OrderCustomer", "O-1001", "C-100", ctx=ctx)
    assert created_link["isDeleted"] is False and created_link["baseLinkVersion"] is None
    assert deleted_link["isDeleted"] is True and deleted_link["baseLinkVersion"] == 1
    main_targets = {
        item["to"]["objectId"] for item in foundry.objects.links("Order", "O-1001", "OrderCustomer", ctx=ctx)
    }
    assert main_targets == {"C-100"}
    diff = foundry.actions.branch_diff(branch_id, ctx=ctx)
    assert diff["items"] == []
    assert len(diff["linkItems"]) == 2
    assert diff["editCount"] == 2
    assert all(item["hasMainDrift"] is False for item in diff["linkItems"])


def test_action_attachment_upload_is_canonicalized_bound_and_retention_protected(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    foundry.media.create_media_set(
        ctx,
        namespace="legal",
        name="receipts",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        classification="internal",
    )
    _prepare_demo(foundry, _media_ontology(tmp_path))
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    uploaded = foundry.actions.upload_parameter(
        "AttachOrderReceipt",
        "receipt",
        object_type="Order",
        object_id="O-1001",
        file_name="receipt.pdf",
        source=io.BytesIO(b"%PDF-1.4 receipt"),
        supplied_mime_type="application/pdf",
        idempotency_key="upload-order-receipt-1",
        ctx=ctx,
    )
    version_id = str(uploaded["reference"]["mediaItemVersionId"])
    assert uploaded["malwareScan"]["verdict"] == "clean"
    assert uploaded["malwareScan"]["scanner"] == "local-signature"
    tampered = {**uploaded["reference"], "contentHash": "client-controlled"}

    with raises(NotFound):
        foundry.media.resolve_reference(ctx, media_item_version_id=version_id)
    plan = foundry.actions.plan(
        "AttachOrderReceipt",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"receipt": tampered},
        ctx=ctx,
    )
    canonical = plan["parameters"]["receipt"]
    assert isinstance(canonical, dict)
    assert canonical["contentHash"] != "client-controlled"
    result = foundry.actions.apply(
        "AttachOrderReceipt",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"receipt": version_id},
        idempotency_key="attach-order-receipt-1",
        ctx=ctx,
    )

    updated = foundry.objects.get("Order", "O-1001", ctx=ctx)
    assert result["status"] == "succeeded"
    assert updated["properties"]["receipt"]["mediaItemVersionId"] == version_id
    assert foundry.media.resolve_reference(ctx, media_item_version_id=version_id).version.status == "COMMITTED"
    with foundry.engine.begin() as transaction:
        binding = (
            transaction.execute(
                select(db.media_reference_bindings).where(
                    db.media_reference_bindings.c.media_item_version_id == version_id
                )
            )
            .mappings()
            .one()
        )
        version = (
            transaction.execute(select(db.media_item_versions).where(db.media_item_versions.c.id == version_id))
            .mappings()
            .one()
        )
    assert binding["holder_type"] == "Order"
    assert binding["holder_id"] == "O-1001"
    assert binding["property_name"] == "receipt"
    assert version["retention_marked_at"] is not None
    assert version["security_envelope"]["malwareScan"]["verdict"] == "clean"

    reverted = foundry.actions.revert(str(result["actionRunId"]), idempotency_key="revert-order-receipt-1", ctx=ctx)
    assert reverted["status"] == "succeeded"
    assert "receipt" not in foundry.objects.get("Order", "O-1001", ctx=ctx)["properties"]
    with raises(NotFound):
        foundry.media.resolve_reference(ctx, media_item_version_id=version_id)


def test_action_attachment_upload_rejects_malware_before_staging(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    foundry.media.create_media_set(
        ctx,
        namespace="legal",
        name="receipts",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        classification="internal",
    )
    _prepare_demo(foundry, _media_ontology(tmp_path))

    with raises(ValidationFailed, match="rejected by malware scanning") as raised:
        foundry.actions.upload_parameter(
            "AttachOrderReceipt",
            "receipt",
            object_type="Order",
            object_id="O-1001",
            file_name="malicious.pdf",
            source=io.BytesIO(b"%PDF EICAR-STANDARD-ANTIVIRUS-TEST-FILE"),
            supplied_mime_type="application/pdf",
            idempotency_key="upload-rejected-malware-1",
            ctx=ctx,
        )

    assert raised.value.details["verdict"] == "infected"
    with foundry.engine.begin() as transaction:
        media_transaction_count = transaction.execute(
            select(func.count()).select_from(db.media_transactions)
        ).scalar_one()
        rejection = (
            transaction.execute(
                select(db.audit_events).where(db.audit_events.c.event_type == "action.media.scan.rejected")
            )
            .mappings()
            .one()
        )
    assert media_transaction_count == 0
    assert rejection["decision"] == "deny"
    assert rejection["after_ref"]["threatName"] == "Eicar-Test-Signature"


def _branch_only_action(display_name: str) -> dict[str, object]:
    return {
        "apiName": "BranchSetOperatorNote",
        "contractVersion": 3,
        "displayName": display_name,
        "target": "Order",
        "riskLevel": "low",
        "agentExecutionPolicy": "approval_required",
        "branchPolicy": {"enabled": True},
        "permissions": {"allowedRoles": ["ops_manager"]},
        "parameters": [{"apiName": "note", "type": "string", "required": True}],
        "rules": [
            {
                "kind": "modifyObject",
                "ruleId": "set-branch-note",
                "objectType": "Order",
                "target": {"kind": "parameter", "parameter": "__target__"},
                "assignments": [
                    {
                        "property": "operatorNote",
                        "value": {"kind": "parameter", "parameter": "note"},
                    }
                ],
            }
        ],
    }


def _branch_link_action(api_name: str, kind: str) -> dict[str, object]:
    return {
        "apiName": api_name,
        "contractVersion": 3,
        "displayName": api_name,
        "target": "Order",
        "riskLevel": "medium",
        "agentExecutionPolicy": "approval_required",
        "branchPolicy": {"enabled": True},
        "permissions": {"allowedRoles": ["ops_manager"]},
        "parameters": [{"apiName": "customerId", "type": "string", "required": True}],
        "rules": [
            {
                "kind": kind,
                "ruleId": "branch-link-edit",
                "linkType": "OrderCustomer",
                "source": {"kind": "parameter", "parameter": "__target__"},
                "target": {"kind": "parameter", "parameter": "customerId"},
            }
        ],
    }
