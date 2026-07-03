"""Ontology change proposals: submit, assign, decide, execute, withdraw governance flow."""

from __future__ import annotations

from typing import Any, cast

import yaml
from fastapi.testclient import TestClient
from foundry_lite.application.foundry import FoundryLite
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

SUBMITTER = RequestContext(actor_user_id="user-submitter", roles=("data_engineer",))
REVIEWER = RequestContext(actor_user_id="user-reviewer", roles=("data_engineer",))
VIEWER = RequestContext(actor_user_id="user-viewer", roles=("viewer",))

SUBMITTER_HEADERS = {"X-Tenant-ID": "tenant-demo", "X-User-ID": "user-submitter", "X-Roles": "data_engineer"}
REVIEWER_HEADERS = {"X-Tenant-ID": "tenant-demo", "X-User-ID": "user-reviewer", "X-Roles": "data_engineer"}
VIEWER_HEADERS = {"X-Tenant-ID": "tenant-demo", "X-User-ID": "user-viewer", "X-Roles": "viewer"}


def _order_definition(*, extra_properties: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "objectTypes": [
            {
                "apiName": "Order",
                "primaryKey": "orderId",
                "backing": {"dataset": "clean.orders"},
                "properties": [
                    {"apiName": "orderId", "column": "order_id", "type": "string", "nullable": False, "indexed": True},
                    {"apiName": "status", "column": "status", "type": "string"},
                    *(extra_properties or []),
                ],
            }
        ]
    }


def _note_property(api_name: str = "note") -> dict[str, object]:
    return {"apiName": api_name, "type": "string", "source": "edit_layer"}


def _yaml_text(definition: dict[str, object]) -> str:
    return yaml.safe_dump(definition, sort_keys=False)


def _prepare_active_ontology(foundry: FoundryLite, tmp_path: Any) -> RequestContext:
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text(ORDERS_CSV, encoding="utf-8")
    foundry.datasets.ensure("clean.orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("clean.orders", str(csv_path), ctx=ctx)
    foundry.ontology.apply_text(_yaml_text(_order_definition()), ctx=ctx)
    return ctx


def _submit(foundry: FoundryLite, *, key: str = "submit-key-1", title: str = "Add note") -> dict[str, object]:
    return foundry.ontology.submit_proposal(
        yaml_text=_yaml_text(_order_definition(extra_properties=[_note_property()])),
        title=title,
        description="adds an edit-layer note property",
        idempotency_key=key,
        ctx=SUBMITTER,
    )


def _fingerprint(proposal: dict[str, object]) -> str:
    return str(proposal["fingerprint"])


def test_full_lifecycle_submit_assign_approve_execute_updates_catalog_and_audits(foundry, tmp_path) -> None:
    admin = _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)
    assert proposal["status"] == "submitted"
    assert proposal["submittedByUserId"] == "user-submitter"
    assert proposal["hasBlockedChangesAtSubmit"] is False
    assert cast(dict[str, Any], proposal["validation"])["objectTypeCount"] == 1
    proposal_id = str(proposal["id"])

    assigned = foundry.ontology.assign_proposal(proposal_id, reviewer_user_id="user-reviewer", ctx=REVIEWER)
    assert assigned["status"] == "in_review"
    assert assigned["assigneeUserId"] == "user-reviewer"

    decided = foundry.ontology.decide_proposal(
        proposal_id,
        decision="approve",
        expected_fingerprint=_fingerprint(proposal),
        comment="looks good",
        ctx=REVIEWER,
    )
    assert decided["status"] == "approved"
    decision = cast(dict[str, Any], decided["decision"])
    assert decision["decision"] == "approved"
    assert decision["decidedByUserId"] == "user-reviewer"
    assert decision["migrationPlan"]["status"] == "compatible"

    applied = foundry.ontology.execute_proposal(proposal_id, expected_fingerprint=_fingerprint(proposal), ctx=REVIEWER)
    assert applied["status"] == "applied"
    applied_version = cast(dict[str, Any], applied["appliedOntologyVersion"])
    assert applied_version["versionNumber"] == 2
    assert applied_version["ontologyVersionId"]

    catalog = foundry.ontology.catalog(ctx=admin)
    assert catalog["versionNumber"] == 2
    order = catalog["objectTypes"][0]
    assert "note" in {prop["apiName"] for prop in order["properties"]}

    detail = foundry.ontology.get_proposal(proposal_id, ctx=SUBMITTER)
    assert [event["event"] for event in detail["timeline"]] == ["submitted", "assigned", "decided", "applied"]
    assert (
        cast(dict[str, Any], detail["appliedOntologyVersion"])["ontologyVersionId"]
        == (applied_version["ontologyVersionId"])
    )

    audit_events = foundry.operations.list_runs(ctx=admin)["auditEvents"]
    proposal_events = {
        event["event_type"]
        for event in audit_events
        if event["resource_type"] == "ontology_proposal" and event["resource_id"] == proposal_id
    }
    assert {
        "ontology.proposal.submitted",
        "ontology.proposal.assigned",
        "ontology.proposal.decided",
        "ontology.proposal.applied",
    } <= proposal_events


def test_submitter_may_not_approve_own_proposal(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)

    with raises(PermissionDenied, match="may not approve their own proposal"):
        foundry.ontology.decide_proposal(
            str(proposal["id"]),
            decision="approve",
            expected_fingerprint=_fingerprint(proposal),
            ctx=SUBMITTER,
        )

    # A different reviewer can approve the same proposal.
    approved = foundry.ontology.decide_proposal(
        str(proposal["id"]),
        decision="approve",
        expected_fingerprint=_fingerprint(proposal),
        ctx=REVIEWER,
    )
    assert approved["status"] == "approved"


def test_submit_replay_is_idempotent_and_conflicting_reuse_is_rejected(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    first = _submit(foundry, key="replay-key")
    replay = _submit(foundry, key="replay-key")
    assert replay["id"] == first["id"]
    assert replay["fingerprint"] == first["fingerprint"]

    with raises(ConflictDetected, match="idempotency key reused"):
        foundry.ontology.submit_proposal(
            yaml_text=_yaml_text(_order_definition(extra_properties=[_note_property("other")])),
            title="Different change",
            idempotency_key="replay-key",
            ctx=SUBMITTER,
        )


def test_decision_recomputes_drifted_plan_and_refuses_blocked_approval(foundry, tmp_path) -> None:
    admin = _prepare_active_ontology(foundry, tmp_path)
    # The proposal matches the currently active shape, so it is compatible at submit.
    proposal = foundry.ontology.submit_proposal(
        yaml_text=_yaml_text(_order_definition()),
        title="No-op against v1",
        idempotency_key="drift-key",
        ctx=SUBMITTER,
    )
    assert proposal["hasBlockedChangesAtSubmit"] is False

    # Drift: a direct apply adds a property the proposal does not carry, so the
    # proposal would now REMOVE that property (a blocked change).
    foundry.ontology.apply_text(_yaml_text(_order_definition(extra_properties=[_note_property()])), ctx=admin)

    with raises(ValidationFailed, match="approval refused") as exc_info:
        foundry.ontology.decide_proposal(
            str(proposal["id"]),
            decision="approve",
            expected_fingerprint=_fingerprint(proposal),
            ctx=REVIEWER,
        )
    plan = cast(dict[str, Any], exc_info.value.details["migrationPlan"])
    assert plan["status"] == "blocked"
    assert any(change["kind"] == "property_removed" for change in plan["blockedChanges"])

    # Rejection still lands and stores the recomputed decision-time plan.
    rejected = foundry.ontology.decide_proposal(
        str(proposal["id"]),
        decision="reject",
        expected_fingerprint=_fingerprint(proposal),
        comment="would remove note",
        ctx=REVIEWER,
    )
    assert rejected["status"] == "rejected"
    decision = cast(dict[str, Any], rejected["decision"])
    assert decision["comment"] == "would remove note"
    assert decision["hasBlockedChanges"] is True
    assert decision["migrationPlan"]["status"] == "blocked"


def test_stale_fingerprint_conflicts_on_decide_and_execute(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)
    proposal_id = str(proposal["id"])

    with raises(ConflictDetected, match="fingerprint mismatch"):
        foundry.ontology.decide_proposal(
            proposal_id,
            decision="approve",
            expected_fingerprint="sha256:stale",
            ctx=REVIEWER,
        )
    foundry.ontology.decide_proposal(
        proposal_id, decision="approve", expected_fingerprint=_fingerprint(proposal), ctx=REVIEWER
    )
    with raises(ConflictDetected, match="fingerprint mismatch"):
        foundry.ontology.execute_proposal(proposal_id, expected_fingerprint="sha256:stale", ctx=REVIEWER)


def test_duplicate_execute_replays_without_reapplying(foundry, tmp_path) -> None:
    admin = _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)
    proposal_id = str(proposal["id"])
    foundry.ontology.decide_proposal(
        proposal_id, decision="approve", expected_fingerprint=_fingerprint(proposal), ctx=REVIEWER
    )
    first = foundry.ontology.execute_proposal(proposal_id, expected_fingerprint=_fingerprint(proposal), ctx=REVIEWER)
    replay = foundry.ontology.execute_proposal(proposal_id, expected_fingerprint=_fingerprint(proposal), ctx=REVIEWER)

    assert replay["status"] == "applied"
    assert replay["appliedOntologyVersion"] == first["appliedOntologyVersion"]
    # The apply black box ran exactly once: the catalog stays at version 2.
    assert foundry.ontology.catalog(ctx=admin)["versionNumber"] == 2


def test_execute_requires_an_approved_proposal(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)

    with raises(ConflictDetected, match="only approved ontology proposals"):
        foundry.ontology.execute_proposal(
            str(proposal["id"]), expected_fingerprint=_fingerprint(proposal), ctx=REVIEWER
        )


def test_decision_replay_and_conflicting_second_decision(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)
    proposal_id = str(proposal["id"])
    rejected = foundry.ontology.decide_proposal(
        proposal_id,
        decision="reject",
        expected_fingerprint=_fingerprint(proposal),
        comment="not now",
        ctx=REVIEWER,
    )
    assert rejected["status"] == "rejected"

    replay = foundry.ontology.decide_proposal(
        proposal_id,
        decision="reject",
        expected_fingerprint=_fingerprint(proposal),
        comment="not now",
        ctx=REVIEWER,
    )
    assert replay["status"] == "rejected"

    with raises(ConflictDetected, match="already decided differently"):
        foundry.ontology.decide_proposal(
            proposal_id,
            decision="approve",
            expected_fingerprint=_fingerprint(proposal),
            ctx=REVIEWER,
        )


def test_withdraw_is_submitter_only_and_blocks_later_review(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)
    proposal_id = str(proposal["id"])

    with raises(PermissionDenied, match="only the submitter"):
        foundry.ontology.withdraw_proposal(proposal_id, reason="not yours", ctx=REVIEWER)

    withdrawn = foundry.ontology.withdraw_proposal(proposal_id, reason="superseded", ctx=SUBMITTER)
    assert withdrawn["status"] == "withdrawn"
    assert cast(dict[str, Any], withdrawn["withdrawal"])["reason"] == "superseded"

    replay = foundry.ontology.withdraw_proposal(proposal_id, ctx=SUBMITTER)
    assert replay["status"] == "withdrawn"

    with raises(ConflictDetected):
        foundry.ontology.assign_proposal(proposal_id, reviewer_user_id="user-reviewer", ctx=REVIEWER)
    with raises(ConflictDetected):
        foundry.ontology.decide_proposal(
            proposal_id, decision="approve", expected_fingerprint=_fingerprint(proposal), ctx=REVIEWER
        )


def test_withdraw_covers_approved_but_not_applied_proposals(foundry, tmp_path) -> None:
    admin = _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)
    proposal_id = str(proposal["id"])
    foundry.ontology.decide_proposal(
        proposal_id, decision="approve", expected_fingerprint=_fingerprint(proposal), ctx=REVIEWER
    )

    withdrawn = foundry.ontology.withdraw_proposal(proposal_id, reason="changed my mind", ctx=SUBMITTER)
    assert withdrawn["status"] == "withdrawn"

    with raises(ConflictDetected):
        foundry.ontology.execute_proposal(proposal_id, expected_fingerprint=_fingerprint(proposal), ctx=REVIEWER)
    # Nothing was applied: the active ontology stays at version 1.
    assert foundry.ontology.catalog(ctx=admin)["versionNumber"] == 1


def test_applied_proposal_cannot_be_withdrawn(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)
    proposal_id = str(proposal["id"])
    foundry.ontology.decide_proposal(
        proposal_id, decision="approve", expected_fingerprint=_fingerprint(proposal), ctx=REVIEWER
    )
    foundry.ontology.execute_proposal(proposal_id, expected_fingerprint=_fingerprint(proposal), ctx=REVIEWER)

    with raises(ConflictDetected, match="can no longer be withdrawn"):
        foundry.ontology.withdraw_proposal(proposal_id, ctx=SUBMITTER)


def test_unauthorized_roles_are_denied(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)
    proposal_id = str(proposal["id"])

    with raises(PermissionDenied):
        foundry.ontology.submit_proposal(
            yaml_text=_yaml_text(_order_definition()), title="viewer", idempotency_key="viewer-key", ctx=VIEWER
        )
    with raises(PermissionDenied):
        foundry.ontology.assign_proposal(proposal_id, reviewer_user_id="user-viewer", ctx=VIEWER)
    with raises(PermissionDenied):
        foundry.ontology.decide_proposal(
            proposal_id, decision="approve", expected_fingerprint=_fingerprint(proposal), ctx=VIEWER
        )
    with raises(PermissionDenied):
        foundry.ontology.execute_proposal(proposal_id, expected_fingerprint=_fingerprint(proposal), ctx=VIEWER)
    with raises(PermissionDenied):
        foundry.ontology.withdraw_proposal(proposal_id, ctx=VIEWER)
    with raises(PermissionDenied):
        foundry.ontology.list_proposals(ctx=VIEWER)
    with raises(PermissionDenied):
        foundry.ontology.get_proposal(proposal_id, ctx=VIEWER)


def test_list_filters_by_status_and_pages_with_cursor(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposals = [_submit(foundry, key=f"list-key-{index}", title=f"Proposal {index}") for index in range(3)]
    first_id = str(proposals[0]["id"])
    foundry.ontology.assign_proposal(first_id, reviewer_user_id="user-reviewer", ctx=REVIEWER)

    submitted = foundry.ontology.list_proposals(status="submitted", ctx=SUBMITTER)
    assert {item["id"] for item in submitted["items"]} == {str(p["id"]) for p in proposals[1:]}
    in_review = foundry.ontology.list_proposals(status="in_review", ctx=SUBMITTER)
    assert [item["id"] for item in in_review["items"]] == [first_id]

    page_one = foundry.ontology.list_proposals(limit=2, ctx=SUBMITTER)
    assert len(page_one["items"]) == 2
    assert page_one["nextCursor"] is not None
    page_two = foundry.ontology.list_proposals(limit=2, cursor=str(page_one["nextCursor"]), ctx=SUBMITTER)
    page_ids = [item["id"] for item in page_one["items"]] + [item["id"] for item in page_two["items"]]
    assert set(page_ids) == {str(p["id"]) for p in proposals}
    assert len(page_ids) == len(set(page_ids))

    with raises(ValidationFailed, match="invalid ontology proposal status filter"):
        foundry.ontology.list_proposals(status="nonsense", ctx=SUBMITTER)
    with raises(ValidationFailed, match="invalid ontology proposal cursor"):
        foundry.ontology.list_proposals(cursor="not-a-cursor", ctx=SUBMITTER)


def test_get_proposal_returns_full_read_model_and_missing_id_404s(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)

    detail = foundry.ontology.get_proposal(str(proposal["id"]), ctx=SUBMITTER)
    assert detail["title"] == "Add note"
    assert detail["description"] == "adds an edit-layer note property"
    assert "note" in str(detail["yamlText"])
    assert cast(dict[str, Any], detail["submittedMigrationPlan"])["status"] == "compatible"
    assert detail["timeline"][0]["event"] == "submitted"

    with raises(NotFound):
        foundry.ontology.get_proposal("ontprop_missing", ctx=SUBMITTER)


def test_submit_requires_structurally_valid_yaml_and_title(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)

    with raises(ValidationFailed):
        foundry.ontology.submit_proposal(
            yaml_text="objectTypes:\n  - apiName: Broken\n",
            title="broken",
            idempotency_key="broken-key",
            ctx=SUBMITTER,
        )
    with raises(ValidationFailed, match="title is required"):
        foundry.ontology.submit_proposal(
            yaml_text=_yaml_text(_order_definition()), title="", idempotency_key="no-title", ctx=SUBMITTER
        )


def test_api_proposal_flow_end_to_end(foundry, tmp_path, monkeypatch: MonkeyPatch) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)

    submitted = client.post(
        "/api/ontology/proposals",
        headers={**SUBMITTER_HEADERS, "Idempotency-Key": "api-key-1"},
        json={
            "yamlText": _yaml_text(_order_definition(extra_properties=[_note_property()])),
            "title": "Add note over API",
        },
    )
    assert submitted.status_code == 200
    proposal = submitted.json()
    assert proposal["status"] == "submitted"
    proposal_id = proposal["id"]
    fingerprint = proposal["fingerprint"]

    assigned = client.post(
        f"/api/ontology/proposals/{proposal_id}/assign",
        headers=REVIEWER_HEADERS,
        json={"reviewerUserId": "user-reviewer"},
    )
    assert assigned.status_code == 200
    assert assigned.json()["status"] == "in_review"

    decided = client.post(
        f"/api/ontology/proposals/{proposal_id}/decide",
        headers=REVIEWER_HEADERS,
        json={"decision": "approve", "expectedFingerprint": fingerprint, "comment": "ship it"},
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "approved"

    executed = client.post(
        f"/api/ontology/proposals/{proposal_id}/execute",
        headers=REVIEWER_HEADERS,
        json={"expectedFingerprint": fingerprint},
    )
    assert executed.status_code == 200
    assert executed.json()["status"] == "applied"
    assert executed.json()["appliedOntologyVersion"]["versionNumber"] == 2

    listed = client.get("/api/ontology/proposals", headers=SUBMITTER_HEADERS, params={"status": "applied"})
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [proposal_id]

    detail = client.get(f"/api/ontology/proposals/{proposal_id}", headers=SUBMITTER_HEADERS)
    assert detail.status_code == 200
    assert [event["event"] for event in detail.json()["timeline"]] == ["submitted", "assigned", "decided", "applied"]


def test_api_proposal_error_mapping(foundry, tmp_path, monkeypatch: MonkeyPatch) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)

    missing_header = client.post(
        "/api/ontology/proposals",
        headers=SUBMITTER_HEADERS,
        json={"yamlText": _yaml_text(_order_definition()), "title": "no key"},
    )
    assert missing_header.status_code == 422

    oversized = client.post(
        "/api/ontology/proposals",
        headers={**SUBMITTER_HEADERS, "Idempotency-Key": "oversized-key"},
        json={"yamlText": "a" * (1_048_576 + 1), "title": "too big"},
    )
    assert oversized.status_code == 400
    assert oversized.json()["detail"]["code"] == "VALIDATION_FAILED"
    assert oversized.json()["detail"]["message"] == "ontology yaml text exceeds size limit"

    viewer_denied = client.post(
        "/api/ontology/proposals",
        headers={**VIEWER_HEADERS, "Idempotency-Key": "viewer-key"},
        json={"yamlText": _yaml_text(_order_definition()), "title": "viewer"},
    )
    assert viewer_denied.status_code == 403
    assert viewer_denied.json()["detail"]["code"] == "PERMISSION_DENIED"

    submitted = client.post(
        "/api/ontology/proposals",
        headers={**SUBMITTER_HEADERS, "Idempotency-Key": "conflict-key"},
        json={"yamlText": _yaml_text(_order_definition()), "title": "conflict"},
    ).json()
    stale = client.post(
        f"/api/ontology/proposals/{submitted['id']}/decide",
        headers=REVIEWER_HEADERS,
        json={"decision": "approve", "expectedFingerprint": "sha256:stale"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "CONFLICT"

    own_approval = client.post(
        f"/api/ontology/proposals/{submitted['id']}/decide",
        headers=SUBMITTER_HEADERS,
        json={"decision": "approve", "expectedFingerprint": submitted["fingerprint"]},
    )
    assert own_approval.status_code == 403
