"""Ontology change proposals: submit, assign, decide, execute, withdraw governance flow."""

from __future__ import annotations

from typing import Any, cast

import yaml
from fastapi.testclient import TestClient
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.runtime_profile import RuntimeProfile
from foundry_lite.application.services.aip.governed_release_outcomes import (
    GovernedReleaseMutationOutcomeUnknown,
)
from foundry_lite.application.services.ontology_proposal_recovery import (
    OntologyProposalExecutionOutcomeUnknown,
)
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import (
    ConflictDetected,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app
from pytest import MonkeyPatch, mark, raises
from sqlalchemy import select, update

ORDERS_CSV = "order_id,status\nO-1,PENDING\n"

SUBMITTER = RequestContext(actor_user_id="user-submitter", roles=("data_engineer",))
REVIEWER = RequestContext(actor_user_id="user-reviewer", roles=("data_engineer",))
OTHER_REVIEWER = RequestContext(actor_user_id="user-other-reviewer", roles=("data_engineer",))
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


def _assign(foundry: FoundryLite, proposal: dict[str, object]) -> dict[str, object]:
    return foundry.ontology.assign_proposal(
        str(proposal["id"]),
        reviewer_user_id=REVIEWER.actor_user_id,
        ctx=REVIEWER,
    )


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


def test_submitter_may_be_assigned_and_approve_own_proposal(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)

    assigned = foundry.ontology.assign_proposal(
        str(proposal["id"]),
        reviewer_user_id=SUBMITTER.actor_user_id,
        ctx=SUBMITTER,
    )
    assert assigned["assigneeUserId"] == SUBMITTER.actor_user_id
    approved = foundry.ontology.decide_proposal(
        str(proposal["id"]),
        decision="approve",
        expected_fingerprint=_fingerprint(proposal),
        ctx=SUBMITTER,
    )
    assert approved["status"] == "approved"
    assert approved["decision"]["decidedByUserId"] == SUBMITTER.actor_user_id
    with raises(PermissionDenied, match="assigned human reviewer"):
        foundry.ontology.decide_proposal(
            str(proposal["id"]),
            decision="approve",
            expected_fingerprint=_fingerprint(proposal),
            ctx=REVIEWER,
        )


def test_protected_runtime_rejects_self_assignment_and_legacy_self_approval_execution(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry, key="protected-reviewer-separation")
    proposal_service = foundry._services.ontology.proposals
    proposal_service.profile = RuntimeProfile.from_value("production")

    with raises(PermissionDenied, match="reviewer other than"):
        foundry.ontology.assign_proposal(
            str(proposal["id"]),
            reviewer_user_id=SUBMITTER.actor_user_id,
            ctx=SUBMITTER,
        )

    proposal_service.profile = RuntimeProfile.from_value("local")
    foundry.ontology.assign_proposal(
        str(proposal["id"]),
        reviewer_user_id=SUBMITTER.actor_user_id,
        ctx=SUBMITTER,
    )
    foundry.ontology.decide_proposal(
        str(proposal["id"]),
        decision="approve",
        expected_fingerprint=_fingerprint(proposal),
        ctx=SUBMITTER,
    )
    proposal_service.profile = RuntimeProfile.from_value("production")

    with raises(PermissionDenied, match="approval evidence"):
        foundry.ontology.execute_proposal(
            str(proposal["id"]),
            expected_fingerprint=_fingerprint(proposal),
            ctx=SUBMITTER,
        )


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
    _assign(foundry, proposal)

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
    _assign(foundry, proposal)
    foundry.ontology.decide_proposal(
        proposal_id, decision="approve", expected_fingerprint=_fingerprint(proposal), ctx=REVIEWER
    )
    with raises(ConflictDetected, match="fingerprint mismatch"):
        foundry.ontology.execute_proposal(proposal_id, expected_fingerprint="sha256:stale", ctx=REVIEWER)


def test_duplicate_execute_replays_without_reapplying(foundry, tmp_path) -> None:
    admin = _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)
    proposal_id = str(proposal["id"])
    _assign(foundry, proposal)
    foundry.ontology.decide_proposal(
        proposal_id, decision="approve", expected_fingerprint=_fingerprint(proposal), ctx=REVIEWER
    )
    first = foundry.ontology.execute_proposal(proposal_id, expected_fingerprint=_fingerprint(proposal), ctx=REVIEWER)
    replay = foundry.ontology.execute_proposal(proposal_id, expected_fingerprint=_fingerprint(proposal), ctx=REVIEWER)

    assert replay["status"] == "applied"
    assert replay["appliedOntologyVersion"] == first["appliedOntologyVersion"]
    # The apply black box ran exactly once: the catalog stays at version 2.
    assert foundry.ontology.catalog(ctx=admin)["versionNumber"] == 2


def test_process_death_after_ontology_apply_reconciles_the_committed_version(
    foundry,
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    admin = _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry, key="submit-crash-recovery")
    proposal_id = str(proposal["id"])
    _assign(foundry, proposal)
    foundry.ontology.decide_proposal(
        proposal_id,
        decision="approve",
        expected_fingerprint=_fingerprint(proposal),
        ctx=REVIEWER,
    )

    def crash_before_execution_receipt(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise SystemExit("simulated process death after ontology activation")

    finish_execution = foundry.ontology._proposals._finish_execution
    monkeypatch.setattr(foundry.ontology._proposals, "_finish_execution", crash_before_execution_receipt)
    with raises(SystemExit, match="simulated process death"):
        foundry.ontology.execute_proposal(
            proposal_id,
            expected_fingerprint=_fingerprint(proposal),
            ctx=REVIEWER,
        )

    interrupted = foundry.ontology.get_proposal(proposal_id, ctx=REVIEWER)
    assert interrupted["executionStatus"] == "executing"
    assert foundry.ontology.catalog(ctx=admin)["versionNumber"] == 2
    monkeypatch.setattr(foundry.ontology._proposals, "_finish_execution", finish_execution)

    recovered = foundry.ontology.execute_proposal(
        proposal_id,
        expected_fingerprint=_fingerprint(proposal),
        ctx=REVIEWER,
    )

    assert recovered["status"] == "applied"
    assert cast(dict[str, Any], recovered["appliedOntologyVersion"])["versionNumber"] == 2
    assert foundry.ontology.catalog(ctx=admin)["versionNumber"] == 2
    detail = foundry.ontology.get_proposal(proposal_id, ctx=REVIEWER)
    assert [event["event"] for event in detail["timeline"]].count("applied") == 1


def test_executing_proposal_does_not_claim_an_unrelated_same_yaml_activation(
    foundry,
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    admin = _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry, key="same-yaml-attribution")
    proposal_id = str(proposal["id"])
    _assign(foundry, proposal)
    foundry.ontology.decide_proposal(
        proposal_id,
        decision="approve",
        expected_fingerprint=_fingerprint(proposal),
        ctx=REVIEWER,
    )
    apply_once = foundry.ontology._proposals.ontology_service.apply_ontology_text_once

    def stop_before_proposal_apply(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise SystemExit("stop after execution claim")

    monkeypatch.setattr(
        foundry.ontology._proposals.ontology_service,
        "apply_ontology_text_once",
        stop_before_proposal_apply,
    )
    with raises(SystemExit, match="execution claim"):
        foundry.ontology.execute_proposal(
            proposal_id,
            expected_fingerprint=_fingerprint(proposal),
            ctx=REVIEWER,
        )

    foundry.ontology.apply_text(
        _yaml_text(_order_definition(extra_properties=[_note_property()])),
        ctx=admin,
    )
    assert foundry.ontology.catalog(ctx=admin)["versionNumber"] == 2

    monkeypatch.setattr(foundry.ontology._proposals.ontology_service, "apply_ontology_text_once", apply_once)
    recovered = foundry.ontology.execute_proposal(
        proposal_id,
        expected_fingerprint=_fingerprint(proposal),
        ctx=REVIEWER,
    )

    assert cast(dict[str, Any], recovered["appliedOntologyVersion"])["versionNumber"] == 3
    assert foundry.ontology.catalog(ctx=admin)["versionNumber"] == 3
    receipts = [
        event
        for event in foundry.operations.list_runs(ctx=admin)["outboxEvents"]
        if event["idempotency_key"] == f"ontology.activation:{proposal_id}:execute"
    ]
    assert len(receipts) == 1
    assert cast(dict[str, Any], receipts[0]["payload"])["result"]["version_number"] == 3


def test_governed_release_recovers_finish_fault_after_durable_ontology_activation(
    foundry,
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    admin = _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry, key="submit-finish-fault")
    proposal_id = str(proposal["id"])
    _assign(foundry, proposal)
    foundry.ontology.decide_proposal(
        proposal_id,
        decision="approve",
        expected_fingerprint=_fingerprint(proposal),
        ctx=REVIEWER,
    )
    proposal_service = foundry.ontology._proposals
    finish_execution = proposal_service._finish_execution

    def fail_finish(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("proposal completion projection failed")

    monkeypatch.setattr(proposal_service, "_finish_execution", fail_finish)
    arguments = {
        "releaseKind": "ontology",
        "proposalId": proposal_id,
        "expectedFingerprint": _fingerprint(proposal),
        "idempotencyKey": "ontology-finish-fault",
    }
    with raises(GovernedReleaseMutationOutcomeUnknown):
        foundry.release._gateway.release_service.execute_approved(REVIEWER, arguments)

    assert foundry.ontology.catalog(ctx=admin)["versionNumber"] == 2
    assert foundry.ontology.get_proposal(proposal_id, ctx=REVIEWER)["executionStatus"] == "executing"

    monkeypatch.setattr(proposal_service, "_finish_execution", finish_execution)
    recovered = foundry.release._gateway.release_service.execute_approved(REVIEWER, arguments)

    assert recovered["stage"] == "active"
    assert foundry.ontology.catalog(ctx=admin)["versionNumber"] == 2
    detail = foundry.ontology.get_proposal(proposal_id, ctx=REVIEWER)
    assert [event["event"] for event in detail["timeline"]].count("applied") == 1
    evidence = foundry.operations.list_runs(ctx=admin)
    receipts = [
        event
        for event in evidence["outboxEvents"]
        if event["event_type"] == "ontology.activation.completed"
        and event["idempotency_key"] == f"ontology.activation:{proposal_id}:execute"
    ]
    assert len(receipts) == 1


def test_ontology_apply_commit_ack_loss_reconciles_exact_receipt_instead_of_marking_failed(
    foundry,
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    admin = _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry, key="apply-commit-ack-loss")
    proposal_id = str(proposal["id"])
    _assign(foundry, proposal)
    foundry.ontology.decide_proposal(
        proposal_id,
        decision="approve",
        expected_fingerprint=_fingerprint(proposal),
        ctx=REVIEWER,
    )
    ontology_service = foundry.ontology._proposals.ontology_service
    apply_once = ontology_service.apply_ontology_text_once

    def commit_then_lose_ack(*args: object, **kwargs: object) -> dict[str, object]:
        apply_once(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("database commit acknowledgement was lost")

    monkeypatch.setattr(ontology_service, "apply_ontology_text_once", commit_then_lose_ack)
    recovered = foundry.ontology.execute_proposal(
        proposal_id,
        expected_fingerprint=_fingerprint(proposal),
        ctx=REVIEWER,
    )

    assert recovered["status"] == "applied"
    assert recovered["executionStatus"] == "executed"
    assert foundry.ontology.catalog(ctx=admin)["versionNumber"] == 2
    detail = foundry.ontology.get_proposal(proposal_id, ctx=REVIEWER)
    assert [event["event"] for event in detail["timeline"]].count("applied") == 1
    receipts = [
        event
        for event in foundry.operations.list_runs(ctx=admin)["outboxEvents"]
        if event["idempotency_key"] == f"ontology.activation:{proposal_id}:execute"
    ]
    assert len(receipts) == 1


def test_ontology_apply_failure_without_receipt_is_recorded_as_failed(
    foundry,
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry, key="apply-no-receipt")
    proposal_id = str(proposal["id"])
    _assign(foundry, proposal)
    foundry.ontology.decide_proposal(
        proposal_id,
        decision="approve",
        expected_fingerprint=_fingerprint(proposal),
        ctx=REVIEWER,
    )

    def fail_before_commit(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise ValidationFailed("activation validation failed before commit")

    monkeypatch.setattr(foundry.ontology._proposals.ontology_service, "apply_ontology_text_once", fail_before_commit)
    with raises(ValidationFailed, match="before commit"):
        foundry.ontology.execute_proposal(
            proposal_id,
            expected_fingerprint=_fingerprint(proposal),
            ctx=REVIEWER,
        )

    assert foundry.ontology.get_proposal(proposal_id, ctx=REVIEWER)["executionStatus"] == "failed"


def test_ontology_apply_failure_scrubs_secret_from_review_and_audit_evidence(
    foundry,
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry, key="apply-secret-redaction")
    proposal_id = str(proposal["id"])
    _assign(foundry, proposal)
    foundry.ontology.decide_proposal(
        proposal_id,
        decision="approve",
        expected_fingerprint=_fingerprint(proposal),
        ctx=REVIEWER,
    )

    def fail_before_commit(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise ValidationFailed("Authorization: Bearer raw-ontology-token password=raw-ontology-password")

    monkeypatch.setattr(foundry.ontology._proposals.ontology_service, "apply_ontology_text_once", fail_before_commit)
    with raises(ValidationFailed, match="raw-ontology-token"):
        foundry.ontology.execute_proposal(
            proposal_id,
            expected_fingerprint=_fingerprint(proposal),
            ctx=REVIEWER,
        )

    with foundry.engine.begin() as transaction:
        review_metadata = transaction.execute(
            select(db.insight_reviews.c.review_metadata).where(db.insight_reviews.c.id == proposal_id)
        ).scalar_one()
        audit_after_ref = transaction.execute(
            select(db.audit_events.c.after_ref).where(
                db.audit_events.c.resource_id == proposal_id,
                db.audit_events.c.event_type == "ontology.proposal.apply_failed",
            )
        ).scalar_one()
    assert review_metadata["executionError"]["message"] == "***MASKED***"
    assert audit_after_ref["error"] == "***MASKED***"
    assert "raw-ontology" not in str((review_metadata, audit_after_ref))


def test_ontology_receipt_lookup_failure_preserves_executing_for_safe_recovery(
    foundry,
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry, key="receipt-lookup-failure")
    proposal_id = str(proposal["id"])
    _assign(foundry, proposal)
    foundry.ontology.decide_proposal(
        proposal_id,
        decision="approve",
        expected_fingerprint=_fingerprint(proposal),
        ctx=REVIEWER,
    )

    def lose_apply_ack(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("activation outcome unknown")

    def fail_receipt_lookup(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("receipt store unavailable")

    proposals = foundry.ontology._proposals
    monkeypatch.setattr(proposals.ontology_service, "apply_ontology_text_once", lose_apply_ack)
    monkeypatch.setattr(proposals.runtime_service, "_outbox_event_by_idempotency_key", fail_receipt_lookup)
    with raises(OntologyProposalExecutionOutcomeUnknown, match="activation committed"):
        foundry.ontology.execute_proposal(
            proposal_id,
            expected_fingerprint=_fingerprint(proposal),
            ctx=REVIEWER,
        )

    assert foundry.ontology.get_proposal(proposal_id, ctx=REVIEWER)["executionStatus"] == "executing"


def test_execute_requires_an_approved_proposal(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)

    with raises(ConflictDetected, match="only approved ontology proposals"):
        foundry.ontology.execute_proposal(
            str(proposal["id"]), expected_fingerprint=_fingerprint(proposal), ctx=REVIEWER
        )


@mark.parametrize(
    "decision_patch",
    [
        {"decision": "rejected"},
        {"decidedByUserId": "user-other-reviewer"},
        {"hasBlockedChanges": True},
        {"migrationPlan": {"status": "blocked", "blockedChanges": [{"kind": "property_removed"}]}},
    ],
)
def test_execute_rejects_corrupt_independent_approval_evidence(foundry, tmp_path, decision_patch) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)
    proposal_id = str(proposal["id"])
    _assign(foundry, proposal)
    approved = foundry.ontology.decide_proposal(
        proposal_id,
        decision="approve",
        expected_fingerprint=_fingerprint(proposal),
        ctx=REVIEWER,
    )
    corrupted = {**cast(dict[str, object], approved["decision"]), **decision_patch}
    with foundry.engine.begin() as conn:
        conn.execute(
            update(db.insight_reviews).where(db.insight_reviews.c.id == proposal_id).values(decision=corrupted)
        )

    with raises(PermissionDenied, match="approval evidence"):
        foundry.ontology.execute_proposal(
            proposal_id,
            expected_fingerprint=_fingerprint(proposal),
            ctx=REVIEWER,
        )


def test_decision_replay_and_conflicting_second_decision(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)
    proposal_id = str(proposal["id"])
    _assign(foundry, proposal)
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


def _update_yaml() -> str:
    return _yaml_text(_order_definition(extra_properties=[_note_property(), _note_property("note2")]))


def test_submitter_update_recomputes_plan_and_new_fingerprint_gates_decide(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)
    proposal_id = str(proposal["id"])

    updated = foundry.ontology.update_proposal(
        proposal_id,
        yaml_text=_update_yaml(),
        expected_fingerprint=_fingerprint(proposal),
        title="Add two notes",
        description="second draft",
        ctx=SUBMITTER,
    )
    assert updated["status"] == "submitted"
    assert updated["fingerprint"] != proposal["fingerprint"]
    assert updated["title"] == "Add two notes"
    assert updated["revisionCount"] == 1
    assert updated["lastRevisedAt"] is not None

    detail = foundry.ontology.get_proposal(proposal_id, ctx=SUBMITTER)
    assert "note2" in str(detail["yamlText"])
    assert detail["description"] == "second draft"
    assert cast(dict[str, Any], detail["submittedMigrationPlan"])["status"] == "compatible"
    assert {"event": "revised", "count": 1}.items() <= detail["timeline"][1].items()

    with raises(ConflictDetected, match="fingerprint mismatch"):
        foundry.ontology.decide_proposal(
            proposal_id, decision="approve", expected_fingerprint=_fingerprint(proposal), ctx=REVIEWER
        )
    _assign(foundry, updated)
    approved = foundry.ontology.decide_proposal(
        proposal_id, decision="approve", expected_fingerprint=_fingerprint(updated), ctx=REVIEWER
    )
    assert approved["status"] == "approved"


def test_update_is_submitter_only(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)

    with raises(PermissionDenied, match="only the submitter may update"):
        foundry.ontology.update_proposal(
            str(proposal["id"]),
            yaml_text=_update_yaml(),
            expected_fingerprint=_fingerprint(proposal),
            ctx=REVIEWER,
        )
    with raises(PermissionDenied):
        foundry.ontology.update_proposal(
            str(proposal["id"]),
            yaml_text=_update_yaml(),
            expected_fingerprint=_fingerprint(proposal),
            ctx=VIEWER,
        )


def test_update_conflicts_after_decide_withdraw_and_execute(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    rejected = _submit(foundry, key="upd-rejected", title="Rejected")
    _assign(foundry, rejected)
    foundry.ontology.decide_proposal(
        str(rejected["id"]), decision="reject", expected_fingerprint=_fingerprint(rejected), ctx=REVIEWER
    )
    withdrawn = _submit(foundry, key="upd-withdrawn", title="Withdrawn")
    foundry.ontology.withdraw_proposal(str(withdrawn["id"]), ctx=SUBMITTER)
    executed = _submit(foundry, key="upd-executed", title="Executed")
    _assign(foundry, executed)
    foundry.ontology.decide_proposal(
        str(executed["id"]), decision="approve", expected_fingerprint=_fingerprint(executed), ctx=REVIEWER
    )
    foundry.ontology.execute_proposal(str(executed["id"]), expected_fingerprint=_fingerprint(executed), ctx=REVIEWER)

    for proposal in (rejected, withdrawn, executed):
        with raises(ConflictDetected, match="can no longer be updated"):
            foundry.ontology.update_proposal(
                str(proposal["id"]),
                yaml_text=_update_yaml(),
                expected_fingerprint=_fingerprint(proposal),
                ctx=SUBMITTER,
            )


def test_update_with_stale_expected_fingerprint_conflicts(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)
    proposal_id = str(proposal["id"])
    _assign(foundry, proposal)
    foundry.ontology.update_proposal(
        proposal_id, yaml_text=_update_yaml(), expected_fingerprint=_fingerprint(proposal), ctx=SUBMITTER
    )

    with raises(ConflictDetected, match="fingerprint mismatch") as exc_info:
        foundry.ontology.update_proposal(
            proposal_id,
            yaml_text=_yaml_text(_order_definition(extra_properties=[_note_property("note3")])),
            expected_fingerprint=_fingerprint(proposal),
            ctx=SUBMITTER,
        )
    assert exc_info.value.details["expectedFingerprint"] == _fingerprint(proposal)
    assert exc_info.value.details["proposalFingerprint"] != _fingerprint(proposal)


def test_update_rejects_oversized_or_invalid_yaml_and_empty_title(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)
    proposal_id = str(proposal["id"])
    expected = _fingerprint(proposal)

    with raises(ValidationFailed, match="exceeds size limit"):
        foundry.ontology.update_proposal(
            proposal_id, yaml_text="a" * (1_048_576 + 1), expected_fingerprint=expected, ctx=SUBMITTER
        )
    with raises(ValidationFailed):
        foundry.ontology.update_proposal(
            proposal_id,
            yaml_text="objectTypes:\n  - apiName: Broken\n",
            expected_fingerprint=expected,
            ctx=SUBMITTER,
        )
    with raises(ValidationFailed, match="title is required"):
        foundry.ontology.update_proposal(
            proposal_id, yaml_text=_update_yaml(), expected_fingerprint=expected, title="", ctx=SUBMITTER
        )
    # Nothing was stored by the rejected attempts.
    assert foundry.ontology.get_proposal(proposal_id, ctx=SUBMITTER)["fingerprint"] == expected


def test_update_replay_with_same_yaml_is_idempotent_without_new_audit(foundry, tmp_path) -> None:
    admin = _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)
    proposal_id = str(proposal["id"])
    first = foundry.ontology.update_proposal(
        proposal_id, yaml_text=_update_yaml(), expected_fingerprint=_fingerprint(proposal), ctx=SUBMITTER
    )
    replay = foundry.ontology.update_proposal(
        proposal_id, yaml_text=_update_yaml(), expected_fingerprint=_fingerprint(proposal), ctx=SUBMITTER
    )

    assert replay["fingerprint"] == first["fingerprint"]
    assert replay["revisionCount"] == 1
    revised_events = [
        event
        for event in foundry.operations.list_runs(ctx=admin)["auditEvents"]
        if event["event_type"] == "ontology.proposal.revised" and event["resource_id"] == proposal_id
    ]
    assert len(revised_events) == 1


def test_update_after_assignment_preserves_reviewer_and_bumps_revision_count(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)
    proposal_id = str(proposal["id"])
    foundry.ontology.assign_proposal(proposal_id, reviewer_user_id="user-reviewer", ctx=REVIEWER)

    first = foundry.ontology.update_proposal(
        proposal_id, yaml_text=_update_yaml(), expected_fingerprint=_fingerprint(proposal), ctx=SUBMITTER
    )
    assert first["status"] == "in_review"
    assert first["assigneeUserId"] == "user-reviewer"
    assert first["revisionCount"] == 1

    second = foundry.ontology.update_proposal(
        proposal_id,
        yaml_text=_yaml_text(_order_definition(extra_properties=[_note_property("note3")])),
        expected_fingerprint=_fingerprint(first),
        ctx=SUBMITTER,
    )
    assert second["assigneeUserId"] == "user-reviewer"
    assert second["revisionCount"] == 2
    detail = foundry.ontology.get_proposal(proposal_id, ctx=SUBMITTER)
    revised = [event for event in detail["timeline"] if event["event"] == "revised"]
    assert [event["count"] for event in revised] == [2]
    assert revised[0]["byUserId"] == "user-submitter"


def test_update_stores_blocked_plan_flagged_like_submit(foundry, tmp_path) -> None:
    admin = _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)
    # Direct apply activates the note property, so a revision without it would
    # remove an active property: blocked, but storable (flagged) like submit.
    foundry.ontology.apply_text(_yaml_text(_order_definition(extra_properties=[_note_property()])), ctx=admin)

    updated = foundry.ontology.update_proposal(
        str(proposal["id"]),
        yaml_text=_yaml_text(_order_definition()),
        expected_fingerprint=_fingerprint(proposal),
        ctx=SUBMITTER,
    )
    assert updated["hasBlockedChangesAtSubmit"] is True
    assert cast(dict[str, Any], updated["submittedMigrationPlan"])["status"] == "blocked"


def test_withdraw_is_submitter_only_and_blocks_later_review(foundry, tmp_path) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)
    proposal_id = str(proposal["id"])
    _assign(foundry, proposal)

    with raises(PermissionDenied, match="only the submitter"):
        foundry.ontology.withdraw_proposal(proposal_id, reason="not yours", ctx=REVIEWER)

    withdrawn = foundry.ontology.withdraw_proposal(proposal_id, reason="superseded", ctx=SUBMITTER)
    assert withdrawn["status"] == "withdrawn"
    assert cast(dict[str, Any], withdrawn["withdrawal"])["reason"] == "superseded"

    replay = foundry.ontology.withdraw_proposal(proposal_id, ctx=SUBMITTER)
    assert replay["status"] == "withdrawn"

    with raises(ConflictDetected):
        foundry.ontology.assign_proposal(
            proposal_id,
            reviewer_user_id=OTHER_REVIEWER.actor_user_id,
            ctx=OTHER_REVIEWER,
        )
    with raises(ConflictDetected):
        foundry.ontology.decide_proposal(
            proposal_id, decision="approve", expected_fingerprint=_fingerprint(proposal), ctx=REVIEWER
        )


def test_withdraw_covers_approved_but_not_applied_proposals(foundry, tmp_path) -> None:
    admin = _prepare_active_ontology(foundry, tmp_path)
    proposal = _submit(foundry)
    proposal_id = str(proposal["id"])
    _assign(foundry, proposal)
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
    _assign(foundry, proposal)
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


def test_api_proposal_update_happy_path_and_error_mapping(foundry, tmp_path, monkeypatch: MonkeyPatch) -> None:
    _prepare_active_ontology(foundry, tmp_path)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)
    proposal = client.post(
        "/api/ontology/proposals",
        headers={**SUBMITTER_HEADERS, "Idempotency-Key": "api-update-key"},
        json={"yamlText": _yaml_text(_order_definition(extra_properties=[_note_property()])), "title": "Add note"},
    ).json()
    proposal_id = proposal["id"]

    updated = client.post(
        f"/api/ontology/proposals/{proposal_id}/update",
        headers=SUBMITTER_HEADERS,
        json={"yamlText": _update_yaml(), "expectedFingerprint": proposal["fingerprint"], "title": "Add two notes"},
    )
    assert updated.status_code == 200
    assert updated.json()["fingerprint"] != proposal["fingerprint"]
    assert updated.json()["revisionCount"] == 1
    assert updated.json()["title"] == "Add two notes"

    non_submitter = client.post(
        f"/api/ontology/proposals/{proposal_id}/update",
        headers=REVIEWER_HEADERS,
        json={"yamlText": _update_yaml(), "expectedFingerprint": updated.json()["fingerprint"]},
    )
    assert non_submitter.status_code == 403
    assert non_submitter.json()["detail"]["code"] == "PERMISSION_DENIED"

    stale = client.post(
        f"/api/ontology/proposals/{proposal_id}/update",
        headers=SUBMITTER_HEADERS,
        json={
            "yamlText": _yaml_text(_order_definition(extra_properties=[_note_property("note3")])),
            "expectedFingerprint": proposal["fingerprint"],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "CONFLICT"

    assigned = client.post(
        f"/api/ontology/proposals/{proposal_id}/assign",
        headers=REVIEWER_HEADERS,
        json={"reviewerUserId": "user-reviewer"},
    )
    assert assigned.status_code == 200
    decided = client.post(
        f"/api/ontology/proposals/{proposal_id}/decide",
        headers=REVIEWER_HEADERS,
        json={"decision": "reject", "expectedFingerprint": updated.json()["fingerprint"]},
    )
    assert decided.status_code == 200
    after_decision = client.post(
        f"/api/ontology/proposals/{proposal_id}/update",
        headers=SUBMITTER_HEADERS,
        json={"yamlText": _update_yaml(), "expectedFingerprint": updated.json()["fingerprint"]},
    )
    assert after_decision.status_code == 409
    assert after_decision.json()["detail"]["code"] == "CONFLICT"


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

    assigned = client.post(
        f"/api/ontology/proposals/{submitted['id']}/assign",
        headers=REVIEWER_HEADERS,
        json={"reviewerUserId": "user-reviewer"},
    )
    assert assigned.status_code == 200

    own_approval = client.post(
        f"/api/ontology/proposals/{submitted['id']}/decide",
        headers=SUBMITTER_HEADERS,
        json={"decision": "approve", "expectedFingerprint": submitted["fingerprint"]},
    )
    assert own_approval.status_code == 403
