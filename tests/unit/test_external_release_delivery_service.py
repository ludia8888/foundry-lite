from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.governed_release_delivery_config import GovernedReleaseDeliveryConfig
from foundry_lite.application.ports.infrastructure_deployment_adapter import (
    InfrastructureDeploymentCandidateQuery,
    InfrastructureDeploymentGetRequest,
    InfrastructureDeploymentMutationResult,
    InfrastructureDeploymentObservation,
    InfrastructureDeploymentRollbackRequest,
    InfrastructureDeploymentServicePolicyObservation,
    InfrastructureDeploymentServicePolicyRequest,
    InfrastructureDeploymentStartRequest,
)
from foundry_lite.application.ports.release_delivery_repository import (
    ReleaseDeliveryOperation,
    ReleaseDeliveryRecord,
)
from foundry_lite.application.ports.source_control_candidate import (
    SourceCandidateCommitBinding,
    SourceCandidatePublicationReceipt,
    SourceCandidatePublicationRequest,
    SourceCandidatePublicationStatus,
)
from foundry_lite.application.ports.source_control_release import (
    BranchRuleEvidence,
    PullRequestReviewEvidence,
    PullRequestSearch,
    PullRequestSnapshot,
    PullRequestTarget,
    RequiredCheckEvidence,
    SourceControlMergeMethod,
    SourceControlMergeReceipt,
    SourceControlMergeRequest,
    SourceControlMergeStatus,
    SourceControlReviewDecision,
    SourceRefSnapshot,
    SourceRepositoryRef,
)
from foundry_lite.application.services.aip.external_release_delivery_service import (
    ExternalReleaseDeliveryOutcomeUnknown,
    ExternalReleaseDeliveryService,
)
from foundry_lite.application.services.pipeline_graph_model import pipeline_graph_fingerprint
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.release_delivery_repository import (
    SqlAlchemyReleaseDeliveryRepository,
)
from sqlalchemy import create_engine, update
from sqlalchemy.engine import Engine


class _RuntimeEvidence:
    def __init__(self) -> None:
        self.events: list[str] = []

    def _audit(self, _transaction: object, _ctx: RequestContext, **payload: object) -> None:
        self.events.append(str(payload["event_type"]))

    def _outbox(
        self, _transaction: object, _ctx: RequestContext, event_type: str, *args: object, **kwargs: object
    ) -> None:
        del args, kwargs
        self.events.append(f"outbox:{event_type}")

    def _error_payload(
        self,
        exc: Exception,
        _ctx: RequestContext,
        *,
        run_id: str,
    ) -> dict[str, object]:
        return {"type": type(exc).__name__, "message": str(exc), "runId": run_id}


class _SourceControlAdapter:
    profile_name = "github-release"

    def __init__(
        self,
        engine: Engine,
        repository: SqlAlchemyReleaseDeliveryRepository,
        snapshot: PullRequestSnapshot,
    ) -> None:
        self.engine = engine
        self.repository = repository
        self.snapshot = snapshot
        self.merge_status = SourceControlMergeStatus.LANDED
        self.lookup_status = SourceControlMergeStatus.LANDED
        self.searches: list[PullRequestSearch] = []
        self.merge_requests: list[SourceControlMergeRequest] = []
        self.lookup_targets: list[PullRequestTarget] = []
        self.candidate_lookups: list[SourceCandidatePublicationRequest] = []
        self.intent_statuses_at_merge: list[str | None] = []

    def inspect_source_ref(self, repository: SourceRepositoryRef, ref: str) -> SourceRefSnapshot:
        assert repository == self.snapshot.target.repository
        assert ref == "main"
        return SourceRefSnapshot(repository, ref, _sha("b"), _sha("f"))

    def publish_pull_request_candidate(
        self,
        request: SourceCandidatePublicationRequest,
    ) -> SourceCandidatePublicationReceipt:
        receipt = _publication_receipt(request)
        self.snapshot = replace(self.snapshot, target=receipt.to_pull_request_target())
        return receipt

    def lookup_pull_request_candidate(
        self,
        request: SourceCandidatePublicationRequest,
    ) -> SourceCandidatePublicationReceipt:
        self.candidate_lookups.append(request)
        return _publication_receipt(request)

    def find_pull_request(self, search: PullRequestSearch) -> PullRequestSnapshot:
        self.searches.append(search)
        return self.snapshot

    def inspect_pull_request(self, target: PullRequestTarget) -> PullRequestSnapshot:
        assert target == self.snapshot.target
        return self.snapshot

    def merge_pull_request(self, request: SourceControlMergeRequest) -> SourceControlMergeReceipt:
        self.merge_requests.append(request)
        row = _find_delivery(
            self.engine,
            self.repository,
            provider="github",
            operation="source_merge",
            idempotency_key=request.idempotency_key,
        )
        self.intent_statuses_at_merge.append(row.status if row is not None else None)
        receipt = _source_receipt(self.snapshot, self.merge_status, request.idempotency_key)
        self.snapshot = _merged_source_snapshot(self.snapshot)
        return receipt

    def lookup_merge(self, target: PullRequestTarget) -> SourceControlMergeReceipt:
        self.lookup_targets.append(target)
        return _source_receipt(self.snapshot, self.lookup_status, None)

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())


class _InfrastructureAdapter:
    profile_name = "render-deployment"

    def __init__(self, engine: Engine, repository: SqlAlchemyReleaseDeliveryRepository) -> None:
        self.engine = engine
        self.repository = repository
        self.start_outcome = "accepted"
        self.start_requests: list[InfrastructureDeploymentStartRequest] = []
        self.rollback_requests: list[InfrastructureDeploymentRollbackRequest] = []
        self.candidate_queries: list[InfrastructureDeploymentCandidateQuery] = []
        self.intent_statuses_at_mutation: list[str | None] = []
        self.reconciliation_candidates: tuple[InfrastructureDeploymentObservation, ...] = ()
        self.observations: dict[str, InfrastructureDeploymentObservation] = {}
        self.is_auto_deploy_enabled = False
        self.policy_requests: list[InfrastructureDeploymentServicePolicyRequest] = []

    def start(
        self,
        request: InfrastructureDeploymentStartRequest,
    ) -> InfrastructureDeploymentMutationResult:
        self.start_requests.append(request)
        self._capture_intent("application_deploy", request.idempotency_key)
        if self.start_outcome == "outcome_unknown":
            return InfrastructureDeploymentMutationResult(
                operation="start",
                outcome="outcome_unknown",
                provider_http_status=504,
                observation=None,
                rollback_target_deploy_id=None,
                is_safe_to_retry=False,
                reason="provider timeout after request dispatch",
            )
        observation = _deployment_observation(f"deploy-{len(self.start_requests)}", request.commit_id)
        self.observations[observation.deploy_id] = observation
        return _accepted_result("start", observation, None)

    def get(self, request: InfrastructureDeploymentGetRequest) -> InfrastructureDeploymentObservation:
        return self.observations[request.deploy_id]

    def get_service_policy(
        self,
        request: InfrastructureDeploymentServicePolicyRequest,
    ) -> InfrastructureDeploymentServicePolicyObservation:
        self.policy_requests.append(request)
        return InfrastructureDeploymentServicePolicyObservation(
            provider="render",
            service_id=request.service_id,
            is_auto_deploy_enabled=self.is_auto_deploy_enabled,
            source_repository_owner="acme",
            source_repository_name="platform",
            source_branch="main",
            service_type="web_service",
            is_suspended=False,
            provider_request_id="render-policy-request-1",
        )

    def list_candidates(
        self,
        query: InfrastructureDeploymentCandidateQuery,
    ) -> tuple[InfrastructureDeploymentObservation, ...]:
        self.candidate_queries.append(query)
        return self.reconciliation_candidates

    def rollback(
        self,
        request: InfrastructureDeploymentRollbackRequest,
    ) -> InfrastructureDeploymentMutationResult:
        self.rollback_requests.append(request)
        self._capture_intent("application_rollback", request.idempotency_key)
        observation = _deployment_observation(
            f"rollback-deploy-{len(self.rollback_requests)}",
            request.target_commit_id,
            trigger="rollback",
        )
        self.observations[observation.deploy_id] = observation
        return _accepted_result("rollback", observation, request.target_deploy_id)

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())

    def _capture_intent(self, operation: ReleaseDeliveryOperation, idempotency_key: str) -> None:
        row = _find_delivery(
            self.engine,
            self.repository,
            provider="render",
            operation=operation,
            idempotency_key=idempotency_key,
        )
        self.intent_statuses_at_mutation.append(row.status if row is not None else None)


@dataclass(frozen=True)
class _Harness:
    service: ExternalReleaseDeliveryService
    context: RequestContext
    proposal: dict[str, object]
    snapshot: PullRequestSnapshot
    source: _SourceControlAdapter
    infrastructure: _InfrastructureAdapter
    repository: SqlAlchemyReleaseDeliveryRepository
    engine: Engine
    runtime: _RuntimeEvidence


def test_source_evidence_projects_fresh_published_candidate() -> None:
    harness = _harness()

    evidence = harness.service.source_evidence(harness.context, harness.proposal)

    assert evidence["status"] == "ready"
    publication = evidence["publication"]
    assert isinstance(publication, dict)
    assert publication["operation"] == "source_publish"
    assert publication["status"] == "landed"


def test_source_evidence_contains_provider_read_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _harness()

    def _fail_read(_target: PullRequestTarget) -> PullRequestSnapshot:
        raise ValueError("provider response could not be decoded")

    monkeypatch.setattr(harness.source, "inspect_pull_request", _fail_read)
    evidence = harness.service.source_evidence(harness.context, harness.proposal)

    assert evidence["status"] == "unavailable"
    assert evidence["error"] == {"type": "ValueError"}
    assert "publication" not in evidence


def test_source_merge_writes_intent_before_provider_and_exact_replay_is_read_only() -> None:
    harness = _harness()
    arguments = _source_arguments(harness.snapshot, "source-key-1")

    first = harness.service.execute_source_merge(harness.context, harness.proposal, arguments)
    replay = harness.service.execute_source_merge(harness.context, harness.proposal, arguments)

    assert first["status"] == "landed"
    assert replay == first
    assert len(harness.source.candidate_lookups) == 0
    assert len(harness.source.merge_requests) == 1
    merge_request = harness.source.merge_requests[0]
    assert merge_request.expected_base_sha == harness.snapshot.base_sha
    assert merge_request.expected_rules_fingerprint == harness.snapshot.rules_fingerprint
    assert merge_request.expected_checks_fingerprint == harness.snapshot.checks_fingerprint
    assert harness.source.intent_statuses_at_merge == ["dispatching"]
    merge_row = _delivery(harness, "source_merge", "source-key-1")
    publication = _find_delivery(
        harness.engine,
        harness.repository,
        provider="github",
        operation="source_publish",
        idempotency_key="source-publication-seed",
    )
    assert publication is not None
    assert merge_row.status == "landed"
    assert merge_row.parent_delivery_id == publication.delivery_id
    assert merge_row.workflow_run_id == publication.workflow_run_id == "release-run-a"


def test_source_policy_failure_keeps_same_key_prepared_and_retry_merges_exactly_once() -> None:
    harness = _harness()
    arguments = _source_arguments(harness.snapshot, "source-policy-retry-1")
    harness.infrastructure.is_auto_deploy_enabled = True

    with pytest.raises(ConflictDetected, match="manual deployment policy was not verified"):
        harness.service.execute_source_merge(harness.context, harness.proposal, arguments)

    assert _delivery(harness, "source_merge", "source-policy-retry-1").status == "prepared"
    assert len(harness.source.merge_requests) == 0
    harness.infrastructure.is_auto_deploy_enabled = False

    completed = harness.service.execute_source_merge(harness.context, harness.proposal, arguments)

    assert completed["status"] == "landed"
    assert len(harness.source.candidate_lookups) == 0
    assert len(harness.source.merge_requests) == 1
    assert len(harness.infrastructure.policy_requests) == 2
    assert _delivery(harness, "source_merge", "source-policy-retry-1").status == "landed"


def test_ambiguous_source_merge_uses_lookup_without_a_second_merge() -> None:
    harness = _harness()
    harness.source.merge_status = SourceControlMergeStatus.AMBIGUOUS
    arguments = _source_arguments(harness.snapshot, "source-ambiguous-1")

    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown, match="ambiguous"):
        harness.service.execute_source_merge(harness.context, harness.proposal, arguments)

    assert _delivery(harness, "source_merge", "source-ambiguous-1").status == "ambiguous"
    reconciled = harness.service.execute_source_merge(harness.context, harness.proposal, arguments)

    assert reconciled["status"] == "landed"
    assert len(harness.source.candidate_lookups) == 0
    assert len(harness.source.merge_requests) == 1
    assert harness.source.lookup_targets == [harness.snapshot.target]


def test_source_merge_receipt_cannot_be_rebound_to_changed_review_evidence() -> None:
    harness = _harness()
    arguments = _source_arguments(harness.snapshot, "source-binding-1")
    harness.service.execute_source_merge(harness.context, harness.proposal, arguments)
    arguments["expectedSourceChecksFingerprint"] = _fingerprint("a")

    with pytest.raises(ConflictDetected, match="evidence changed after review"):
        harness.service.execute_source_merge(harness.context, harness.proposal, arguments)

    assert len(harness.source.merge_requests) == 1
    assert _delivery(harness, "source_merge", "source-binding-1").status == "landed"


def test_application_deploy_records_acceptance_and_exact_replay_skips_second_start() -> None:
    harness = _harness()
    merge_row = _land_source_merge(harness)

    first = harness.service.start_application_deployment(
        harness.context,
        "proposal-1",
        _sha("e"),
        "deploy-key-1",
    )
    replay = harness.service.start_application_deployment(
        harness.context,
        "proposal-1",
        _sha("e"),
        "deploy-key-1",
    )

    assert first["status"] == "landed"
    assert first["providerResourceId"] == "deploy-1"
    assert replay == first
    assert len(harness.infrastructure.start_requests) == 1
    assert harness.infrastructure.intent_statuses_at_mutation == ["dispatching"]
    deploy_row = _delivery(harness, "application_deploy", "deploy-key-1")
    assert deploy_row.parent_delivery_id == merge_row.delivery_id
    assert deploy_row.workflow_run_id == merge_row.workflow_run_id


def test_application_deploy_rejects_commit_without_one_exact_source_merge_parent() -> None:
    harness = _harness()
    _land_source_merge(harness)

    with pytest.raises(ConflictDetected, match="one exact landed source merge"):
        harness.service.start_application_deployment(
            harness.context,
            "proposal-1",
            _sha("f"),
            "deploy-unbound-commit",
        )

    assert harness.infrastructure.start_requests == []


def test_application_deploy_timeout_reconciles_without_blind_retry() -> None:
    harness = _harness()
    _land_source_merge(harness)
    harness.infrastructure.start_outcome = "outcome_unknown"

    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown, match="ambiguous"):
        harness.service.start_application_deployment(
            harness.context,
            "proposal-1",
            _sha("e"),
            "deploy-timeout-1",
        )

    assert _delivery(harness, "application_deploy", "deploy-timeout-1").status == "ambiguous"
    reconciled_observation = _deployment_observation("deploy-reconciled", _sha("e"))
    harness.infrastructure.reconciliation_candidates = (reconciled_observation,)
    harness.infrastructure.observations[reconciled_observation.deploy_id] = reconciled_observation
    reconciled = harness.service.start_application_deployment(
        harness.context,
        "proposal-1",
        _sha("e"),
        "deploy-timeout-1",
    )

    assert reconciled["status"] == "landed"
    assert reconciled["providerResourceId"] == "deploy-reconciled"
    assert len(harness.infrastructure.start_requests) == 1
    assert len(harness.infrastructure.candidate_queries) == 1


def test_application_rollback_creates_a_new_deployment_receipt() -> None:
    harness = _harness()
    _land_source_merge(harness)
    harness.service.start_application_deployment(harness.context, "proposal-1", _sha("e"), "deploy-prior")
    harness.service.start_application_deployment(harness.context, "proposal-1", _sha("e"), "deploy-current")
    target = harness.service.application_rollback_target(harness.context, "proposal-1")
    assert target is not None

    result = harness.service.start_application_rollback(
        harness.context,
        "proposal-1",
        target,
        "rollback-key-1",
    )

    assert result["status"] == "landed"
    assert result["providerResourceId"] == "rollback-deploy-1"
    assert result["providerResourceId"] != target["targetDeployId"]
    assert result["priorResourceId"] == "deploy-2"
    assert harness.infrastructure.rollback_requests[0].target_deploy_id == "deploy-1"
    assert harness.infrastructure.rollback_requests[0].target_commit_id == _sha("e")
    rollback_row = _delivery(harness, "application_rollback", "rollback-key-1")
    assert rollback_row.parent_delivery_id == _delivery(harness, "application_deploy", "deploy-current").delivery_id


def test_application_rollback_fails_closed_when_current_delivery_parent_is_ambiguous() -> None:
    harness = _harness()
    _land_source_merge(harness)
    harness.service.start_application_deployment(harness.context, "proposal-1", _sha("e"), "deploy-one")
    harness.service.start_application_deployment(harness.context, "proposal-1", _sha("e"), "deploy-two")
    first = _delivery(harness, "application_deploy", "deploy-one")
    with harness.engine.begin() as transaction:
        transaction.execute(
            update(db.governed_release_deliveries)
            .where(db.governed_release_deliveries.c.id == first.delivery_id)
            .values(provider_resource_id="deploy-2")
        )

    with pytest.raises(ConflictDetected, match="missing or ambiguous"):
        harness.service.start_application_rollback(
            harness.context,
            "proposal-1",
            {
                "targetDeployId": "deploy-1",
                "targetCommitId": _sha("e"),
                "rolledBackFromDeployId": "deploy-2",
            },
            "rollback-ambiguous-parent",
        )

    assert harness.infrastructure.rollback_requests == []


def test_delivery_evidence_lists_receipts_observations_and_safe_rollback_target() -> None:
    harness = _harness()
    _land_source_merge(harness)
    harness.service.start_application_deployment(harness.context, "proposal-1", _sha("e"), "deploy-key-a")
    harness.service.start_application_deployment(harness.context, "proposal-1", _sha("e"), "deploy-key-b")

    evidence = harness.service.delivery_evidence(harness.context, "proposal-1")

    deliveries = evidence["deliveries"]
    observations = evidence["deploymentObservations"]
    assert isinstance(deliveries, list)
    assert isinstance(observations, list)
    assert {item["providerResourceId"] for item in deliveries} == {
        "pull:17",
        _sha("e"),
        "deploy-1",
        "deploy-2",
    }
    assert {item["deployId"] for item in observations} == {"deploy-1", "deploy-2"}
    assert evidence["applicationRollbackTarget"] == {
        "targetDeployId": "deploy-1",
        "targetCommitId": _sha("e"),
        "rolledBackFromDeployId": "deploy-2",
    }


def test_application_rollback_replay_keeps_original_binding_while_current_chain_advances() -> None:
    harness = _harness()
    _land_source_merge(harness)
    harness.service.start_application_deployment(harness.context, "proposal-1", _sha("e"), "deploy-key-a")
    harness.service.start_application_deployment(harness.context, "proposal-1", _sha("e"), "deploy-key-b")
    original_target = harness.service.application_rollback_target(harness.context, "proposal-1")
    assert original_target is not None

    harness.service.start_application_rollback(
        harness.context,
        "proposal-1",
        original_target,
        "rollback-key-chain",
    )

    assert harness.service.application_rollback_target(harness.context, "proposal-1") == {
        "targetDeployId": "deploy-2",
        "targetCommitId": _sha("e"),
        "rolledBackFromDeployId": "rollback-deploy-1",
    }
    assert (
        harness.service.application_rollback_target(
            harness.context,
            "proposal-1",
            "rollback-key-chain",
        )
        == original_target
    )


def _harness() -> _Harness:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    repository = SqlAlchemyReleaseDeliveryRepository(engine)
    snapshot = _source_snapshot()
    source = _SourceControlAdapter(engine, repository, snapshot)
    infrastructure = _InfrastructureAdapter(engine, repository)
    runtime = _RuntimeEvidence()
    service = ExternalReleaseDeliveryService(
        engine=engine,
        governed_release_delivery_config=_delivery_config(snapshot.target.repository),
        release_delivery_repository=repository,
        source_control_release_adapter=source,
        infrastructure_deployment_adapter=infrastructure,
    )
    service.bind_collaborators({"runtime_service": runtime})
    context = RequestContext(
        tenant_id="tenant-a",
        actor_user_id="reviewer-a",
        request_id="request-a",
        roles=("admin",),
        application_id="application-a",
        governed_release_run_id="release-run-a",
        governed_release_binding_hash=_fingerprint("f"),
    )
    proposal = _source_proposal()
    service.publish_source_candidate(
        context,
        "pipeline",
        proposal,
        {"idempotencyKey": "source-publication-seed"},
    )
    return _Harness(service, context, proposal, source.snapshot, source, infrastructure, repository, engine, runtime)


def _delivery_config(repository: SourceRepositoryRef) -> GovernedReleaseDeliveryConfig:
    return GovernedReleaseDeliveryConfig(
        source_repository=repository,
        source_base_ref="main",
        source_head_prefix="codex/",
        source_merge_method=SourceControlMergeMethod.SQUASH,
        is_source_control_required=True,
        deployment_service_id="render-service-1",
        deployment_environment="production",
        is_deployment_required=True,
    )


def _source_snapshot() -> PullRequestSnapshot:
    repository = SourceRepositoryRef("github", 42, "acme", "platform")
    target = PullRequestTarget(repository, 17, "main", _sha("a"))
    approval = PullRequestReviewEvidence(7, "reviewer", "APPROVED", target.expected_head_sha, "2026-08-09T01:00:00Z")
    rule = BranchRuleEvidence("required_status_checks", 9, "Repository", "acme/platform", _fingerprint("b"))
    check = RequiredCheckEvidence(
        "quality-gate", target.expected_head_sha, "completed", "success", "check_run", 1, True
    )
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
        review_decision=SourceControlReviewDecision.APPROVED,
        required_approval_count=1,
        approvals=(approval,),
        active_rules=(rule,),
        required_checks=(check,),
        is_merge_queue_required=False,
        rules_fingerprint=_fingerprint("c"),
        checks_fingerprint=_fingerprint("d"),
        blocking_reasons=(),
        is_ready_to_merge=True,
        provider_request_id="github-request-1",
    )


def _source_proposal() -> dict[str, object]:
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


def _publication_receipt(
    request: SourceCandidatePublicationRequest,
) -> SourceCandidatePublicationReceipt:
    binding = SourceCandidateCommitBinding(
        expected_base_sha=request.expected_base_sha,
        expected_tree_sha=_sha("9"),
        expected_head_ref=request.expected_head_ref,
        manifest=request.manifest,
    )
    return SourceCandidatePublicationReceipt(
        status=SourceCandidatePublicationStatus.PUBLISHED,
        repository=request.repository,
        expected_base_ref=request.expected_base_ref,
        expected_head_ref=request.expected_head_ref,
        expected_base_sha=request.expected_base_sha,
        manifest_fingerprint=request.manifest.manifest_fingerprint,
        idempotency_key=request.idempotency_key,
        head_sha=_sha("a"),
        pull_number=17,
        commit_binding=binding,
        provider_request_id="github-publication-request-1",
        evidence={"reason": "published"},
    )


def _merged_source_snapshot(snapshot: PullRequestSnapshot) -> PullRequestSnapshot:
    return PullRequestSnapshot(
        target=snapshot.target,
        state="closed",
        is_draft=False,
        is_merged=True,
        base_sha=snapshot.base_sha,
        head_ref=snapshot.head_ref,
        author_id=snapshot.author_id,
        author_login=snapshot.author_login,
        mergeable_state="unknown",
        test_merge_commit_sha=snapshot.test_merge_commit_sha,
        checks_commit_sha=snapshot.checks_commit_sha,
        review_decision=snapshot.review_decision,
        required_approval_count=snapshot.required_approval_count,
        approvals=snapshot.approvals,
        active_rules=snapshot.active_rules,
        required_checks=snapshot.required_checks,
        is_merge_queue_required=False,
        rules_fingerprint=snapshot.rules_fingerprint,
        checks_fingerprint=snapshot.checks_fingerprint,
        blocking_reasons=("pull_request_already_merged",),
        is_ready_to_merge=False,
        provider_request_id="github-request-after-merge",
    )


def _source_arguments(snapshot: PullRequestSnapshot, idempotency_key: str) -> dict[str, object]:
    return {
        "idempotencyKey": idempotency_key,
        "expectedSourceBaseSha": snapshot.base_sha,
        "expectedSourceHeadSha": snapshot.target.expected_head_sha,
        "expectedSourceChecksFingerprint": snapshot.checks_fingerprint,
        "expectedSourceRulesFingerprint": snapshot.rules_fingerprint,
    }


def _source_receipt(
    snapshot: PullRequestSnapshot,
    status: SourceControlMergeStatus,
    idempotency_key: str | None,
) -> SourceControlMergeReceipt:
    is_landed = status is SourceControlMergeStatus.LANDED
    return SourceControlMergeReceipt(
        status=status,
        repository_id=snapshot.target.repository.repository_id,
        pull_number=snapshot.target.pull_number,
        head_sha=snapshot.target.expected_head_sha,
        merge_commit_sha=_sha("e") if is_landed else None,
        merged_at="2026-08-09T02:00:00Z" if is_landed else None,
        provider_request_id="github-request-merge",
        idempotency_key=idempotency_key,
        evidence={"exactHeadVerified": True},
    )


def _deployment_observation(
    deploy_id: str,
    commit_id: str,
    *,
    trigger: str = "api",
) -> InfrastructureDeploymentObservation:
    timestamp = datetime.now(UTC)
    return InfrastructureDeploymentObservation(
        provider="render",
        service_id="render-service-1",
        deploy_id=deploy_id,
        status="queued",
        provider_status="build_in_progress",
        commit_id=commit_id,
        trigger=trigger,
        created_at=timestamp,
        started_at=None,
        updated_at=timestamp,
        finished_at=None,
        is_terminal=False,
        is_successful=False,
        provider_request_id=f"render-request-{deploy_id}",
    )


def _accepted_result(
    operation: str,
    observation: InfrastructureDeploymentObservation,
    rollback_target: str | None,
) -> InfrastructureDeploymentMutationResult:
    assert operation in {"start", "rollback"}
    return InfrastructureDeploymentMutationResult(
        operation=operation,
        outcome="accepted",
        provider_http_status=201,
        observation=observation,
        rollback_target_deploy_id=rollback_target,
        is_safe_to_retry=False,
        reason="provider accepted the immutable deployment request",
    )


def _find_delivery(
    engine: Engine,
    repository: SqlAlchemyReleaseDeliveryRepository,
    *,
    provider: str,
    operation: ReleaseDeliveryOperation,
    idempotency_key: str,
) -> ReleaseDeliveryRecord | None:
    with engine.begin() as transaction:
        return repository.find_by_idempotency(
            transaction=transaction,
            tenant_id="tenant-a",
            provider=provider,
            operation=operation,
            idempotency_key=idempotency_key,
        )


def _delivery(
    harness: _Harness,
    operation: ReleaseDeliveryOperation,
    idempotency_key: str,
) -> ReleaseDeliveryRecord:
    provider = "github" if operation == "source_merge" else "render"
    row = _find_delivery(
        harness.engine, harness.repository, provider=provider, operation=operation, idempotency_key=idempotency_key
    )
    assert row is not None
    return row


def _land_source_merge(harness: _Harness) -> ReleaseDeliveryRecord:
    key = "source-lineage-seed"
    existing = _find_delivery(
        harness.engine,
        harness.repository,
        provider="github",
        operation="source_merge",
        idempotency_key=key,
    )
    if existing is not None:
        return existing
    harness.service.execute_source_merge(
        harness.context,
        harness.proposal,
        _source_arguments(harness.snapshot, key),
    )
    return _delivery(harness, "source_merge", key)


def _proposal_deliveries(harness: _Harness) -> tuple[ReleaseDeliveryRecord, ...]:
    with harness.engine.begin() as transaction:
        return harness.repository.list_for_proposal(
            transaction=transaction,
            tenant_id=harness.context.tenant_id,
            proposal_id="proposal-1",
            limit=20,
        )


def _sha(character: str) -> str:
    return character * 40


def _fingerprint(character: str) -> str:
    return f"sha256:{character * 64}"
