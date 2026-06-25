from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Any

from foundry_lite.application.ports.insight_review_repository import (
    InsightReviewRecord,
    InsightReviewRepository,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyInsightReviewRepository
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


class InsightReviewHarness:
    repository: InsightReviewRepository

    def transaction(self) -> AbstractContextManager[Any]:
        raise NotImplementedError


@dataclass
class SqlAlchemyInsightReviewHarness(InsightReviewHarness):
    repository: InsightReviewRepository
    engine: Engine

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.engine.begin() as conn:
            yield conn


def test_insight_review_create_is_idempotent_by_tenant_and_key() -> None:
    harness = _sqlalchemy_harness()
    with harness.transaction() as transaction:
        first = _record("review_1", create_key="create-key")
        duplicate = _record("review_2", create_key="create-key")
        assert harness.repository.insert_review_or_get_existing(transaction=transaction, record=first) is None
        existing = harness.repository.insert_review_or_get_existing(transaction=transaction, record=duplicate)

    assert existing is not None
    assert existing["id"] == "review_1"
    assert existing["claim_id"] == "claim-1"


def test_insight_review_list_filters_status_and_assignee() -> None:
    harness = _sqlalchemy_harness()
    with harness.transaction() as transaction:
        harness.repository.insert_review_or_get_existing(
            transaction=transaction,
            record=_record("review_1", create_key="create-1", assignee_user_id="ops-a"),
        )
        harness.repository.insert_review_or_get_existing(
            transaction=transaction,
            record=_record("review_2", create_key="create-2", assignee_user_id="ops-b"),
        )
        rows = harness.repository.list_reviews(
            transaction=transaction,
            tenant_id="tenant-demo",
            status="pending",
            assignee_user_id="ops-b",
            limit=10,
        )

    assert [row["id"] for row in rows] == ["review_2"]


def test_insight_review_decision_has_one_terminal_winner() -> None:
    harness = _sqlalchemy_harness()
    with harness.transaction() as transaction:
        harness.repository.insert_review_or_get_existing(
            transaction=transaction,
            record=_record("review_1", create_key="create-1"),
        )
        approved = harness.repository.decide_review(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_1",
            status="approved",
            decision={"decision": "approved"},
            decision_idempotency_key="decision-1",
            updated_at="2026-06-19T00:00:01Z",
        )
        rejected = harness.repository.decide_review(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_1",
            status="rejected",
            decision={"decision": "rejected"},
            decision_idempotency_key="decision-2",
            updated_at="2026-06-19T00:00:02Z",
        )

    assert approved is not None
    assert approved["status"] == "approved"
    assert rejected is None


def test_insight_review_assignment_does_not_mutate_terminal_review() -> None:
    harness = _sqlalchemy_harness()
    with harness.transaction() as transaction:
        harness.repository.insert_review_or_get_existing(
            transaction=transaction,
            record=_record("review_1", create_key="create-1"),
        )
        approved = harness.repository.decide_review(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_1",
            status="approved",
            decision={"decision": "approved"},
            decision_idempotency_key="decision-1",
            updated_at="2026-06-19T00:00:01Z",
        )
        assigned = harness.repository.assign_review(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_1",
            assignee_user_id="late-ops",
            assignment_idempotency_key="assign-late",
            updated_at="2026-06-19T00:00:02Z",
        )
        current = harness.repository.review_by_id(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_1",
        )

    assert approved is not None
    assert assigned is None
    assert current is not None
    assert current["status"] == "approved"
    assert current["assignee_user_id"] is None
    assert current["assignment_idempotency_key"] is None


def test_insight_review_execution_status_links_action_once() -> None:
    harness = _sqlalchemy_harness()
    with harness.transaction() as transaction:
        harness.repository.insert_review_or_get_existing(
            transaction=transaction,
            record=_record("review_1", create_key="create-1", execution_status="pending_review"),
        )
        harness.repository.decide_review(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_1",
            status="approved",
            decision={"decision": "approved"},
            decision_idempotency_key="decision-1",
            updated_at="2026-06-19T00:00:01Z",
        )
        started = harness.repository.mark_execution_started(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_1",
            updated_at="2026-06-19T00:00:02Z",
        )
        duplicate_start = harness.repository.mark_execution_started(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_1",
            updated_at="2026-06-19T00:00:03Z",
        )
        succeeded = harness.repository.mark_execution_succeeded(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_1",
            action_run_id="action_run_1",
            updated_at="2026-06-19T00:00:04Z",
        )
        late_failure = harness.repository.mark_execution_failed(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_1",
            error={"message": "late"},
            updated_at="2026-06-19T00:00:05Z",
        )

    assert started is not None
    assert started["execution_status"] == "executing"
    assert duplicate_start is None
    assert succeeded is not None
    assert succeeded["execution_status"] == "executed"
    assert succeeded["approved_action_run_id"] == "action_run_1"
    assert late_failure is None


def _sqlalchemy_harness() -> SqlAlchemyInsightReviewHarness:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    return SqlAlchemyInsightReviewHarness(repository=SqlAlchemyInsightReviewRepository(engine), engine=engine)


def _record(
    review_id: str,
    *,
    create_key: str,
    assignee_user_id: str | None = None,
    execution_status: str | None = None,
) -> InsightReviewRecord:
    return InsightReviewRecord(
        review_id=review_id,
        tenant_id="tenant-demo",
        claim_id="claim-1",
        claim_text="Supplier risk increased",
        status="pending",
        priority="normal",
        assignee_user_id=assignee_user_id,
        evidence_object_ids=["evidence-1"],
        evidence_refs=[{"evidenceId": "evidence-1"}],
        action_proposal={"actionApiName": "ApproveOrder"},
        proposal_type="ontology_action" if execution_status else None,
        proposal_fingerprint="sha256:proposal" if execution_status else None,
        originating_ai_run_id="ai-run-1" if execution_status else None,
        originating_tool_call_id=None,
        expires_at=None,
        execution_status=execution_status,
        approved_action_run_id=None,
        approval_policy_version="policy-v1" if execution_status else None,
        created_by_user_id="creator",
        created_idempotency_key=create_key,
        assignment_idempotency_key=None,
        decision=None,
        decision_idempotency_key=None,
        review_metadata={"requestId": "request-1"},
        created_at="2026-06-19T00:00:00Z",
        updated_at="2026-06-19T00:00:00Z",
    )
