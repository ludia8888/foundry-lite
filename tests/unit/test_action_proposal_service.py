"""Unit tests for ``ActionProposalService`` (AIP-lite P0g, §8.10/§12.2)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from foundry_lite.application.ports.ai_run_repository import AiContextItemRecord, AiExecutionRunRecord, AiSessionRecord
from foundry_lite.application.services.aip.action_proposal import ActionProposalError
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyAiRunRepository
from sqlalchemy import func, select

from tests.conftest import prepare_indexed_demo

_CTX = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="ops-user",
    roles=("admin", "ops_manager", "data_engineer"),
    request_id="req-action-proposal",
)


def test_action_proposal_creates_review_from_selected_context_without_executing_action(
    foundry: Any,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    _seed_ai_run(foundry.engine)
    before_action_runs = _table_count(foundry.engine, db.action_runs)

    result = foundry.aip.propose_action(
        originating_ai_run_id="ai-run-1",
        action_type="ApproveOrder",
        target_object_type="Order",
        target_object_id="O-1001",
        expected_object_version=int(order["objectVersion"]),
        parameters={"reason": "Inventory confirmed"},
        evidence_context_ids=("ctx-order-1",),
        agent_allowed_actions=("ApproveOrder",),
        policy_version="policy-v1",
        expires_at="2026-06-25T00:10:00Z",
        claim_text="Approve O-1001 based on selected AI evidence.",
        originating_tool_call_id="tool-call-1",
        ctx=_CTX,
    )

    row = _review_row(foundry.engine, result.review_id)
    assert result.proposal_fingerprint.startswith("sha256:")
    assert result.review_payload["status"] == "pending"
    assert result.review_payload["executionStatus"] == "pending_review"
    assert row["proposal_fingerprint"] == result.proposal_fingerprint
    assert row["originating_ai_run_id"] == "ai-run-1"
    assert row["originating_tool_call_id"] == "tool-call-1"
    assert row["approved_action_run_id"] is None
    assert result.action_proposal["expectedObjectVersion"] == order["objectVersion"]
    assert result.action_proposal["evidenceRefs"][0]["contextId"] == "ctx-order-1"
    assert _table_count(foundry.engine, db.action_runs) == before_action_runs


def test_action_proposal_rejects_forged_context_before_review_creation(
    foundry: Any,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    _seed_ai_run(foundry.engine)

    with pytest.raises(ActionProposalError) as excinfo:
        foundry.aip.propose_action(
            originating_ai_run_id="ai-run-1",
            action_type="ApproveOrder",
            target_object_type="Order",
            target_object_id="O-1001",
            expected_object_version=int(order["objectVersion"]),
            parameters={"reason": "Inventory confirmed"},
            evidence_context_ids=("ctx-forged",),
            agent_allowed_actions=("ApproveOrder",),
            policy_version="policy-v1",
            expires_at="2026-06-25T00:10:00Z",
            claim_text="Forged context should not become a review.",
            ctx=_CTX,
        )

    assert excinfo.value.reason == "evidence_not_in_manifest"
    assert _table_count(foundry.engine, db.insight_reviews) == 0


def test_proposal_fingerprint_change_requires_new_review(foundry: Any) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    _seed_ai_run(foundry.engine)

    first = _propose(foundry, order, reason="Inventory confirmed")
    replay = _propose(foundry, order, reason="Inventory confirmed")
    changed = _propose(foundry, order, reason="Manager override")

    assert replay.review_id == first.review_id
    assert replay.proposal_fingerprint == first.proposal_fingerprint
    assert changed.review_id != first.review_id
    assert changed.proposal_fingerprint != first.proposal_fingerprint
    assert _table_count(foundry.engine, db.insight_reviews) == 2


def test_action_proposal_rechecks_policy_allowlist_and_object_version(
    foundry: Any,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    _seed_ai_run(foundry.engine)
    viewer = RequestContext(tenant_id="tenant-demo", actor_user_id="viewer", roles=("viewer",), request_id="viewer")

    with pytest.raises(ActionProposalError) as policy_error:
        _propose(foundry, order, reason="Inventory confirmed", ctx=viewer)
    with pytest.raises(ActionProposalError) as allowlist_error:
        _propose(foundry, order, reason="Inventory confirmed", agent_allowed_actions=())
    with pytest.raises(ActionProposalError) as version_error:
        _propose(foundry, order, reason="Inventory confirmed", expected_object_version=999)

    assert policy_error.value.reason == "policy_denied"
    assert allowlist_error.value.reason == "action_not_in_agent_manifest"
    assert version_error.value.reason == "object_version_conflict"
    assert _table_count(foundry.engine, db.insight_reviews) == 0


def _propose(
    foundry: Any,
    order: Mapping[str, object],
    *,
    reason: str,
    ctx: RequestContext = _CTX,
    agent_allowed_actions: tuple[str, ...] = ("ApproveOrder",),
    expected_object_version: int | None = None,
):
    return foundry.aip.propose_action(
        originating_ai_run_id="ai-run-1",
        action_type="ApproveOrder",
        target_object_type="Order",
        target_object_id="O-1001",
        expected_object_version=expected_object_version or _object_version(order),
        parameters={"reason": reason},
        evidence_context_ids=("ctx-order-1",),
        agent_allowed_actions=agent_allowed_actions,
        policy_version="policy-v1",
        expires_at="2026-06-25T00:10:00Z",
        claim_text="Approve O-1001 based on selected AI evidence.",
        ctx=ctx,
    )


def _seed_ai_run(engine: Any) -> None:
    repository = SqlAlchemyAiRunRepository(engine)
    with engine.begin() as transaction:
        repository.create_session(transaction=transaction, record=_session_record())
        repository.create_execution_run(transaction=transaction, record=_run_record())
        repository.record_context_item(transaction=transaction, record=_context_item_record())


def _object_version(order: Mapping[str, object]) -> int:
    value = order["objectVersion"]
    assert isinstance(value, int)
    return value


def _session_record() -> AiSessionRecord:
    return AiSessionRecord(
        id="ai-session-1",
        tenant_id="tenant-demo",
        agent_version_id="agent-v1",
        actor_user_id="ops-user",
        status="active",
        created_at="2026-06-25T00:00:00Z",
        last_activity_at="2026-06-25T00:00:01Z",
    )


def _run_record() -> AiExecutionRunRecord:
    return AiExecutionRunRecord(
        id="ai-run-1",
        tenant_id="tenant-demo",
        session_id="ai-session-1",
        agent_version_id="agent-v1",
        actor_user_id="ops-user",
        request_id="req-action-proposal",
        trace_id="trace-action-proposal",
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
        id="context-item-1",
        tenant_id="tenant-demo",
        ai_run_id="ai-run-1",
        context_id="ctx-order-1",
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


def _review_row(engine: Any, review_id: str) -> Mapping[str, object]:
    with engine.begin() as transaction:
        return (
            transaction.execute(select(db.insight_reviews).where(db.insight_reviews.c.id == review_id)).mappings().one()
        )


def _table_count(engine: Any, table: Any) -> int:
    with engine.begin() as transaction:
        return int(transaction.execute(select(func.count()).select_from(table)).scalar_one())
