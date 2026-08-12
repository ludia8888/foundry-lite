"""True ontology branching: isolated editing, three-way diff, rebase, and merge-by-proposal."""

from __future__ import annotations

from typing import Any, cast

import yaml
from fastapi.testclient import TestClient
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.services.ontology_branch_diff import (
    evaluate_branch_diff,
    merge_branch_onto_main,
    parse_resource_map,
    serialize_definition_yaml,
    unknown_resolutions,
    unresolved_conflicts,
)
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import (
    ConflictDetected,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app
from pytest import MonkeyPatch, raises

ORDERS_CSV = "order_id,status\nO-1,PENDING\n"
CUSTOMERS_CSV = "customer_id,region\nC-1,EMEA\n"

CREATOR = RequestContext(actor_user_id="user-creator", roles=("data_engineer",))
COLLABORATOR = RequestContext(actor_user_id="user-collaborator", roles=("data_engineer",))
REVIEWER = RequestContext(actor_user_id="user-reviewer", roles=("data_engineer",))
VIEWER = RequestContext(actor_user_id="user-viewer", roles=("viewer",))

CREATOR_HEADERS = {"X-Tenant-ID": "tenant-demo", "X-User-ID": "user-creator", "X-Roles": "data_engineer"}
REVIEWER_HEADERS = {"X-Tenant-ID": "tenant-demo", "X-User-ID": "user-reviewer", "X-Roles": "data_engineer"}
VIEWER_HEADERS = {"X-Tenant-ID": "tenant-demo", "X-User-ID": "user-viewer", "X-Roles": "viewer"}


def _order_type(*, extra_properties: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "apiName": "Order",
        "primaryKey": "orderId",
        "backing": {"dataset": "clean.orders"},
        "properties": [
            {"apiName": "orderId", "column": "order_id", "type": "string", "nullable": False, "indexed": True},
            {
                "apiName": "status",
                "column": "status",
                "type": "string",
                "editable": True,
                "source": "edit_layer",
            },
            *(extra_properties or []),
        ],
    }


def _customer_type() -> dict[str, object]:
    return {
        "apiName": "Customer",
        "primaryKey": "customerId",
        "backing": {"dataset": "clean.customers"},
        "properties": [
            {"apiName": "customerId", "column": "customer_id", "type": "string", "nullable": False, "indexed": True},
            {"apiName": "region", "column": "region", "type": "string"},
        ],
    }


def _note_property(api_name: str = "note") -> dict[str, object]:
    return {"apiName": api_name, "type": "string", "source": "edit_layer"}


def _branch_action_type(*, display_name: str = "Set order status") -> dict[str, object]:
    return {
        "contractVersion": 3,
        "apiName": "SetOrderStatus",
        "displayName": display_name,
        "description": "Update one order through the governed Action runtime.",
        "target": "Order",
        "targetKind": "object",
        "riskLevel": "low",
        "agentExecutionPolicy": "approval_required",
        "permissions": {
            "viewRoles": ["viewer", "data_engineer"],
            "editRoles": ["data_engineer"],
            "applyRoles": ["data_engineer"],
        },
        "parameters": [{"apiName": "status", "type": "string", "required": True}],
        "formLayout": {
            "sections": [
                {
                    "id": "decision",
                    "title": "Decision",
                    "columns": 2,
                    "parameterNames": ["status"],
                }
            ]
        },
        "rules": [
            {
                "kind": "modifyObject",
                "ruleId": "set-order-status",
                "objectType": "Order",
                "target": {"kind": "parameter", "parameter": "__target__"},
                "assignments": [
                    {
                        "property": "status",
                        "value": {"kind": "parameter", "parameter": "status"},
                    }
                ],
            }
        ],
    }


def _yaml_text(*object_types: dict[str, object]) -> str:
    return yaml.safe_dump({"objectTypes": list(object_types)}, sort_keys=False)


def _prepare_active_ontology(foundry: FoundryLite, tmp_path: Any) -> RequestContext:
    ctx = demo_admin_context()
    orders_csv = tmp_path / "orders.csv"
    orders_csv.write_text(ORDERS_CSV, encoding="utf-8")
    customers_csv = tmp_path / "customers.csv"
    customers_csv.write_text(CUSTOMERS_CSV, encoding="utf-8")
    foundry.datasets.ensure("clean.orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("clean.orders", str(orders_csv), ctx=ctx)
    foundry.datasets.ensure("clean.customers", ctx=ctx, primary_key=["customer_id"])
    foundry.datasets.upload_csv("clean.customers", str(customers_csv), ctx=ctx)
    foundry.ontology.apply_text(_yaml_text(_order_type()), ctx=ctx)
    return ctx


def _create_branch(foundry: FoundryLite, *, name: str = "feature-a", key: str = "branch-key-1") -> dict[str, object]:
    return foundry.ontology.create_branch(name=name, idempotency_key=key, ctx=CREATOR)


def _diff_entry(diff: dict[str, object], kind: str, api_name: str) -> dict[str, object]:
    for entry in cast(list[dict[str, object]], diff["resources"]):
        if entry["kind"] == kind and entry["apiName"] == api_name:
            return entry
    raise AssertionError(f"no diff entry for {kind}/{api_name}: {diff['resources']}")


def _properties(yaml_text: str, api_name: str) -> set[str]:
    definition = yaml.safe_load(yaml_text)
    for object_type in definition["objectTypes"]:
        if object_type["apiName"] == api_name:
            return {prop["apiName"] for prop in object_type["properties"]}
    raise AssertionError(f"no object type {api_name} in {yaml_text}")


def test_full_branch_lifecycle_merges_through_proposal_and_updates_catalog(foundry, tmp_path) -> None:
    admin = _prepare_active_ontology(foundry, tmp_path)
    branch = _create_branch(foundry)
    assert branch["status"] == "open"
    assert branch["baseVersionNumber"] == 1
    assert branch["baseStale"] is False
    branch_id = str(branch["id"])

    updated = foundry.ontology.update_branch(
        branch_id,
        yaml_text=_yaml_text(_order_type(extra_properties=[_note_property()]), _customer_type()),
        expected_fingerprint=str(branch["contentFingerprint"]),
        ctx=COLLABORATOR,  # branches are collaborative: any validate holder edits
    )
    assert updated["contentFingerprint"] != branch["contentFingerprint"]

    diff = foundry.ontology.branch_diff(branch_id, ctx=CREATOR)
    assert diff["baseStale"] is False
    assert diff["conflicts"] == []
    assert _diff_entry(diff, "objectType", "Customer")["branchChange"] == "added"
    assert _diff_entry(diff, "objectType", "Order")["branchChange"] == "modified"
    assert cast(dict[str, Any], diff["migrationPlan"])["status"] == "compatible"

    proposed = foundry.ontology.propose_branch(
        branch_id, title="Add customers", idempotency_key="propose-key-1", ctx=CREATOR
    )
    proposal = cast(dict[str, Any], proposed["proposal"])
    assert proposed["proposalId"] == proposal["id"]
    assert proposal["status"] == "submitted"

    # A human reviewer must be assigned before a decision is accepted.
    foundry.ontology.assign_proposal(str(proposal["id"]), reviewer_user_id=REVIEWER.actor_user_id, ctx=REVIEWER)
    foundry.ontology.decide_proposal(
        str(proposal["id"]),
        decision="approve",
        expected_fingerprint=str(proposal["fingerprint"]),
        ctx=REVIEWER,
    )
    executed = foundry.ontology.execute_proposal(
        str(proposal["id"]), expected_fingerprint=str(proposal["fingerprint"]), ctx=REVIEWER
    )
    assert executed["status"] == "applied"

    merged = foundry.ontology.get_branch(branch_id, ctx=CREATOR)
    assert merged["status"] == "merged"
    assert merged["mergedVersionNumber"] == 2

    catalog = foundry.ontology.catalog(ctx=admin)
    assert catalog["versionNumber"] == 2
    object_types = {item["apiName"] for item in catalog["objectTypes"]}
    assert {"Order", "Customer"} <= object_types

    audit_events = foundry.operations.list_runs(ctx=admin)["auditEvents"]
    branch_events = {
        event["event_type"]
        for event in audit_events
        if event["resource_type"] == "ontology_branch" and event["resource_id"] == branch_id
    }
    assert {
        "ontology.branch.created",
        "ontology.branch.updated",
        "ontology.branch.proposed",
        "ontology.branch.merged",
    } <= branch_events


def test_branch_edits_never_touch_the_active_catalog(foundry, tmp_path) -> None:
    admin = _prepare_active_ontology(foundry, tmp_path)
    branch = _create_branch(foundry)
    foundry.ontology.update_branch(
        str(branch["id"]),
        yaml_text=_yaml_text(_order_type(extra_properties=[_note_property()]), _customer_type()),
        expected_fingerprint=str(branch["contentFingerprint"]),
        ctx=CREATOR,
    )
    catalog = foundry.ontology.catalog(ctx=admin)
    assert catalog["versionNumber"] == 1
    assert [item["apiName"] for item in catalog["objectTypes"]] == ["Order"]
    assert "note" not in {prop["apiName"] for prop in catalog["objectTypes"][0]["properties"]}


def test_branch_action_type_crud_uses_canonical_contract_without_touching_main(foundry, tmp_path) -> None:
    admin = _prepare_active_ontology(foundry, tmp_path)
    branch = _create_branch(foundry, name="action-builder", key="action-builder-branch")
    branch_id = str(branch["id"])

    created = foundry.ontology.create_branch_action_type(
        branch_id,
        definition=_branch_action_type(),
        expected_fingerprint=str(branch["contentFingerprint"]),
        idempotency_key="create-set-status",
        ctx=CREATOR,
    )
    action = cast(dict[str, Any], created["actionType"])
    assert created["isReplay"] is False
    assert action["contractFingerprint"] == action["parameterSchema"]["x-foundry-contract-fingerprint"]
    assert action["contract"]["formLayout"] == action["parameterSchema"]["x-foundry-form-layout"]
    assert action["inlineEligibility"] == {
        "isEligible": True,
        "reasons": [],
        "propertyApiName": "status",
        "parameterApiName": "status",
        "parameterType": "string",
    }

    replay = foundry.ontology.create_branch_action_type(
        branch_id,
        definition=_branch_action_type(),
        expected_fingerprint=str(branch["contentFingerprint"]),
        idempotency_key="create-set-status",
        ctx=CREATOR,
    )
    assert replay["isReplay"] is True
    assert replay["branch"]["baseStale"] is False
    assert replay["branch"]["contentFingerprint"] == created["branch"]["contentFingerprint"]
    with raises(ConflictDetected, match="Idempotency-Key"):
        foundry.ontology.create_branch_action_type(
            branch_id,
            definition=_branch_action_type(display_name="Different request"),
            expected_fingerprint=str(branch["contentFingerprint"]),
            idempotency_key="create-set-status",
            ctx=CREATOR,
        )
    assert foundry.ontology.get_branch_action_type(branch_id, "SetOrderStatus", ctx=CREATOR)["apiName"] == (
        "SetOrderStatus"
    )
    listed = foundry.ontology.list_branch_action_types(branch_id, ctx=CREATOR)
    assert [item["apiName"] for item in listed["items"]] == ["SetOrderStatus"]
    assert foundry.ontology.catalog(ctx=admin).get("actions", []) == []

    foundry.ontology.apply_text(_yaml_text(_order_type(extra_properties=[_note_property()])), ctx=admin)
    stale_replay = foundry.ontology.create_branch_action_type(
        branch_id,
        definition=_branch_action_type(),
        expected_fingerprint=str(branch["contentFingerprint"]),
        idempotency_key="create-set-status",
        ctx=CREATOR,
    )
    assert stale_replay["isReplay"] is True
    assert stale_replay["branch"]["baseStale"] is False
    assert foundry.ontology.get_branch(branch_id, ctx=CREATOR)["baseStale"] is True
    operations = foundry.operations.list_runs(ctx=admin)
    created_audits = [
        event
        for event in operations["auditEvents"]
        if event["event_type"] == "ontology.branch.action_type.created" and event["resource_id"] == branch_id
    ]
    created_outbox = [
        event
        for event in operations["outboxEvents"]
        if event["event_type"] == "ontology.branch.action_type.created" and event["aggregate_id"] == branch_id
    ]
    assert len(created_audits) == len(created_outbox) == 1

    updated = foundry.ontology.update_branch_action_type(
        branch_id,
        "SetOrderStatus",
        definition=_branch_action_type(display_name="Change order state"),
        expected_fingerprint=str(created["branch"]["contentFingerprint"]),
        idempotency_key="update-set-status",
        ctx=COLLABORATOR,
    )
    assert updated["actionType"]["displayName"] == "Change order state"

    deleted = foundry.ontology.delete_branch_action_type(
        branch_id,
        "SetOrderStatus",
        expected_fingerprint=str(updated["branch"]["contentFingerprint"]),
        idempotency_key="delete-set-status",
        ctx=CREATOR,
    )
    assert deleted["isReplay"] is False
    assert foundry.ontology.list_branch_action_types(branch_id, ctx=CREATOR)["items"] == []


def test_branch_action_activation_preserves_canonical_consumer_schema(foundry, tmp_path) -> None:
    admin = _prepare_active_ontology(foundry, tmp_path)
    branch = _create_branch(foundry, name="inline-action", key="inline-action-branch")
    created = foundry.ontology.create_branch_action_type(
        str(branch["id"]),
        definition=_branch_action_type(),
        expected_fingerprint=str(branch["contentFingerprint"]),
        idempotency_key="create-inline-action",
        ctx=CREATOR,
    )
    proposed = foundry.ontology.propose_branch(
        str(branch["id"]),
        title="Activate inline Action",
        idempotency_key="propose-inline-action",
        ctx=CREATOR,
    )
    proposal = cast(dict[str, Any], proposed["proposal"])
    # A human reviewer must be assigned before a decision is accepted.
    foundry.ontology.assign_proposal(str(proposal["id"]), reviewer_user_id=REVIEWER.actor_user_id, ctx=REVIEWER)
    foundry.ontology.decide_proposal(
        str(proposal["id"]),
        decision="approve",
        expected_fingerprint=str(proposal["fingerprint"]),
        ctx=REVIEWER,
    )
    foundry.ontology.execute_proposal(
        str(proposal["id"]),
        expected_fingerprint=str(proposal["fingerprint"]),
        ctx=REVIEWER,
    )

    catalog = foundry.ontology.catalog(ctx=admin)
    order = next(item for item in catalog["objectTypes"] if item["apiName"] == "Order")
    active_action = next(item for item in order["actions"] if item["apiName"] == "SetOrderStatus")

    assert active_action["parameterSchema"] == created["actionType"]["parameterSchema"]
    assert active_action["parameterSchema"]["x-foundry-inline-eligibility"]["propertyApiName"] == "status"


def test_branch_action_view_and_edit_roles_are_enforced_server_side(foundry, tmp_path) -> None:
    admin = _prepare_active_ontology(foundry, tmp_path)
    branch = foundry.ontology.create_branch(
        name="action-acl",
        idempotency_key="action-acl-branch",
        ctx=admin,
    )
    definition = _branch_action_type(display_name="Restricted action")
    definition["permissions"] = {
        "viewRoles": ["ops_manager"],
        "editRoles": ["ops_manager"],
        "applyRoles": ["ops_manager"],
    }

    created = foundry.ontology.create_branch_action_type(
        str(branch["id"]),
        definition=definition,
        expected_fingerprint=str(branch["contentFingerprint"]),
        idempotency_key="create-restricted-action",
        ctx=admin,
    )
    assert foundry.ontology.list_branch_action_types(str(branch["id"]), ctx=CREATOR)["items"] == []
    with raises(PermissionDenied, match="view Action Type"):
        foundry.ontology.get_branch_action_type(str(branch["id"]), "SetOrderStatus", ctx=CREATOR)

    replacement = _branch_action_type(display_name="Still restricted")
    replacement["permissions"] = definition["permissions"]
    with raises(PermissionDenied, match="edit Action Type"):
        foundry.ontology.update_branch_action_type(
            str(branch["id"]),
            "SetOrderStatus",
            definition=replacement,
            expected_fingerprint=str(created["branch"]["contentFingerprint"]),
            idempotency_key="edit-restricted-action",
            ctx=CREATOR,
        )


def test_branch_action_type_crud_rejects_stale_or_invalid_definitions(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    branch = _create_branch(foundry, name="action-validation", key="action-validation-branch")
    branch_id = str(branch["id"])
    invalid = _branch_action_type()
    invalid["target"] = "MissingObject"

    with raises(ValidationFailed, match="target reference was not found"):
        foundry.ontology.create_branch_action_type(
            branch_id,
            definition=invalid,
            expected_fingerprint=str(branch["contentFingerprint"]),
            idempotency_key="invalid-target",
            ctx=CREATOR,
        )

    with raises(ConflictDetected, match="fingerprint"):
        foundry.ontology.create_branch_action_type(
            branch_id,
            definition=_branch_action_type(),
            expected_fingerprint="sha256:stale",
            idempotency_key="stale-create",
            ctx=CREATOR,
        )


def test_rebase_carries_branch_changes_when_main_moves_compatibly(foundry, tmp_path) -> None:
    admin = _prepare_active_ontology(foundry, tmp_path)
    branch = _create_branch(foundry)
    branch_id = str(branch["id"])
    updated = foundry.ontology.update_branch(
        branch_id,
        yaml_text=_yaml_text(_order_type(extra_properties=[_note_property()])),
        expected_fingerprint=str(branch["contentFingerprint"]),
        ctx=CREATOR,
    )

    # Main moves while the branch is open: a compatible, non-overlapping change.
    foundry.ontology.apply_text(_yaml_text(_order_type(), _customer_type()), ctx=admin)

    diff = foundry.ontology.branch_diff(branch_id, ctx=CREATOR)
    assert diff["baseStale"] is True
    assert diff["conflicts"] == []
    customer_entry = _diff_entry(diff, "objectType", "Customer")
    assert customer_entry["branchChange"] == "unchanged"
    assert customer_entry["mainChange"] == "added"

    with raises(ConflictDetected, match="must be rebased"):
        foundry.ontology.propose_branch(branch_id, title="Notes", idempotency_key="stale-propose", ctx=CREATOR)

    rebased = foundry.ontology.rebase_branch(
        branch_id, expected_fingerprint=str(updated["contentFingerprint"]), ctx=CREATOR
    )
    assert rebased["baseStale"] is False
    assert rebased["baseVersionNumber"] == 2
    assert rebased["rebasedAt"] is not None

    detail = foundry.ontology.get_branch(branch_id, ctx=CREATOR)
    assert _properties(str(detail["yamlText"]), "Order") >= {"orderId", "status", "note"}
    assert _properties(str(detail["yamlText"]), "Customer") >= {"customerId", "region"}

    proposed = foundry.ontology.propose_branch(branch_id, title="Notes", idempotency_key="rebased-propose", ctx=CREATOR)
    assert cast(dict[str, Any], proposed["proposal"])["status"] == "submitted"


def test_rebase_conflicts_require_explicit_per_resource_resolution(foundry, tmp_path) -> None:
    admin = _prepare_active_ontology(foundry, tmp_path)
    keep_main = _create_branch(foundry, name="keep-main", key="key-main")
    keep_branch = _create_branch(foundry, name="keep-branch", key="key-branch")
    branch_yaml = _yaml_text(_order_type(extra_properties=[_note_property()]))
    fingerprints: dict[str, str] = {}
    for branch in (keep_main, keep_branch):
        updated = foundry.ontology.update_branch(
            str(branch["id"]),
            yaml_text=branch_yaml,
            expected_fingerprint=str(branch["contentFingerprint"]),
            ctx=CREATOR,
        )
        fingerprints[str(branch["id"])] = str(updated["contentFingerprint"])

    # Main modifies the SAME object type differently -> resource-level conflict.
    foundry.ontology.apply_text(_yaml_text(_order_type(extra_properties=[_note_property("shipped")])), ctx=admin)

    diff = foundry.ontology.branch_diff(str(keep_main["id"]), ctx=CREATOR)
    order_entry = _diff_entry(diff, "objectType", "Order")
    assert order_entry["hasConflict"] is True
    assert order_entry["branchChange"] == "modified"
    assert order_entry["mainChange"] == "modified"

    with raises(ValidationFailed, match="every rebase conflict needs a resolution") as exc_info:
        foundry.ontology.rebase_branch(
            str(keep_main["id"]),
            expected_fingerprint=fingerprints[str(keep_main["id"])],
            ctx=CREATOR,
        )
    unresolved = cast(list[dict[str, Any]], exc_info.value.details["unresolvedConflicts"])
    assert [(entry["kind"], entry["apiName"]) for entry in unresolved] == [("objectType", "Order")]

    use_main = foundry.ontology.rebase_branch(
        str(keep_main["id"]),
        expected_fingerprint=fingerprints[str(keep_main["id"])],
        resolutions=[{"kind": "objectType", "apiName": "Order", "use": "main"}],
        ctx=CREATOR,
    )
    main_properties = _properties(
        str(foundry.ontology.get_branch(str(keep_main["id"]), ctx=CREATOR)["yamlText"]), "Order"
    )
    assert "shipped" in main_properties
    assert "note" not in main_properties
    assert use_main["baseVersionNumber"] == 2

    use_branch = foundry.ontology.rebase_branch(
        str(keep_branch["id"]),
        expected_fingerprint=fingerprints[str(keep_branch["id"])],
        resolutions=[{"kind": "objectType", "apiName": "Order", "use": "branch"}],
        ctx=CREATOR,
    )
    branch_properties = _properties(
        str(foundry.ontology.get_branch(str(keep_branch["id"]), ctx=CREATOR)["yamlText"]), "Order"
    )
    assert "note" in branch_properties
    assert "shipped" not in branch_properties
    assert use_branch["baseVersionNumber"] == 2

    # The kept-branch content removes main's shipped property -> blocked at
    # merge time, which the diff's migration plan surfaces to the UI.
    plan = cast(dict[str, Any], foundry.ontology.branch_diff(str(keep_branch["id"]), ctx=CREATOR)["migrationPlan"])
    assert plan["status"] == "blocked"


def test_rebase_rejects_unknown_resolutions_and_up_to_date_branches(foundry, tmp_path) -> None:
    admin = _prepare_active_ontology(foundry, tmp_path)
    branch = _create_branch(foundry)
    with raises(ConflictDetected, match="already based on the active ontology version"):
        foundry.ontology.rebase_branch(
            str(branch["id"]), expected_fingerprint=str(branch["contentFingerprint"]), ctx=CREATOR
        )
    foundry.ontology.apply_text(_yaml_text(_order_type(), _customer_type()), ctx=admin)
    with raises(ValidationFailed, match="not in conflict"):
        foundry.ontology.rebase_branch(
            str(branch["id"]),
            expected_fingerprint=str(branch["contentFingerprint"]),
            resolutions=[{"kind": "objectType", "apiName": "Order", "use": "main"}],
            ctx=CREATOR,
        )
    with raises(ValidationFailed, match="kind, apiName, and use"):
        foundry.ontology.rebase_branch(
            str(branch["id"]),
            expected_fingerprint=str(branch["contentFingerprint"]),
            resolutions=[{"kind": "objectType", "apiName": "Order", "use": "theirs"}],
            ctx=CREATOR,
        )


def test_update_guards_fingerprint_size_and_structure(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    branch = _create_branch(foundry)
    branch_id = str(branch["id"])

    with raises(ConflictDetected, match="fingerprint mismatch") as exc_info:
        foundry.ontology.update_branch(
            branch_id,
            yaml_text=_yaml_text(_order_type(extra_properties=[_note_property()])),
            expected_fingerprint="sha256:stale",
            ctx=CREATOR,
        )
    assert exc_info.value.details["expectedFingerprint"] == "sha256:stale"
    assert exc_info.value.details["contentFingerprint"] == branch["contentFingerprint"]

    with raises(ValidationFailed, match="exceeds size limit"):
        foundry.ontology.update_branch(
            branch_id,
            yaml_text="a" * (1_048_576 + 1),
            expected_fingerprint=str(branch["contentFingerprint"]),
            ctx=CREATOR,
        )

    with raises(ValidationFailed):
        foundry.ontology.update_branch(
            branch_id,
            yaml_text="just a string, not an ontology definition",
            expected_fingerprint=str(branch["contentFingerprint"]),
            ctx=CREATOR,
        )

    replay = foundry.ontology.update_branch(
        branch_id,
        yaml_text=str(foundry.ontology.get_branch(branch_id, ctx=CREATOR)["yamlText"]),
        expected_fingerprint=str(branch["contentFingerprint"]),
        ctx=CREATOR,
    )
    assert replay["contentFingerprint"] == branch["contentFingerprint"]


def test_abandon_frees_name_and_blocks_further_edits(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    branch = _create_branch(foundry, name="temp", key="temp-key")
    branch_id = str(branch["id"])

    replay = _create_branch(foundry, name="temp", key="temp-key")
    assert replay["id"] == branch_id

    with raises(ConflictDetected, match="already exists"):
        foundry.ontology.create_branch(name="temp", idempotency_key="other-key", ctx=COLLABORATOR)

    abandoned = foundry.ontology.abandon_branch(branch_id, ctx=CREATOR)
    assert abandoned["status"] == "abandoned"
    assert foundry.ontology.abandon_branch(branch_id, ctx=CREATOR)["status"] == "abandoned"

    with raises(ConflictDetected, match="no longer open"):
        foundry.ontology.update_branch(
            branch_id,
            yaml_text=_yaml_text(_order_type(extra_properties=[_note_property()])),
            expected_fingerprint=str(branch["contentFingerprint"]),
            ctx=CREATOR,
        )
    with raises(ConflictDetected, match="no longer open"):
        foundry.ontology.propose_branch(branch_id, title="late", idempotency_key="late-key", ctx=CREATOR)

    reused = foundry.ontology.create_branch(name="temp", idempotency_key="reuse-key", ctx=COLLABORATOR)
    assert reused["id"] != branch_id

    # A non-creator with ontology:activate may abandon someone else's branch.
    other_abandoned = foundry.ontology.abandon_branch(str(reused["id"]), ctx=REVIEWER)
    assert other_abandoned["status"] == "abandoned"


def test_propose_is_idempotent_while_the_proposal_is_live(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    branch = _create_branch(foundry)
    branch_id = str(branch["id"])
    foundry.ontology.update_branch(
        branch_id,
        yaml_text=_yaml_text(_order_type(extra_properties=[_note_property()])),
        expected_fingerprint=str(branch["contentFingerprint"]),
        ctx=CREATOR,
    )
    first = foundry.ontology.propose_branch(branch_id, title="Notes", idempotency_key="propose-a", ctx=CREATOR)
    replay_same_key = foundry.ontology.propose_branch(
        branch_id, title="Notes", idempotency_key="propose-a", ctx=CREATOR
    )
    replay_new_key = foundry.ontology.propose_branch(
        branch_id, title="Notes again", idempotency_key="propose-b", ctx=CREATOR
    )
    first_proposal = cast(dict[str, Any], first["proposal"])
    assert cast(dict[str, Any], replay_same_key["proposal"])["id"] == first_proposal["id"]
    assert cast(dict[str, Any], replay_new_key["proposal"])["id"] == first_proposal["id"]
    # The embedded reviewer summary carries the branch's per-resource diff.
    description = str(first_proposal["description"])
    assert "[ontology-branch-diff]" in description
    assert '"apiName": "Order"' in description
    listed = foundry.ontology.list_proposals(ctx=CREATOR)
    assert [item["id"] for item in listed["items"]] == [first_proposal["id"]]


def test_permission_denials_for_viewer_and_missing_branch(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    with raises(PermissionDenied):
        foundry.ontology.create_branch(name="nope", idempotency_key="viewer-key", ctx=VIEWER)
    with raises(PermissionDenied):
        foundry.ontology.list_branches(ctx=VIEWER)
    branch = _create_branch(foundry)
    with raises(PermissionDenied):
        foundry.ontology.abandon_branch(str(branch["id"]), ctx=VIEWER)
    with raises(NotFound):
        foundry.ontology.get_branch("ontbranch_missing", ctx=CREATOR)


def test_list_branches_filters_status_and_pages_with_cursor(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    ids = [str(_create_branch(foundry, name=f"feature-{index}", key=f"key-{index}")["id"]) for index in range(3)]
    foundry.ontology.abandon_branch(ids[1], ctx=CREATOR)

    open_items: list[str] = []
    cursor: str | None = None
    while True:
        page = foundry.ontology.list_branches(status="open", cursor=cursor, limit=1, ctx=CREATOR)
        open_items += [str(item["id"]) for item in cast(list[dict[str, Any]], page["items"])]
        cursor = cast(str | None, page["nextCursor"])
        if cursor is None:
            break
    assert set(open_items) == {ids[0], ids[2]}

    abandoned = foundry.ontology.list_branches(status="abandoned", ctx=CREATOR)
    assert [item["id"] for item in abandoned["items"]] == [ids[1]]

    with raises(ValidationFailed, match="invalid ontology branch status filter"):
        foundry.ontology.list_branches(status="closed", ctx=CREATOR)
    with raises(ValidationFailed, match="invalid ontology branch cursor"):
        foundry.ontology.list_branches(cursor="not-a-cursor", ctx=CREATOR)


def test_diff_rules_classify_and_merge_resources_directly() -> None:
    base = parse_resource_map(
        serialize_definition_yaml(
            {
                "objectTypes": [
                    {"apiName": "Order", "primaryKey": "id", "properties": []},
                    {"apiName": "Removed", "primaryKey": "id", "properties": []},
                    {"apiName": "Fought", "primaryKey": "id", "properties": []},
                ]
            }
        )
    )
    branch = parse_resource_map(
        serialize_definition_yaml(
            {
                "objectTypes": [
                    {"apiName": "Order", "primaryKey": "id", "properties": [{"apiName": "note"}]},
                    {"apiName": "Fought", "primaryKey": "id", "properties": [{"apiName": "branchTake"}]},
                    {"apiName": "Added", "primaryKey": "id", "properties": []},
                ]
            }
        )
    )
    main = parse_resource_map(
        serialize_definition_yaml(
            {
                "objectTypes": [
                    {"apiName": "Order", "primaryKey": "id", "properties": []},
                    {"apiName": "Removed", "primaryKey": "id", "properties": []},
                    {"apiName": "Fought", "primaryKey": "id", "properties": [{"apiName": "mainTake"}]},
                    {"apiName": "MainNew", "primaryKey": "id", "properties": []},
                ],
                "linkTypes": [{"apiName": "orderLink", "from": "Order", "to": "MainNew"}],
            }
        )
    )
    diff = evaluate_branch_diff(base, branch, main)
    by_key = {(entry["kind"], entry["apiName"]): entry for entry in diff["resources"]}
    assert by_key[("objectType", "Added")]["branchChange"] == "added"
    assert by_key[("objectType", "Removed")]["branchChange"] == "removed"
    assert by_key[("objectType", "Order")]["branchChange"] == "modified"
    assert by_key[("objectType", "MainNew")]["mainChange"] == "added"
    assert by_key[("linkType", "orderLink")]["mainChange"] == "added"
    assert [(entry["kind"], entry["apiName"]) for entry in diff["conflicts"]] == [("objectType", "Fought")]

    assert unresolved_conflicts(diff, {}) == diff["conflicts"]
    assert unknown_resolutions(diff, {("objectType", "Order"): "main"}) == [("objectType", "Order")]

    merged = merge_branch_onto_main(base, branch, main, diff, {("objectType", "Fought"): "branch"})
    merged_map = parse_resource_map(serialize_definition_yaml(merged))
    assert ("objectType", "Added") in merged_map
    assert ("objectType", "MainNew") in merged_map
    assert ("objectType", "Removed") not in merged_map
    assert merged_map[("objectType", "Fought")]["properties"] == [{"apiName": "branchTake"}]
    assert merged_map[("objectType", "Order")]["properties"] == [{"apiName": "note"}]

    kept_main = merge_branch_onto_main(base, branch, main, diff, {("objectType", "Fought"): "main"})
    kept_map = parse_resource_map(serialize_definition_yaml(kept_main))
    assert kept_map[("objectType", "Fought")]["properties"] == [{"apiName": "mainTake"}]

    # Identical changes on both sides converge without a conflict.
    convergent = evaluate_branch_diff(base, main, main)
    assert convergent["conflicts"] == []


def test_api_branch_flow_and_error_mapping(foundry, tmp_path, monkeypatch: MonkeyPatch) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)

    missing_key = client.post("/api/ontology/branches", headers=CREATOR_HEADERS, json={"name": "api-branch"})
    assert missing_key.status_code == 422

    viewer_denied = client.post(
        "/api/ontology/branches",
        headers={**VIEWER_HEADERS, "Idempotency-Key": "viewer-branch"},
        json={"name": "api-branch"},
    )
    assert viewer_denied.status_code == 403
    assert viewer_denied.json()["detail"]["code"] == "PERMISSION_DENIED"

    created = client.post(
        "/api/ontology/branches",
        headers={**CREATOR_HEADERS, "Idempotency-Key": "api-branch-key"},
        json={"name": "api-branch"},
    )
    assert created.status_code == 200
    branch = created.json()
    branch_id = branch["id"]

    listed = client.get("/api/ontology/branches", headers=CREATOR_HEADERS, params={"status": "open"})
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [branch_id]

    stale_update = client.post(
        f"/api/ontology/branches/{branch_id}/update",
        headers=CREATOR_HEADERS,
        json={
            "yamlText": _yaml_text(_order_type(extra_properties=[_note_property()])),
            "expectedFingerprint": "sha256:stale",
        },
    )
    assert stale_update.status_code == 409
    assert stale_update.json()["detail"]["code"] == "CONFLICT"

    updated = client.post(
        f"/api/ontology/branches/{branch_id}/update",
        headers=CREATOR_HEADERS,
        json={
            "yamlText": _yaml_text(_order_type(extra_properties=[_note_property()])),
            "expectedFingerprint": branch["contentFingerprint"],
        },
    )
    assert updated.status_code == 200

    diff = client.get(f"/api/ontology/branches/{branch_id}/diff", headers=CREATOR_HEADERS)
    assert diff.status_code == 200
    assert diff.json()["baseStale"] is False
    assert diff.json()["conflicts"] == []

    not_stale_rebase = client.post(
        f"/api/ontology/branches/{branch_id}/rebase",
        headers=CREATOR_HEADERS,
        json={"expectedFingerprint": updated.json()["contentFingerprint"], "resolutions": []},
    )
    assert not_stale_rebase.status_code == 409
    assert not_stale_rebase.json()["detail"]["code"] == "CONFLICT"

    proposed = client.post(
        f"/api/ontology/branches/{branch_id}/propose",
        headers={**CREATOR_HEADERS, "Idempotency-Key": "api-propose-key"},
        json={"title": "API branch merge", "description": "adds a note"},
    )
    assert proposed.status_code == 200
    proposal = proposed.json()["proposal"]

    # Review requires an explicitly assigned human reviewer before a decision lands.
    assigned = client.post(
        f"/api/ontology/proposals/{proposal['id']}/assign",
        headers=REVIEWER_HEADERS,
        json={"reviewerUserId": "user-reviewer"},
    )
    assert assigned.status_code == 200
    decided = client.post(
        f"/api/ontology/proposals/{proposal['id']}/decide",
        headers=REVIEWER_HEADERS,
        json={"decision": "approve", "expectedFingerprint": proposal["fingerprint"]},
    )
    assert decided.status_code == 200
    executed = client.post(
        f"/api/ontology/proposals/{proposal['id']}/execute",
        headers=REVIEWER_HEADERS,
        json={"expectedFingerprint": proposal["fingerprint"]},
    )
    assert executed.status_code == 200

    detail = client.get(f"/api/ontology/branches/{branch_id}", headers=CREATOR_HEADERS)
    assert detail.status_code == 200
    assert detail.json()["status"] == "merged"
    assert detail.json()["mergedVersionNumber"] == 2

    abandon_merged = client.post(f"/api/ontology/branches/{branch_id}/abandon", headers=CREATOR_HEADERS)
    assert abandon_merged.status_code == 409

    second = client.post(
        "/api/ontology/branches",
        headers={**CREATOR_HEADERS, "Idempotency-Key": "api-branch-key-2"},
        json={"name": "api-branch-2"},
    ).json()
    abandoned = client.post(f"/api/ontology/branches/{second['id']}/abandon", headers=CREATOR_HEADERS)
    assert abandoned.status_code == 200
    assert abandoned.json()["status"] == "abandoned"

    missing = client.get("/api/ontology/branches/ontbranch_missing", headers=CREATOR_HEADERS)
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "NOT_FOUND"


def test_api_branch_action_type_crud_requires_cas_and_idempotency(foundry, tmp_path, monkeypatch: MonkeyPatch) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    branch = client.post(
        "/api/ontology/branches",
        headers={**CREATOR_HEADERS, "Idempotency-Key": "api-action-builder-branch"},
        json={"name": "api-action-builder"},
    ).json()
    branch_id = branch["id"]
    request_payload = {
        "definition": _branch_action_type(),
        "expectedFingerprint": branch["contentFingerprint"],
    }

    missing_key = client.post(
        f"/api/ontology/branches/{branch_id}/action-types", headers=CREATOR_HEADERS, json=request_payload
    )
    assert missing_key.status_code == 422

    created = client.post(
        f"/api/ontology/branches/{branch_id}/action-types",
        headers={**CREATOR_HEADERS, "Idempotency-Key": "api-create-set-status"},
        json=request_payload,
    )
    assert created.status_code == 200
    assert created.json()["actionType"]["parameterSchema"]["x-foundry-form-layout"]["sections"][0]["id"] == ("decision")

    replay = client.post(
        f"/api/ontology/branches/{branch_id}/action-types",
        headers={**CREATOR_HEADERS, "Idempotency-Key": "api-create-set-status"},
        json=request_payload,
    )
    assert replay.status_code == 200
    assert replay.json()["isReplay"] is True
    conflicting_reuse = client.post(
        f"/api/ontology/branches/{branch_id}/action-types",
        headers={**CREATOR_HEADERS, "Idempotency-Key": "api-create-set-status"},
        json={
            "definition": _branch_action_type(display_name="Different request"),
            "expectedFingerprint": branch["contentFingerprint"],
        },
    )
    assert conflicting_reuse.status_code == 409
    assert conflicting_reuse.json()["detail"]["code"] == "CONFLICT"

    listed = client.get(f"/api/ontology/branches/{branch_id}/action-types", headers=CREATOR_HEADERS)
    detail = client.get(f"/api/ontology/branches/{branch_id}/action-types/SetOrderStatus", headers=CREATOR_HEADERS)
    assert listed.status_code == detail.status_code == 200
    assert [item["apiName"] for item in listed.json()["items"]] == ["SetOrderStatus"]

    updated = client.put(
        f"/api/ontology/branches/{branch_id}/action-types/SetOrderStatus",
        headers={**CREATOR_HEADERS, "Idempotency-Key": "api-update-set-status"},
        json={
            "definition": _branch_action_type(display_name="Change order state"),
            "expectedFingerprint": created.json()["branch"]["contentFingerprint"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["actionType"]["displayName"] == "Change order state"

    deleted = client.request(
        "DELETE",
        f"/api/ontology/branches/{branch_id}/action-types/SetOrderStatus",
        headers={**CREATOR_HEADERS, "Idempotency-Key": "api-delete-set-status"},
        json={"expectedFingerprint": updated.json()["branch"]["contentFingerprint"]},
    )
    assert deleted.status_code == 200
    assert deleted.json()["actionType"] is None
