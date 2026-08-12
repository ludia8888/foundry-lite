from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, replace
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


def test_insight_review_create_key_lookup_is_tenant_scoped() -> None:
    harness = _sqlalchemy_harness()
    with harness.transaction() as transaction:
        harness.repository.insert_review_or_get_existing(
            transaction=transaction,
            record=_record("review_1", create_key="mcp-call-key"),
        )
        found = harness.repository.review_by_create_idempotency_key(
            transaction=transaction,
            tenant_id="tenant-demo",
            idempotency_key="mcp-call-key",
        )
        other_tenant = harness.repository.review_by_create_idempotency_key(
            transaction=transaction,
            tenant_id="tenant-other",
            idempotency_key="mcp-call-key",
        )

    assert found is not None
    assert found["id"] == "review_1"
    assert other_tenant is None


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


def test_insight_review_unassigned_claim_cannot_be_stolen_by_a_second_reviewer() -> None:
    harness = _sqlalchemy_harness()
    with harness.transaction() as transaction:
        harness.repository.insert_review_or_get_existing(
            transaction=transaction,
            record=_record("review_1", create_key="create-1"),
        )
        first = harness.repository.assign_review(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_1",
            assignee_user_id="reviewer-1",
            assignment_idempotency_key="claim-1",
            updated_at="2026-06-19T00:00:01Z",
            is_unassigned_only=True,
        )
        stolen = harness.repository.assign_review(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_1",
            assignee_user_id="reviewer-2",
            assignment_idempotency_key="claim-2",
            updated_at="2026-06-19T00:00:02Z",
            is_unassigned_only=True,
        )
        current = harness.repository.review_by_id(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_1",
        )

    assert first is not None
    assert stolen is None
    assert current is not None
    assert current["assignee_user_id"] == "reviewer-1"
    assert current["assignment_idempotency_key"] == "claim-1"


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
        success_before_start = harness.repository.mark_execution_succeeded(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_1",
            action_run_id="action_run_early",
            updated_at="2026-06-19T00:00:02Z",
        )
        started = harness.repository.mark_execution_started(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_1",
            execution_idempotency_key="execute-1",
            execution_request_fingerprint="sha256:execution-request",
            proposal_fingerprint="sha256:proposal",
            updated_at="2026-06-19T00:00:03Z",
        )
        duplicate_start = harness.repository.mark_execution_started(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_1",
            execution_idempotency_key="execute-1",
            execution_request_fingerprint="sha256:execution-request",
            proposal_fingerprint="sha256:proposal",
            updated_at="2026-06-19T00:00:04Z",
        )
        succeeded = harness.repository.mark_execution_succeeded(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_1",
            action_run_id="action_run_1",
            updated_at="2026-06-19T00:00:05Z",
        )
        late_failure = harness.repository.mark_execution_failed(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_1",
            error={"message": "late"},
            updated_at="2026-06-19T00:00:06Z",
        )

    assert success_before_start is None
    assert started is not None
    assert started["execution_status"] == "executing"
    assert started["review_metadata"]["approvalExecution"] == {
        "idempotencyKey": "execute-1",
        "requestFingerprint": "sha256:execution-request",
        "proposalFingerprint": "sha256:proposal",
    }
    assert duplicate_start is None
    assert succeeded is not None
    assert succeeded["execution_status"] == "executed"
    assert succeeded["approved_action_run_id"] == "action_run_1"
    assert late_failure is None


def test_insight_review_lookup_by_execution_idempotency_key_is_indexed_and_tenant_scoped() -> None:
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
            execution_idempotency_key="execute-lookup",
            execution_request_fingerprint="sha256:execution-request",
            proposal_fingerprint="sha256:proposal",
            updated_at="2026-06-19T00:00:02Z",
        )
        found = harness.repository.review_by_execution_idempotency_key(
            transaction=transaction,
            tenant_id="tenant-demo",
            idempotency_key="execute-lookup",
        )
        missing_key = harness.repository.review_by_execution_idempotency_key(
            transaction=transaction,
            tenant_id="tenant-demo",
            idempotency_key="never-claimed",
        )
        other_tenant = harness.repository.review_by_execution_idempotency_key(
            transaction=transaction,
            tenant_id="tenant-other",
            idempotency_key="execute-lookup",
        )

    # The key is persisted in a dedicated column (not scanned out of the JSON blob).
    assert started is not None
    assert started["execution_idempotency_key"] == "execute-lookup"
    # Lookup resolves the owning review by the indexed column.
    assert found is not None
    assert found["id"] == "review_1"
    # A key nobody claimed, and a matching key under another tenant, both miss.
    assert missing_key is None
    assert other_tenant is None


def test_insight_review_execution_success_can_merge_result_metadata() -> None:
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
        harness.repository.mark_execution_started(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_1",
            execution_idempotency_key="execute-1",
            execution_request_fingerprint="sha256:execution-request",
            proposal_fingerprint="sha256:proposal",
            updated_at="2026-06-19T00:00:02Z",
        )
        succeeded = harness.repository.mark_execution_succeeded(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_1",
            action_run_id="ont_1",
            updated_at="2026-06-19T00:00:03Z",
            metadata={"appliedOntologyVersion": {"ontologyVersionId": "ont_1", "versionNumber": 7}},
        )

    assert succeeded is not None
    assert succeeded["approved_action_run_id"] == "ont_1"
    assert succeeded["review_metadata"]["requestId"] == "request-1"
    assert succeeded["review_metadata"]["appliedOntologyVersion"] == {
        "ontologyVersionId": "ont_1",
        "versionNumber": 7,
    }


def test_insight_review_withdraw_terminates_pending_and_approved_but_not_executed() -> None:
    harness = _sqlalchemy_harness()
    with harness.transaction() as transaction:
        for review_id, create_key in (("review_1", "create-1"), ("review_2", "create-2"), ("review_3", "create-3")):
            harness.repository.insert_review_or_get_existing(
                transaction=transaction,
                record=_record(review_id, create_key=create_key, execution_status="pending_review"),
            )
        for review_id, key in (("review_2", "decision-2"), ("review_3", "decision-3")):
            harness.repository.decide_review(
                transaction=transaction,
                tenant_id="tenant-demo",
                review_id=review_id,
                status="approved",
                decision={"decision": "approved"},
                decision_idempotency_key=key,
                updated_at="2026-06-19T00:00:01Z",
            )
        harness.repository.mark_execution_started(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_3",
            execution_idempotency_key="execute-3",
            execution_request_fingerprint="sha256:execution-request",
            proposal_fingerprint="sha256:proposal",
            updated_at="2026-06-19T00:00:02Z",
        )
        withdrawal = {"reason": "superseded", "withdrawnByUserId": "creator"}
        pending_withdrawn = harness.repository.withdraw_review(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_1",
            withdrawal=withdrawal,
            updated_at="2026-06-19T00:00:03Z",
        )
        approved_withdrawn = harness.repository.withdraw_review(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_2",
            withdrawal=withdrawal,
            updated_at="2026-06-19T00:00:04Z",
        )
        executing_withdrawn = harness.repository.withdraw_review(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_3",
            withdrawal=withdrawal,
            updated_at="2026-06-19T00:00:05Z",
        )

    assert pending_withdrawn is not None
    assert pending_withdrawn["status"] == "rejected"
    assert pending_withdrawn["execution_status"] == "withdrawn"
    assert pending_withdrawn["review_metadata"]["withdrawal"] == withdrawal
    assert approved_withdrawn is not None
    assert approved_withdrawn["execution_status"] == "withdrawn"
    assert executing_withdrawn is None


def test_insight_review_update_proposal_content_only_revises_live_undecided_matching_fingerprint() -> None:
    harness = _sqlalchemy_harness()
    with harness.transaction() as transaction:
        for review_id, create_key in (("review_1", "create-1"), ("review_2", "create-2")):
            harness.repository.insert_review_or_get_existing(
                transaction=transaction,
                record=_record(review_id, create_key=create_key, execution_status="pending_review"),
            )
        harness.repository.decide_review(
            transaction=transaction,
            tenant_id="tenant-demo",
            review_id="review_2",
            status="approved",
            decision={"decision": "approved"},
            decision_idempotency_key="decision-2",
            updated_at="2026-06-19T00:00:01Z",
        )
        stale_fingerprint = _update_proposal_content(harness, transaction, "review_1", expected="sha256:other")
        updated = _update_proposal_content(harness, transaction, "review_1", expected="sha256:proposal")
        decided = _update_proposal_content(harness, transaction, "review_2", expected="sha256:proposal")

    assert stale_fingerprint is None
    assert decided is None
    assert updated is not None
    assert updated["proposal_fingerprint"] == "sha256:revised"
    assert updated["claim_text"] == "Revised title"
    assert updated["action_proposal"] == {"yamlText": "objectTypes: []"}
    assert updated["review_metadata"]["requestId"] == "request-1"
    assert updated["review_metadata"]["revision"] == {"count": 1, "lastRevisedAt": "2026-06-19T00:00:02Z"}


def _update_proposal_content(harness: InsightReviewHarness, transaction: Any, review_id: str, *, expected: str) -> Any:
    return harness.repository.update_proposal_content(
        transaction=transaction,
        tenant_id="tenant-demo",
        review_id=review_id,
        expected_fingerprint=expected,
        proposal_fingerprint="sha256:revised",
        claim_text="Revised title",
        action_proposal={"yamlText": "objectTypes: []"},
        revision={"count": 1, "lastRevisedAt": "2026-06-19T00:00:02Z"},
        updated_at="2026-06-19T00:00:02Z",
    )


def test_insight_review_proposal_list_filters_and_keyset_pages_by_type() -> None:
    harness = _sqlalchemy_harness()
    with harness.transaction() as transaction:
        harness.repository.insert_review_or_get_existing(
            transaction=transaction,
            record=_record("review_plain", create_key="create-plain"),
        )
        for index, review_id in enumerate(("review_1", "review_2", "review_3")):
            harness.repository.insert_review_or_get_existing(
                transaction=transaction,
                record=_proposal_record(review_id, create_key=f"create-{index}", created_at_second=index),
            )
        page_one = harness.repository.list_proposal_reviews(
            transaction=transaction,
            tenant_id="tenant-demo",
            proposal_type="ontology_action",
            status="pending",
            execution_statuses=None,
            is_assigned=False,
            created_before=None,
            before_id=None,
            limit=2,
        )
        page_two = harness.repository.list_proposal_reviews(
            transaction=transaction,
            tenant_id="tenant-demo",
            proposal_type="ontology_action",
            status=None,
            execution_statuses=("pending_review",),
            is_assigned=None,
            created_before=page_one[-1]["created_at"],
            before_id=page_one[-1]["id"],
            limit=2,
        )

    assert [row["id"] for row in page_one] == ["review_3", "review_2"]
    assert [row["id"] for row in page_two] == ["review_1"]


def _proposal_record(review_id: str, *, create_key: str, created_at_second: int) -> InsightReviewRecord:
    base = _record(review_id, create_key=create_key, execution_status="pending_review")
    timestamp = f"2026-06-19T00:00:0{created_at_second}Z"
    return replace(base, created_at=timestamp, updated_at=timestamp)


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
