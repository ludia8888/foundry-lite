"""Unit tests for ``ApprovalExecutionService`` (AIP-lite P0h, §8.10/§12.2)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from foundry_lite.application.ports.ai_run_repository import AiContextItemRecord, AiExecutionRunRecord, AiSessionRecord
from foundry_lite.application.services.aip.approval_execution import ApprovalExecutionError
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyAiRunRepository
from sqlalchemy import func, select, update

from tests.conftest import prepare_indexed_demo

_CTX = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="ops-user",
    roles=("admin", "ops_manager", "data_engineer"),
    request_id="req-approval-execution",
)
_VIEWER_CTX = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="viewer-user",
    roles=("viewer",),
    request_id="req-approval-execution-viewer",
)


def test_approval_execution_validates_request_shape(foundry: Any) -> None:
    with pytest.raises(ApprovalExecutionError) as missing_review:
        foundry.aip.execute_approved_action(
            review_id="",
            expected_proposal_fingerprint="sha256:" + "1" * 64,
            ctx=_CTX,
        )
    with pytest.raises(ApprovalExecutionError) as missing_prefix:
        foundry.aip.execute_approved_action(
            review_id="review-1",
            expected_proposal_fingerprint="1" * 64,
            ctx=_CTX,
        )

    assert missing_review.value.reason == "missing_field"
    assert missing_prefix.value.reason == "missing_field"


def test_approval_execution_runs_approved_proposal_once_and_links_review(foundry: Any) -> None:
    ctx = prepare_indexed_demo(foundry)
    proposal = _approved_proposal(foundry, ctx)
    before_action_runs = _table_count(foundry.engine, db.action_runs)

    result = foundry.aip.execute_approved_action(
        review_id=proposal.review_id,
        expected_proposal_fingerprint=proposal.proposal_fingerprint,
        ctx=_CTX,
    )
    replay = foundry.aip.execute_approved_action(
        review_id=proposal.review_id,
        expected_proposal_fingerprint=proposal.proposal_fingerprint,
        ctx=_CTX,
    )

    row = _review_row(foundry.engine, proposal.review_id)
    relations = _relations(foundry.engine, proposal.review_id, result.action_run_id)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    assert result.action_response["actionRunId"] == result.action_run_id
    assert replay.action_run_id == result.action_run_id
    assert replay.action_response["idempotentReplay"] is True
    assert _table_count(foundry.engine, db.action_runs) == before_action_runs + 1
    assert order["objectVersion"] == 2
    assert row["execution_status"] == "executed"
    assert row["approved_action_run_id"] == result.action_run_id
    assert result.review_payload["approvedActionRunId"] == result.action_run_id
    assert relations[0]["relation"] == "approved_as"
    assert relations[0]["metadata"]["proposalFingerprint"] == proposal.proposal_fingerprint


def test_approval_execution_requires_approved_review_and_matching_fingerprint(foundry: Any) -> None:
    ctx = prepare_indexed_demo(foundry)
    proposal = _propose(foundry, ctx)

    with pytest.raises(ApprovalExecutionError) as not_approved:
        foundry.aip.execute_approved_action(
            review_id=proposal.review_id,
            expected_proposal_fingerprint=proposal.proposal_fingerprint,
            ctx=_CTX,
        )
    foundry.insights.decide(proposal.review_id, decision="approved", idempotency_key="approve-p0h", ctx=_CTX)
    with pytest.raises(ApprovalExecutionError) as fingerprint_error:
        foundry.aip.execute_approved_action(
            review_id=proposal.review_id,
            expected_proposal_fingerprint="sha256:" + "0" * 64,
            ctx=_CTX,
        )

    assert not_approved.value.reason == "review_not_approved"
    assert fingerprint_error.value.reason == "fingerprint_mismatch"
    assert _table_count(foundry.engine, db.action_runs) == 0


def test_approval_execution_requires_reviewer_permission(foundry: Any) -> None:
    ctx = prepare_indexed_demo(foundry)
    proposal = _approved_proposal(foundry, ctx)

    with pytest.raises(ApprovalExecutionError) as excinfo:
        foundry.aip.execute_approved_action(
            review_id=proposal.review_id,
            expected_proposal_fingerprint=proposal.proposal_fingerprint,
            ctx=_VIEWER_CTX,
        )

    assert excinfo.value.reason == "policy_denied"
    assert _review_row(foundry.engine, proposal.review_id)["execution_status"] == "pending_review"
    assert _table_count(foundry.engine, db.action_runs) == 0


def test_approval_rechecks_object_version_before_action_run(foundry: Any) -> None:
    ctx = prepare_indexed_demo(foundry)
    proposal = _approved_proposal(foundry, ctx)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=int(order["objectVersion"]),
        params={"reason": "Manual approval changed the object first"},
        idempotency_key="manual-before-approval-execution",
        ctx=ctx,
    )
    before_bridge_attempt = _table_count(foundry.engine, db.action_runs)

    with pytest.raises(ApprovalExecutionError) as excinfo:
        foundry.aip.execute_approved_action(
            review_id=proposal.review_id,
            expected_proposal_fingerprint=proposal.proposal_fingerprint,
            ctx=_CTX,
        )

    row = _review_row(foundry.engine, proposal.review_id)
    assert excinfo.value.reason == "approval_object_version_conflict"
    assert row["execution_status"] == "pending_review"
    assert row["approved_action_run_id"] is None
    assert _table_count(foundry.engine, db.action_runs) == before_bridge_attempt


def test_approval_execution_rejects_expired_review_before_action(foundry: Any) -> None:
    ctx = prepare_indexed_demo(foundry)
    proposal = _approved_proposal(foundry, ctx, expires_at="2000-01-01T00:00:00+00:00")

    with pytest.raises(ApprovalExecutionError) as excinfo:
        foundry.aip.execute_approved_action(
            review_id=proposal.review_id,
            expected_proposal_fingerprint=proposal.proposal_fingerprint,
            ctx=_CTX,
        )

    assert excinfo.value.reason == "review_expired"
    assert _review_row(foundry.engine, proposal.review_id)["execution_status"] == "pending_review"
    assert _table_count(foundry.engine, db.action_runs) == 0


def test_approval_execution_reloads_originating_ai_run_before_action(foundry: Any) -> None:
    ctx = prepare_indexed_demo(foundry)
    proposal = _approved_proposal(foundry, ctx)
    _update_review(foundry.engine, proposal.review_id, originating_ai_run_id="missing-ai-run")

    with pytest.raises(ApprovalExecutionError) as excinfo:
        foundry.aip.execute_approved_action(
            review_id=proposal.review_id,
            expected_proposal_fingerprint=proposal.proposal_fingerprint,
            ctx=_CTX,
        )

    assert excinfo.value.reason == "run_not_found"
    assert _review_row(foundry.engine, proposal.review_id)["execution_status"] == "pending_review"
    assert _table_count(foundry.engine, db.action_runs) == 0


@pytest.mark.parametrize(
    ("proposal_patch", "expected_reason"),
    [
        ({"actionType": "MissingAction"}, "action_not_found"),
        ({"targetObjectType": "Customer"}, "action_target_mismatch"),
        ({"targetObjectId": "missing-order"}, "target_not_found"),
        ({"evidenceRefs": []}, "missing_evidence"),
        ({"evidenceRefs": ["not-an-object"]}, "invalid_proposal"),
        ({"expectedObjectVersion": "1"}, "invalid_proposal"),
        ({"parameters": "not-a-mapping"}, "invalid_proposal"),
        ({"expiresAt": "not-a-date"}, "invalid_expiration"),
    ],
)
def test_approval_execution_rejects_mutated_proposal_payloads(
    foundry: Any,
    proposal_patch: Mapping[str, object],
    expected_reason: str,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    proposal = _approved_proposal(foundry, ctx)
    _patch_action_proposal(foundry.engine, proposal.review_id, proposal_patch)

    with pytest.raises(ApprovalExecutionError) as excinfo:
        foundry.aip.execute_approved_action(
            review_id=proposal.review_id,
            expected_proposal_fingerprint=proposal.proposal_fingerprint,
            ctx=_CTX,
        )

    assert excinfo.value.reason == expected_reason
    assert _review_row(foundry.engine, proposal.review_id)["execution_status"] == "pending_review"
    assert _table_count(foundry.engine, db.action_runs) == 0


def test_approval_execution_rejects_policy_version_drift(foundry: Any) -> None:
    ctx = prepare_indexed_demo(foundry)
    proposal = _approved_proposal(foundry, ctx)
    _update_review(foundry.engine, proposal.review_id, approval_policy_version="policy-v2")

    with pytest.raises(ApprovalExecutionError) as excinfo:
        foundry.aip.execute_approved_action(
            review_id=proposal.review_id,
            expected_proposal_fingerprint=proposal.proposal_fingerprint,
            ctx=_CTX,
        )

    assert excinfo.value.reason == "fingerprint_mismatch"
    assert _review_row(foundry.engine, proposal.review_id)["execution_status"] == "pending_review"


def test_approval_execution_rechecks_source_access_before_action(foundry: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = prepare_indexed_demo(foundry)
    proposal = _approved_proposal(foundry, ctx)
    original_require = foundry.policy.require

    def deny_object_read(ctx: RequestContext, permission: str) -> None:
        if permission == "object:read":
            raise PermissionDenied("permission denied for object:read")
        original_require(ctx, permission)

    monkeypatch.setattr(foundry.policy, "require", deny_object_read)

    with pytest.raises(ApprovalExecutionError) as excinfo:
        foundry.aip.execute_approved_action(
            review_id=proposal.review_id,
            expected_proposal_fingerprint=proposal.proposal_fingerprint,
            ctx=_CTX,
        )

    assert excinfo.value.reason == "source_access_denied"
    assert _review_row(foundry.engine, proposal.review_id)["execution_status"] == "pending_review"
    assert _table_count(foundry.engine, db.action_runs) == 0


def test_approval_execution_marks_review_failed_when_action_fails(
    foundry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = prepare_indexed_demo(foundry)
    proposal = _approved_proposal(foundry, ctx)
    service = foundry._services.approval_execution  # noqa: SLF001 - test hooks the composition boundary.

    def fail_apply_action(_action_api_name: str, **_kwargs: object) -> Mapping[str, object]:
        raise RuntimeError("simulated action outage")

    monkeypatch.setattr(service.action_service, "apply_action", fail_apply_action)

    with pytest.raises(RuntimeError, match="simulated action outage"):
        foundry.aip.execute_approved_action(
            review_id=proposal.review_id,
            expected_proposal_fingerprint=proposal.proposal_fingerprint,
            ctx=_CTX,
        )

    row = _review_row(foundry.engine, proposal.review_id)
    assert row["execution_status"] == "failed"
    assert row["approved_action_run_id"] is None
    assert row["review_metadata"]["executionError"]["type"] == "RuntimeError"
    assert _table_count(foundry.engine, db.action_runs) == 0


def _approved_proposal(foundry: Any, ctx: RequestContext, *, expires_at: str = "2999-01-01T00:00:00+00:00"):
    proposal = _propose(foundry, ctx, expires_at=expires_at)
    foundry.insights.decide(
        proposal.review_id,
        decision="approved",
        idempotency_key=f"approve-{proposal.review_id}",
        ctx=_CTX,
    )
    return proposal


def _propose(foundry: Any, ctx: RequestContext, *, expires_at: str = "2999-01-01T00:00:00+00:00"):
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    _seed_ai_run(foundry.engine)
    return foundry.aip.propose_action(
        originating_ai_run_id="ai-run-approval-1",
        action_type="ApproveOrder",
        target_object_type="Order",
        target_object_id="O-1001",
        expected_object_version=int(order["objectVersion"]),
        parameters={"reason": "Inventory confirmed by approved AI proposal"},
        evidence_context_ids=("ctx-order-approval-1",),
        agent_allowed_actions=("ApproveOrder",),
        policy_version="policy-v1",
        expires_at=expires_at,
        claim_text="Approve O-1001 based on reviewed AI evidence.",
        originating_tool_call_id="tool-call-approval-1",
        ctx=_CTX,
    )


def _seed_ai_run(engine: Any) -> None:
    repository = SqlAlchemyAiRunRepository(engine)
    with engine.begin() as transaction:
        repository.create_session(transaction=transaction, record=_session_record())
        repository.create_execution_run(transaction=transaction, record=_run_record())
        repository.record_context_item(transaction=transaction, record=_context_item_record())


def _session_record() -> AiSessionRecord:
    return AiSessionRecord(
        id="ai-session-approval-1",
        tenant_id="tenant-demo",
        agent_version_id="agent-v1",
        actor_user_id="ops-user",
        status="active",
        created_at="2026-06-25T00:00:00Z",
        last_activity_at="2026-06-25T00:00:01Z",
    )


def _run_record() -> AiExecutionRunRecord:
    return AiExecutionRunRecord(
        id="ai-run-approval-1",
        tenant_id="tenant-demo",
        session_id="ai-session-approval-1",
        agent_version_id="agent-v1",
        actor_user_id="ops-user",
        request_id="req-approval-execution",
        trace_id="trace-approval-execution",
        status="running",
        ontology_version_id="ontology-v1",
        model_alias_version="gpt-4.1@2026-06-25",
        resolved_model_id="gpt-4.1",
        resolved_model_revision="2026-06-25",
        prompt_version_id="prompt-v1",
        compiled_prompt_hash="sha256:prompt",
        tool_manifest_hash="sha256:tools",
        context_manifest_hash="sha256:context-manifest",
        state_snapshot_hash="sha256:state",
        policy_snapshot_hash="sha256:policy",
        budget_json={"maxTokens": 512},
        usage_json=None,
        error_json=None,
        started_at="2026-06-25T00:00:02Z",
        completed_at=None,
    )


def _context_item_record() -> AiContextItemRecord:
    return AiContextItemRecord(
        id="context-item-approval-1",
        tenant_id="tenant-demo",
        ai_run_id="ai-run-approval-1",
        context_id="ctx-order-approval-1",
        kind="object",
        source_resource_type="object",
        source_resource_id="O-1001",
        source_version="object-version-1",
        content_hash="sha256:context",
        retrieval_method="object-read",
        relevance_score=1.0,
        security_partition="tenant-demo:internal",
        token_estimate=24,
        is_selected=True,
        omission_reason=None,
    )


def _review_row(engine: Any, review_id: str) -> Mapping[str, Any]:
    with engine.begin() as transaction:
        return (
            transaction.execute(select(db.insight_reviews).where(db.insight_reviews.c.id == review_id)).mappings().one()
        )


def _update_review(engine: Any, review_id: str, **values: object) -> None:
    with engine.begin() as transaction:
        transaction.execute(update(db.insight_reviews).where(db.insight_reviews.c.id == review_id).values(**values))


def _patch_action_proposal(engine: Any, review_id: str, patch: Mapping[str, object]) -> None:
    row = _review_row(engine, review_id)
    proposal = {**row["action_proposal"], **patch}
    _update_review(engine, review_id, action_proposal=proposal)


def _relations(engine: Any, review_id: str, action_run_id: str) -> list[Mapping[str, Any]]:
    with engine.begin() as transaction:
        return list(
            transaction.execute(
                select(db.runtime_run_relations).where(
                    db.runtime_run_relations.c.source_run_id == review_id,
                    db.runtime_run_relations.c.target_run_id == action_run_id,
                )
            )
            .mappings()
            .all()
        )


def _table_count(engine: Any, table: Any) -> int:
    with engine.begin() as transaction:
        return int(transaction.execute(select(func.count()).select_from(table)).scalar_one())
