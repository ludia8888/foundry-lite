from __future__ import annotations

from base64 import b64decode
from dataclasses import dataclass
from typing import cast

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailure
from foundry_lite.application.ports.governed_release_delivery_config import GovernedReleaseDeliveryConfig
from foundry_lite.application.ports.release_delivery_repository import ReleaseDeliveryRecord
from foundry_lite.application.ports.source_control_candidate import (
    SourceCandidateCommitBinding,
    SourceCandidatePublicationReceipt,
    SourceCandidatePublicationRequest,
    SourceCandidatePublicationStatus,
)
from foundry_lite.application.ports.source_control_release import (
    PullRequestSnapshot,
    PullRequestTarget,
    SourceControlReleasePort,
    SourceControlReviewDecision,
    SourceRefSnapshot,
    SourceRepositoryRef,
)
from foundry_lite.application.services.aip.external_release_delivery_ledger import ExternalReleaseDeliveryLedger
from foundry_lite.application.services.aip.external_release_delivery_support import (
    ExternalReleaseDeliveryOutcomeUnknown,
)
from foundry_lite.application.services.aip.external_release_source_publication import (
    ExternalReleaseSourcePublicationWorkflow,
)
from foundry_lite.application.services.pipeline_graph_model import pipeline_graph_fingerprint
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.release_delivery_repository import (
    SqlAlchemyReleaseDeliveryRepository,
)
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


class _RuntimeEvidence:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.audit_events: list[dict[str, object]] = []
        self.outbox_events: list[dict[str, object]] = []

    def _audit(self, _transaction: object, _ctx: RequestContext, **payload: object) -> None:
        self.events.append(str(payload["event_type"]))
        self.audit_events.append(dict(payload))

    def _outbox(
        self,
        _transaction: object,
        _ctx: RequestContext,
        event_type: str,
        *args: object,
        **kwargs: object,
    ) -> None:
        self.events.append(f"outbox:{event_type}")
        self.outbox_events.append(
            {
                "eventType": event_type,
                "aggregateType": args[0],
                "aggregateId": args[1],
                "payload": args[2],
                **kwargs,
            }
        )

    def _error_payload(
        self,
        exc: Exception,
        _ctx: RequestContext,
        *,
        run_id: str,
    ) -> dict[str, object]:
        return {"type": type(exc).__name__, "message": str(exc), "runId": run_id}


class _SourceControlAdapter:
    def __init__(self, engine: Engine, repository: SqlAlchemyReleaseDeliveryRepository) -> None:
        self.engine = engine
        self.repository = repository
        self.publish_statuses = [SourceCandidatePublicationStatus.PUBLISHED]
        self.lookup_statuses = [SourceCandidatePublicationStatus.PUBLISHED]
        self.publish_requests: list[SourceCandidatePublicationRequest] = []
        self.lookup_requests: list[SourceCandidatePublicationRequest] = []
        self.inspected_targets: list[PullRequestTarget] = []
        self.inspected_refs: list[str] = []
        self.intent_statuses_at_publish: list[str | None] = []
        self.is_conflict_error_enabled = False
        self.is_receipt_mismatch_enabled = False
        self.is_snapshot_target_swap_enabled = False
        self.is_base_ref_swap_enabled = False
        self.publish_exception: Exception | None = None
        self.lookup_exception: Exception | None = None

    def inspect_source_ref(self, repository: SourceRepositoryRef, ref: str) -> SourceRefSnapshot:
        self.inspected_refs.append(ref)
        observed_ref = "production" if self.is_base_ref_swap_enabled else ref
        return SourceRefSnapshot(repository, observed_ref, _sha("b"), _sha("f"))

    def publish_pull_request_candidate(
        self,
        request: SourceCandidatePublicationRequest,
    ) -> SourceCandidatePublicationReceipt:
        self.publish_requests.append(request)
        if self.publish_exception is not None:
            raise self.publish_exception
        row = _find_delivery(self.engine, self.repository, request.idempotency_key)
        self.intent_statuses_at_publish.append(row.status if row is not None else None)
        if self.is_conflict_error_enabled:
            raise AdapterError(
                AdapterFailure(
                    adapter_profile="github-release",
                    operation="publish_pull_request_candidate",
                    kind="conflict",
                    is_retryable=False,
                    operator_message="multiple candidate pull requests matched the exact branch",
                    idempotency_key=request.idempotency_key,
                    details={"reason": "multiple_candidate_pull_requests"},
                )
            )
        status = _next_status(self.publish_statuses, "publish")
        return _publication_receipt(
            request,
            status,
            is_mismatched=self.is_receipt_mismatch_enabled,
        )

    def lookup_pull_request_candidate(
        self,
        request: SourceCandidatePublicationRequest,
    ) -> SourceCandidatePublicationReceipt:
        self.lookup_requests.append(request)
        if self.lookup_exception is not None:
            raise self.lookup_exception
        status = _next_status(self.lookup_statuses, "lookup")
        return _publication_receipt(request, status)

    def inspect_pull_request(self, target: PullRequestTarget) -> PullRequestSnapshot:
        self.inspected_targets.append(target)
        observed_target = target
        if self.is_snapshot_target_swap_enabled:
            observed_target = PullRequestTarget(
                target.repository,
                target.pull_number + 1,
                target.expected_base_ref,
                target.expected_head_sha,
                candidate_binding=target.candidate_binding,
            )
        return _pull_request_snapshot(observed_target)


@dataclass(frozen=True)
class _Harness:
    workflow: ExternalReleaseSourcePublicationWorkflow
    context: RequestContext
    proposal: dict[str, object]
    source: _SourceControlAdapter
    runtime: _RuntimeEvidence
    repository: SqlAlchemyReleaseDeliveryRepository
    engine: Engine


def test_publication_writes_intent_before_provider_and_exact_replay_is_read_only() -> None:
    harness = _harness()
    arguments = {"idempotencyKey": "source-publish-1"}

    first = harness.workflow.publish(harness.context, "pipeline", harness.proposal, arguments)
    replay = harness.workflow.publish(harness.context, "pipeline", harness.proposal, arguments)

    assert first["status"] == "landed"
    assert replay == first
    assert harness.source.intent_statuses_at_publish == ["dispatching"]
    assert len(harness.source.publish_requests) == 1
    assert harness.source.lookup_requests == []
    assert harness.source.inspected_refs == ["main"]
    assert _delivery(harness, "source-publish-1").status == "landed"


def test_partial_publication_recovers_by_lookup_then_one_bounded_publish() -> None:
    harness = _harness()
    harness.source.publish_statuses = [
        SourceCandidatePublicationStatus.PARTIAL,
        SourceCandidatePublicationStatus.PUBLISHED,
    ]
    harness.source.lookup_statuses = [SourceCandidatePublicationStatus.PARTIAL]
    arguments = {"idempotencyKey": "source-partial-1"}

    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown, match="incomplete"):
        harness.workflow.publish(harness.context, "pipeline", harness.proposal, arguments)

    assert _delivery(harness, "source-partial-1").status == "ambiguous"
    recovered = harness.workflow.publish(harness.context, "pipeline", harness.proposal, arguments)

    assert recovered["status"] == "landed"
    assert len(harness.source.lookup_requests) == 1
    assert len(harness.source.publish_requests) == 2
    assert harness.source.intent_statuses_at_publish == ["dispatching", "ambiguous"]
    assert _delivery(harness, "source-partial-1").status == "landed"


def test_direct_absent_receipt_is_durable_with_source_publish_audit_outbox_pairs() -> None:
    harness = _harness()
    harness.source.publish_statuses = [SourceCandidatePublicationStatus.ABSENT]

    with pytest.raises(ConflictDetected, match="was not published"):
        harness.workflow.publish(
            harness.context,
            "pipeline",
            harness.proposal,
            {"idempotencyKey": "source-absent-1"},
        )

    row = _delivery(harness, "source-absent-1")
    assert row.status == "absent"
    assert row.result_ref is not None
    assert row.result_ref["status"] == "absent"
    assert len(harness.source.publish_requests) == 1
    assert harness.source.lookup_requests == []
    _assert_source_publish_transition_pairs(harness.runtime, ("prepared", "dispatching", "absent"))


def test_direct_ambiguous_receipt_is_durable_with_source_publish_audit_outbox_pairs() -> None:
    harness = _harness()
    harness.source.publish_statuses = [SourceCandidatePublicationStatus.AMBIGUOUS]

    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown, match="is incomplete"):
        harness.workflow.publish(
            harness.context,
            "pipeline",
            harness.proposal,
            {"idempotencyKey": "source-ambiguous-1"},
        )

    row = _delivery(harness, "source-ambiguous-1")
    assert row.status == "ambiguous"
    assert row.result_ref is not None
    assert row.result_ref["status"] == "ambiguous"
    assert row.error_ref == {"kind": "outcome_unknown"}
    assert len(harness.source.publish_requests) == 1
    assert harness.source.lookup_requests == []
    _assert_source_publish_transition_pairs(harness.runtime, ("prepared", "dispatching", "ambiguous"))


def test_ambiguous_publication_settles_absent_after_lookup_and_bounded_retry_both_confirm_absence() -> None:
    harness = _harness()
    harness.source.publish_statuses = [
        SourceCandidatePublicationStatus.AMBIGUOUS,
        SourceCandidatePublicationStatus.ABSENT,
    ]
    harness.source.lookup_statuses = [SourceCandidatePublicationStatus.ABSENT]
    arguments = {"idempotencyKey": "source-confirmed-absent-1"}

    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown, match="incomplete"):
        harness.workflow.publish(harness.context, "pipeline", harness.proposal, arguments)

    with pytest.raises(ConflictDetected, match="was not published"):
        harness.workflow.publish(harness.context, "pipeline", harness.proposal, arguments)

    row = _delivery(harness, "source-confirmed-absent-1")
    assert row.status == "absent"
    assert row.result_ref is not None and row.result_ref["status"] == "absent"
    assert len(harness.source.lookup_requests) == 1
    assert len(harness.source.publish_requests) == 2
    _assert_source_publish_transition_pairs(harness.runtime, ("prepared", "dispatching", "ambiguous", "absent"))


def test_conflict_adapter_error_remains_ambiguous_without_known_not_committed_claim() -> None:
    harness = _harness()
    harness.source.is_conflict_error_enabled = True

    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown, match="outcome is unknown"):
        harness.workflow.publish(
            harness.context,
            "pipeline",
            harness.proposal,
            {"idempotencyKey": "source-conflict-1"},
        )

    row = _delivery(harness, "source-conflict-1")
    assert row.status == "ambiguous"
    assert row.result_ref is None
    assert row.error_ref is not None
    adapter_failure = row.error_ref["adapterFailure"]
    assert isinstance(adapter_failure, dict)
    assert adapter_failure["kind"] == "conflict"
    assert "knownNotCommitted" not in row.error_ref
    assert "safeToRetry" not in row.error_ref


def test_receipt_that_does_not_match_ledger_intent_is_rejected() -> None:
    harness = _harness()
    harness.source.is_receipt_mismatch_enabled = True

    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown, match="does not match its intent"):
        harness.workflow.publish(
            harness.context,
            "pipeline",
            harness.proposal,
            {"idempotencyKey": "source-receipt-mismatch-1"},
        )

    row = _delivery(harness, "source-receipt-mismatch-1")
    assert row.status == "dispatching"
    assert row.result_ref is None
    assert row.provider_resource_id is None


def test_fresh_snapshot_rejects_provider_pr_target_swap() -> None:
    harness = _harness()
    harness.workflow.publish(
        harness.context,
        "pipeline",
        harness.proposal,
        {"idempotencyKey": "source-target-swap-1"},
    )
    harness.source.is_snapshot_target_swap_enabled = True

    with pytest.raises(ConflictDetected, match="no longer matches the published governed candidate"):
        harness.workflow.fresh_snapshot(harness.context, harness.proposal)

    assert len(harness.source.publish_requests) == 1
    assert len(harness.source.lookup_requests) == 0
    assert harness.source.inspected_targets[0].pull_number == 17


def test_published_manifest_bytes_and_workflow_root_survive_proposal_and_branch_drift() -> None:
    harness = _harness()
    harness.workflow.publish(
        harness.context,
        "pipeline",
        harness.proposal,
        {"idempotencyKey": "source-durable-manifest-1"},
    )
    row = _delivery(harness, "source-durable-manifest-1")
    candidate = row.candidate_ref
    assert candidate is not None

    harness.source.lookup_statuses = [SourceCandidatePublicationStatus.ABSENT]
    snapshot = harness.workflow.fresh_snapshot(harness.context, _changed_proposal(harness.proposal))

    request = harness.source.publish_requests[0]
    assert b64decode(str(candidate["manifestCanonicalBytesBase64"]), validate=True) == request.manifest.canonical_bytes
    assert row.application_id == "application-a"
    assert row.release_kind == "pipeline"
    assert row.workflow_run_id == "release-run-a"
    assert row.parent_delivery_id is None
    assert snapshot.target.candidate_binding is not None
    assert snapshot.target.candidate_binding.manifest == request.manifest
    assert harness.source.lookup_requests == []


def test_same_key_replay_rejects_changed_proposal_fingerprint() -> None:
    harness = _harness()
    arguments = {"idempotencyKey": "source-proposal-drift-1"}
    harness.workflow.publish(harness.context, "pipeline", harness.proposal, arguments)
    changed_proposal = _changed_proposal(harness.proposal)

    with pytest.raises(ConflictDetected, match="server binding"):
        harness.workflow.publish(harness.context, "pipeline", changed_proposal, arguments)

    assert len(harness.source.publish_requests) == 1
    assert harness.source.lookup_requests == []
    assert _delivery(harness, "source-proposal-drift-1").status == "landed"


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"idempotencyKey": "optional-1", "consumerOsdkApplicationId": 7}, "consumerOsdkApplicationId"),
        ({"idempotencyKey": "optional-2", "consumerOsdkApplicationId": "   "}, "consumerOsdkApplicationId"),
        ({"idempotencyKey": "optional-3", "consumerOsdkCompliance": []}, "consumerOsdkCompliance"),
    ],
)
def test_optional_consumer_osdk_coordinates_are_strict_before_any_provider_call(
    arguments: dict[str, object],
    message: str,
) -> None:
    harness = _harness()

    with pytest.raises(ConflictDetected, match=message):
        harness.workflow.publish(harness.context, "pipeline", harness.proposal, arguments)

    assert harness.source.inspected_refs == []
    assert harness.source.publish_requests == []


def test_source_repository_and_base_ref_snapshot_are_server_bound_before_intent_creation() -> None:
    harness = _harness()
    harness.source.is_base_ref_swap_enabled = True

    with pytest.raises(ConflictDetected, match="base ref outside the server binding"):
        harness.workflow.publish(
            harness.context,
            "pipeline",
            harness.proposal,
            {"idempotencyKey": "base-ref-swap-1"},
        )

    assert harness.source.publish_requests == []
    assert _find_delivery(harness.engine, harness.repository, "base-ref-swap-1") is None

    config = GovernedReleaseDeliveryConfig(source_repository=None, is_source_control_required=False)
    missing = ExternalReleaseSourcePublicationWorkflow(
        cast(SourceControlReleasePort, harness.source),
        config,
        harness.workflow._ledger,
        cast(RuntimeEvidenceBoundary, harness.runtime),
    )
    with pytest.raises(ConflictDetected, match="repository is not configured"):
        missing.publish(harness.context, "pipeline", harness.proposal, {"idempotencyKey": "missing-repo-1"})


def test_unknown_provider_exception_is_durable_ambiguous_and_redacted_by_runtime_boundary() -> None:
    harness = _harness()
    harness.source.publish_exception = RuntimeError("provider socket failed")

    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown, match="outcome is unknown"):
        harness.workflow.publish(
            harness.context,
            "pipeline",
            harness.proposal,
            {"idempotencyKey": "source-unknown-1"},
        )

    row = _delivery(harness, "source-unknown-1")
    assert row.status == "ambiguous"
    assert row.error_ref == {
        "type": "RuntimeError",
        "message": "provider socket failed",
        "runId": row.ai_run_id,
        "kind": "outcome_unknown",
    }


def test_reconciliation_lookup_or_recovery_write_failure_never_blindly_replays() -> None:
    harness = _harness()
    harness.source.publish_statuses = [SourceCandidatePublicationStatus.AMBIGUOUS]
    arguments = {"idempotencyKey": "source-lookup-error-1"}
    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown):
        harness.workflow.publish(harness.context, "pipeline", harness.proposal, arguments)

    harness.source.lookup_exception = RuntimeError("lookup unavailable")
    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown, match="lookup is not conclusive"):
        harness.workflow.publish(harness.context, "pipeline", harness.proposal, arguments)
    assert len(harness.source.publish_requests) == 1

    harness = _harness()
    harness.source.publish_statuses = [SourceCandidatePublicationStatus.AMBIGUOUS]
    harness.source.lookup_statuses = [SourceCandidatePublicationStatus.ABSENT]
    arguments = {"idempotencyKey": "source-recovery-error-1"}
    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown):
        harness.workflow.publish(harness.context, "pipeline", harness.proposal, arguments)
    harness.source.publish_exception = RuntimeError("recovery unavailable")
    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown, match="recovery is not conclusive"):
        harness.workflow.publish(harness.context, "pipeline", harness.proposal, arguments)
    assert _delivery(harness, "source-recovery-error-1").status == "ambiguous"


def test_ambiguous_lookup_and_recovery_remain_ambiguous_without_a_conclusive_receipt() -> None:
    harness = _harness()
    harness.source.publish_statuses = [
        SourceCandidatePublicationStatus.AMBIGUOUS,
        SourceCandidatePublicationStatus.AMBIGUOUS,
    ]
    harness.source.lookup_statuses = [SourceCandidatePublicationStatus.AMBIGUOUS]
    arguments = {"idempotencyKey": "source-still-ambiguous-1"}
    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown):
        harness.workflow.publish(harness.context, "pipeline", harness.proposal, arguments)
    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown, match="remains ambiguous"):
        harness.workflow.publish(harness.context, "pipeline", harness.proposal, arguments)
    assert len(harness.source.publish_requests) == 1

    harness.source.lookup_statuses = [SourceCandidatePublicationStatus.ABSENT]
    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown, match="remains incomplete"):
        harness.workflow.publish(harness.context, "pipeline", harness.proposal, arguments)
    assert len(harness.source.publish_requests) == 2


def test_dispatch_claim_completion_and_lookup_settlement_fence_loss_are_never_reported_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _harness()
    monkeypatch.setattr(harness.workflow._ledger, "claim", lambda *_: None)
    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown, match="dispatch fence was lost"):
        harness.workflow.publish(
            harness.context,
            "pipeline",
            harness.proposal,
            {"idempotencyKey": "source-claim-lost-1"},
        )
    assert harness.source.publish_requests == []

    harness = _harness()
    monkeypatch.setattr(harness.workflow._ledger, "complete", lambda *_: None)
    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown, match="completion lost its dispatch fence"):
        harness.workflow.publish(
            harness.context,
            "pipeline",
            harness.proposal,
            {"idempotencyKey": "source-complete-lost-1"},
        )

    harness = _harness()
    harness.source.publish_statuses = [SourceCandidatePublicationStatus.AMBIGUOUS]
    harness.source.lookup_statuses = [SourceCandidatePublicationStatus.PUBLISHED]
    arguments = {"idempotencyKey": "source-settle-lost-1"}
    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown):
        harness.workflow.publish(harness.context, "pipeline", harness.proposal, arguments)
    monkeypatch.setattr(harness.workflow._ledger, "settle_lookup", lambda *_: None)
    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown, match="reconciliation lost its fence"):
        harness.workflow.publish(harness.context, "pipeline", harness.proposal, arguments)


def test_fresh_snapshot_requires_exact_landed_publication_and_matching_proposal_parent() -> None:
    harness = _harness()
    with pytest.raises(ConflictDetected, match="durably published"):
        harness.workflow.fresh_snapshot_with_publication(harness.context, harness.proposal, "missing-publication")

    harness.source.publish_statuses = [SourceCandidatePublicationStatus.AMBIGUOUS]
    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown):
        harness.workflow.publish(
            harness.context,
            "pipeline",
            harness.proposal,
            {"idempotencyKey": "source-not-landed-1"},
        )
    ambiguous = _delivery(harness, "source-not-landed-1")
    with pytest.raises(ConflictDetected, match="durably published"):
        harness.workflow.fresh_snapshot_with_publication(harness.context, harness.proposal, ambiguous.delivery_id)

    harness = _harness()
    harness.workflow.publish(
        harness.context,
        "pipeline",
        harness.proposal,
        {"idempotencyKey": "source-parent-1"},
    )
    landed = _delivery(harness, "source-parent-1")
    other = dict(harness.proposal)
    other["id"] = "proposal-other"
    with pytest.raises(ConflictDetected, match="parent does not match"):
        harness.workflow.fresh_snapshot_with_publication(harness.context, other, landed.delivery_id)


def test_known_failed_or_absent_intent_cannot_be_dispatched_again() -> None:
    harness = _harness()
    harness.source.publish_statuses = [SourceCandidatePublicationStatus.ABSENT]
    arguments = {"idempotencyKey": "source-known-absent-1"}
    with pytest.raises(ConflictDetected, match="was not published"):
        harness.workflow.publish(harness.context, "pipeline", harness.proposal, arguments)
    with pytest.raises(ConflictDetected, match="known non-success outcome"):
        harness.workflow.publish(harness.context, "pipeline", harness.proposal, arguments)
    assert len(harness.source.publish_requests) == 1


def _harness() -> _Harness:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    repository = SqlAlchemyReleaseDeliveryRepository(engine)
    runtime = _RuntimeEvidence()
    ledger = ExternalReleaseDeliveryLedger(
        engine,
        repository,
        cast(RuntimeEvidenceBoundary, runtime),
    )
    source = _SourceControlAdapter(engine, repository)
    config = GovernedReleaseDeliveryConfig(
        source_repository=_repository(),
        source_base_ref="main",
        source_head_prefix="codex/",
        is_source_control_required=True,
    )
    workflow = ExternalReleaseSourcePublicationWorkflow(
        cast(SourceControlReleasePort, source),
        config,
        ledger,
        cast(RuntimeEvidenceBoundary, runtime),
    )
    context = RequestContext(
        tenant_id="tenant-a",
        actor_user_id="reviewer-a",
        request_id="request-a",
        roles=("admin",),
        application_id="application-a",
        governed_release_run_id="release-run-a",
        governed_release_binding_hash=_fingerprint("f"),
    )
    return _Harness(workflow, context, _proposal(), source, runtime, repository, engine)


def _assert_source_publish_transition_pairs(
    runtime: _RuntimeEvidence,
    statuses: tuple[str, ...],
) -> None:
    expected_event_types = [f"governed_release.delivery.{status}" for status in statuses]
    assert [event["event_type"] for event in runtime.audit_events] == expected_event_types
    assert [event["eventType"] for event in runtime.outbox_events] == expected_event_types
    assert len(runtime.audit_events) == len(runtime.outbox_events)

    for status, audit, outbox in zip(statuses, runtime.audit_events, runtime.outbox_events, strict=True):
        audit_after = audit["after_ref"]
        outbox_payload = outbox["payload"]
        assert isinstance(audit_after, dict)
        assert isinstance(outbox_payload, dict)
        assert audit_after["operation"] == "source_publish"
        assert outbox_payload["operation"] == "source_publish"
        assert audit_after["status"] == status
        assert outbox_payload["status"] == status
        assert audit_after == outbox_payload
        assert audit["resource_type"] == outbox["aggregateType"]
        assert audit["resource_id"] == outbox["aggregateId"]
        assert audit["correlation_id"] == outbox["correlation_id"]


def _proposal() -> dict[str, object]:
    graph = {"schemaVersion": 2, "nodes": [], "edges": []}
    return {
        "id": "proposal-1",
        "graph": graph,
        "graphFingerprint": pipeline_graph_fingerprint(graph),
        "sourceBranch": {"branchName": "release-1", "branchId": "branch-1"},
        "changeDiff": {"items": [], "summary": {"totalChangeCount": 0}},
        "diffCompleteness": "complete",
        "testReceipt": {"status": "passed", "proofKind": "pipeline_branch_test"},
    }


def _changed_proposal(proposal: dict[str, object]) -> dict[str, object]:
    graph = {"schemaVersion": 2, "nodes": [{"id": "new-node"}], "edges": []}
    changed = dict(proposal)
    changed["graph"] = graph
    changed["graphFingerprint"] = pipeline_graph_fingerprint(graph)
    return changed


def _publication_receipt(
    request: SourceCandidatePublicationRequest,
    status: SourceCandidatePublicationStatus,
    *,
    is_mismatched: bool = False,
) -> SourceCandidatePublicationReceipt:
    has_commit = status in {
        SourceCandidatePublicationStatus.PARTIAL,
        SourceCandidatePublicationStatus.PUBLISHED,
    }
    binding = (
        SourceCandidateCommitBinding(
            request.expected_base_sha,
            _sha("9"),
            request.expected_head_ref,
            request.manifest,
        )
        if has_commit
        else None
    )
    return SourceCandidatePublicationReceipt(
        status=status,
        repository=request.repository,
        expected_base_ref=request.expected_base_ref,
        expected_head_ref=request.expected_head_ref,
        expected_base_sha=request.expected_base_sha,
        manifest_artifact_path=request.manifest.artifact_path,
        manifest_fingerprint=request.manifest.manifest_fingerprint,
        idempotency_key=f"{request.idempotency_key}-other" if is_mismatched else request.idempotency_key,
        head_sha=_sha("a") if has_commit else None,
        pull_number=17 if status is SourceCandidatePublicationStatus.PUBLISHED else None,
        commit_binding=binding,
        provider_request_id="github-publication-request-1",
        evidence={"reason": status.value},
    )


def _pull_request_snapshot(target: PullRequestTarget) -> PullRequestSnapshot:
    return PullRequestSnapshot(
        target=target,
        state="open",
        is_draft=False,
        is_merged=False,
        base_sha=_sha("b"),
        head_ref="codex/release-1",
        author_id=11,
        author_login="author",
        mergeable_state="clean",
        test_merge_commit_sha=None,
        checks_commit_sha=target.expected_head_sha,
        review_decision=SourceControlReviewDecision.REVIEW_REQUIRED,
        required_approval_count=1,
        approvals=(),
        active_rules=(),
        required_checks=(),
        is_merge_queue_required=False,
        rules_fingerprint=_fingerprint("c"),
        checks_fingerprint=_fingerprint("d"),
        blocking_reasons=("review_required",),
        is_ready_to_merge=False,
        provider_request_id="github-inspect-request-1",
    )


def _find_delivery(
    engine: Engine,
    repository: SqlAlchemyReleaseDeliveryRepository,
    idempotency_key: str,
) -> ReleaseDeliveryRecord | None:
    with engine.begin() as transaction:
        return repository.find_by_idempotency(
            transaction=transaction,
            tenant_id="tenant-a",
            provider="github",
            operation="source_publish",
            idempotency_key=idempotency_key,
        )


def _delivery(harness: _Harness, idempotency_key: str) -> ReleaseDeliveryRecord:
    row = _find_delivery(harness.engine, harness.repository, idempotency_key)
    assert row is not None
    return row


def _next_status(
    statuses: list[SourceCandidatePublicationStatus],
    operation: str,
) -> SourceCandidatePublicationStatus:
    if not statuses:
        raise AssertionError(f"unexpected extra source candidate {operation}")
    return statuses.pop(0)


def _repository() -> SourceRepositoryRef:
    return SourceRepositoryRef("github", 42, "acme", "platform")


def _sha(character: str) -> str:
    return character * 40


def _fingerprint(character: str) -> str:
    return f"sha256:{character * 64}"
