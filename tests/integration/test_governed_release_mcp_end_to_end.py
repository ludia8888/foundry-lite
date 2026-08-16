"""Real governed release flows through OAuth, MCP, and product services."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.dependency_release import GovernedReleaseDependencies
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.governed_release_delivery_config import GovernedReleaseDeliveryConfig
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
    SourceControlMergeReceipt,
    SourceControlMergeRequest,
    SourceControlMergeStatus,
    SourceControlReviewDecision,
    SourceRefSnapshot,
    SourceRepositoryRef,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied
from foundry_lite.infrastructure.auth import JwtOidcAuthConfig, JwtOidcAuthProvider
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app
from pytest import MonkeyPatch

_RELEASE_SCOPE = "osdk:connector:governed_release:execute"
_REVIEWER = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="gpt-release-reviewer",
    roles=("admin", "data_engineer"),
    request_id="req-gpt-release-reviewer",
)
_SUBMITTER = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="release-submitter",
    roles=("admin", "data_engineer"),
    request_id="req-release-submitter",
)


def test_ontology_release_completes_review_activation_branch_merge_and_rollback_in_mcp(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "ontology-runtime")
    local_foundry = FoundryLite(dependencies=dependencies)
    _seed_ontology(local_foundry, tmp_path)
    source_control = _DeterministicSourceControlRelease()
    foundry = _protected_foundry(dependencies, source_control=source_control)
    with pytest.raises(PermissionDenied, match="Governed Release"):
        foundry.ontology.apply_text(_ontology_yaml(is_status_deprecated=True), ctx=_SUBMITTER)

    author_client, author_endpoint, author_headers = _release_client(
        foundry,
        monkeypatch,
        "ontology-author",
        actor=_SUBMITTER,
    )
    workspace = _read_tool(
        author_client,
        author_endpoint,
        author_headers,
        "open_release_workspace",
        {"releaseKind": "ontology", "branchName": "gpt-governed-ontology"},
    )
    assert workspace["stage"] == "workspace_ready"
    created_branch = _confirmed_tool(
        author_client,
        author_endpoint,
        author_headers,
        "create_release_branch",
        {
            "releaseKind": "ontology",
            "branchName": "gpt-governed-ontology",
            "idempotencyKey": "gpt-ontology-branch",
        },
    )
    branch_id = str(_mapping(created_branch["releaseEvidence"])["branchId"])
    assert branch_id == _mapping(created_branch["candidate"])["id"]
    builder_client, builder_app_id, builder_headers = _builder_client(
        foundry,
        monkeypatch,
        "ontology_editing",
        "ontology-author",
        _SUBMITTER,
    )
    workspace_ref = str(_mapping(created_branch["releaseEvidence"])["builderWorkspaceRef"])
    edited = _builder_tool(
        builder_client,
        builder_app_id,
        builder_headers,
        "ontology_editing",
        workspace_ref,
        "ontology.branch.apply_patch",
        {
            "upsertResources": [
                {
                    "kind": "objectType",
                    "definition": _ontology_object_definition(is_status_deprecated=True),
                }
            ],
            "deleteResources": [],
            "changeSummary": "Deprecate status property from GPT",
        },
    )
    updated = _mapping(edited["branch"])
    validated = _builder_tool(
        builder_client,
        builder_app_id,
        builder_headers,
        "ontology_editing",
        workspace_ref,
        "ontology.branch.validate",
        {},
    )
    assert _mapping(validated["validation"])["object_type_count"] == 1
    proposed = _builder_tool(
        builder_client,
        builder_app_id,
        builder_headers,
        "ontology_editing",
        workspace_ref,
        "ontology.branch.propose",
        {
            "title": "Deprecate status property from GPT",
            "description": "Authored and validated through Builder MCP",
            "idempotencyKey": "gpt-ontology-proposal",
        },
    )
    proposal = _mapping(proposed["proposal"])
    proposal_id = str(proposal["id"])
    fingerprint = str(proposal["fingerprint"])
    published = _confirmed_tool(
        author_client,
        author_endpoint,
        author_headers,
        "publish_release_candidate",
        {
            "releaseKind": "ontology",
            "proposalId": proposal_id,
            "idempotencyKey": "gpt-ontology-source-publication",
        },
    )
    publication = _mapping(_mapping(published["releaseEvidence"])["externalSourceControl"])["publication"]
    publication_evidence = _mapping(publication)
    publication_result = _mapping(publication_evidence["result"])
    assert publication_evidence["operation"] == "source_publish"
    assert publication_evidence["ledgerStatus"] == "landed"
    assert publication_evidence["receiptStatus"] == "published"
    assert publication_evidence["createdBy"] == _SUBMITTER.actor_user_id
    assert publication_result["manifestFingerprint"] == source_control.publication_receipts[0].manifest_fingerprint
    assert publication_result["headSha"] == source_control.publication_receipts[0].head_sha
    assert publication_result["pullNumber"] == source_control.publication_receipts[0].pull_number
    client, endpoint, headers = _release_client(
        foundry,
        monkeypatch,
        "ontology-author",
        actor=_REVIEWER,
    )

    inbox = _read_tool(
        client,
        endpoint,
        headers,
        "list_release_inbox",
        {"releaseKind": "ontology"},
    )
    assert inbox["proposalId"] == proposal_id
    assert inbox["nextActions"] == ["assign_release_reviewer"]
    claimed = _confirmed_tool(
        client,
        endpoint,
        headers,
        "assign_release_reviewer",
        {
            "releaseKind": "ontology",
            "proposalId": proposal_id,
            "idempotencyKey": "gpt-ontology-review-claim",
        },
    )
    assert _mapping(claimed["candidate"])["assigneeUserId"] == _REVIEWER.actor_user_id

    candidate = _read_tool(
        client,
        endpoint,
        headers,
        "get_release_candidate",
        {"releaseKind": "ontology", "proposalId": proposal_id},
    )
    assert candidate["stage"] == "awaiting_review"
    assert candidate["nextActions"] == ["submit_release_decision"]
    ontology_evidence = _mapping(candidate["releaseEvidence"])
    # The candidate names every changed resource, not only the compatibility-relevant ones, so a
    # reviewer sees the additions instead of an empty change set.
    ontology_diff = _mapping(ontology_evidence["changeDiff"])
    assert ontology_diff["completeness"] == "full_resource"
    assert "full_resource_diff" not in _mapping(ontology_evidence["riskClassification"])["missingEvidence"]
    ontology_validation = ontology_evidence["validationEvidence"]
    assert isinstance(ontology_validation, list)
    assert _mapping(ontology_validation[-1])["status"] == "passed"
    assert _mapping(ontology_validation[-1])["proofKind"] == "github_merge_result_or_head_required_checks"
    assert _mapping(ontology_evidence["riskClassification"])["isComplete"] is False
    source_merge_binding = _source_merge_binding(ontology_evidence)

    approved = _confirmed_tool(
        client,
        endpoint,
        headers,
        "submit_release_decision",
        {
            "releaseKind": "ontology",
            "proposalId": proposal_id,
            "decision": "approve",
            "expectedFingerprint": fingerprint,
            "comment": "reviewed inside GPT",
            "idempotencyKey": "gpt-ontology-decision",
        },
    )
    assert approved["stage"] == "approved"

    with pytest.raises(PermissionDenied, match="Governed Release"):
        foundry.ontology.execute_proposal(
            proposal_id,
            expected_fingerprint=fingerprint,
            ctx=_REVIEWER,
        )

    active = _confirmed_tool(
        client,
        endpoint,
        headers,
        "execute_approved_release",
        {
            "releaseKind": "ontology",
            "proposalId": proposal_id,
            "expectedFingerprint": fingerprint,
            **source_merge_binding,
            "idempotencyKey": "gpt-ontology-activation",
        },
    )
    assert active["stage"] == "active"
    assert active["rollbackTarget"] == {
        "targetVersionNumber": 1,
        "expectedActiveVersionNumber": 2,
    }
    active_source = _mapping(_mapping(active["releaseEvidence"])["externalSourceControl"])
    active_source_delivery = _mapping(active_source["delivery"])
    assert active_source_delivery["operation"] == "source_merge"
    assert active_source_delivery["ledgerStatus"] == "landed"
    assert _mapping(active_source["publication"])["deliveryId"] == publication_evidence["deliveryId"]
    assert foundry.ontology.get_branch(str(updated["id"]), ctx=_REVIEWER)["status"] == "merged"
    assert foundry.ontology.catalog(ctx=_REVIEWER)["versionNumber"] == 2
    assert len(source_control.merge_requests) == 1
    merge_request = source_control.merge_requests[0]
    assert merge_request.target == source_control.publication_receipts[0].to_pull_request_target()
    assert merge_request.target.candidate_binding is not None
    assert (
        merge_request.target.candidate_binding.manifest.manifest_fingerprint
        == publication_result["manifestFingerprint"]
    )

    with pytest.raises(PermissionDenied, match="Governed Release"):
        foundry.ontology.rollback(1, idempotency_key="direct-ontology-rollback", ctx=_REVIEWER)

    rolled_back = _confirmed_tool(
        client,
        endpoint,
        headers,
        "rollback_release",
        {
            "releaseKind": "ontology",
            "proposalId": proposal_id,
            "targetVersionNumber": 1,
            "expectedActiveVersionNumber": 2,
            "idempotencyKey": "gpt-ontology-rollback",
        },
    )
    assert rolled_back["stage"] == "superseded"
    assert _mapping(rolled_back["lastOperation"])["operation"] == "rollback_active"
    catalog = foundry.ontology.catalog(ctx=_REVIEWER)
    assert catalog["versionNumber"] == 3
    status_property = next(row for row in catalog["objectTypes"][0]["properties"] if row["apiName"] == "status")
    assert status_property.get("deprecated", False) is False


def test_pipeline_release_completes_review_merge_promote_status_and_explicit_rollback_in_mcp(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "pipeline-runtime")
    local_foundry = FoundryLite(dependencies=dependencies)
    _seed_pipeline_dataset(local_foundry, tmp_path)
    seed_version = _seed_promoted_pipeline(local_foundry)
    source_control = _DeterministicSourceControlRelease()
    foundry = _protected_foundry(dependencies, source_control=source_control)
    author_client, author_endpoint, author_headers = _release_client(
        foundry,
        monkeypatch,
        "pipeline-author",
        actor=_SUBMITTER,
    )
    _read_tool(
        author_client,
        author_endpoint,
        author_headers,
        "open_release_workspace",
        {
            "releaseKind": "pipeline",
            "branchName": "gpt-candidate",
            "pipelineId": "gpt_release_pipeline",
        },
    )
    created_branch = _confirmed_tool(
        author_client,
        author_endpoint,
        author_headers,
        "create_release_branch",
        {
            "releaseKind": "pipeline",
            "branchName": "gpt-candidate",
            "pipelineId": "gpt_release_pipeline",
            "idempotencyKey": "gpt-pipeline-candidate-branch",
        },
    )
    branch_id = str(_mapping(created_branch["releaseEvidence"])["branchId"])
    branch_candidate = _mapping(created_branch["candidate"])
    assert branch_id == branch_candidate["id"]
    initial_fingerprint = str(branch_candidate["graphFingerprint"])
    builder_client, builder_app_id, builder_headers = _builder_client(
        foundry,
        monkeypatch,
        "data_integration",
        "pipeline-author",
        _SUBMITTER,
    )
    workspace_ref = str(_mapping(created_branch["releaseEvidence"])["builderWorkspaceRef"])
    updated = _builder_tool(
        builder_client,
        builder_app_id,
        builder_headers,
        "data_integration",
        workspace_ref,
        "pipeline.branch.update_graph",
        {
            "graph": _pipeline_graph("clean.gpt_release_v2"),
            "expectedFingerprint": initial_fingerprint,
        },
    )
    validated = _builder_tool(
        builder_client,
        builder_app_id,
        builder_headers,
        "data_integration",
        workspace_ref,
        "pipeline.branch.validate",
        {},
    )
    assert validated["valid"] is True
    tested = _builder_tool(
        builder_client,
        builder_app_id,
        builder_headers,
        "data_integration",
        workspace_ref,
        "pipeline.branch.run_tests",
        {},
    )
    assert tested["status"] == "passed"
    proposal = _builder_tool(
        builder_client,
        builder_app_id,
        builder_headers,
        "data_integration",
        workspace_ref,
        "pipeline.branch.propose",
        {
            "title": "Promote GPT pipeline candidate",
            "description": "Authored and tested through Builder MCP",
            "idempotencyKey": "gpt-pipeline-candidate-proposal",
        },
    )
    proposal_id = str(proposal["id"])
    published = _confirmed_tool(
        author_client,
        author_endpoint,
        author_headers,
        "publish_release_candidate",
        {
            "releaseKind": "pipeline",
            "proposalId": proposal_id,
            "idempotencyKey": "gpt-pipeline-source-publication",
        },
    )
    publication = _mapping(_mapping(published["releaseEvidence"])["externalSourceControl"])["publication"]
    assert _mapping(publication)["receiptStatus"] == "published"
    client, endpoint, headers = _release_client(
        foundry,
        monkeypatch,
        "pipeline-author",
        actor=_REVIEWER,
    )

    inbox = _read_tool(
        client,
        endpoint,
        headers,
        "list_release_inbox",
        {"releaseKind": "pipeline"},
    )
    assert inbox["proposalId"] == proposal_id
    claimed = _confirmed_tool(
        client,
        endpoint,
        headers,
        "assign_release_reviewer",
        {
            "releaseKind": "pipeline",
            "proposalId": proposal_id,
            "idempotencyKey": "gpt-pipeline-review-claim",
        },
    )
    assert _mapping(claimed["candidate"])["assignedTo"] == _REVIEWER.actor_user_id

    candidate = _read_tool(
        client,
        endpoint,
        headers,
        "get_release_candidate",
        {"releaseKind": "pipeline", "proposalId": proposal_id},
    )
    candidate_evidence = _mapping(candidate["releaseEvidence"])
    assert _mapping(candidate_evidence["changeDiff"])["changed"] is True
    pipeline_validation = candidate_evidence["validationEvidence"]
    assert isinstance(pipeline_validation, list)
    assert _mapping(pipeline_validation[0])["status"] == "passed"
    assert _mapping(pipeline_validation[1])["status"] == "passed"
    assert _mapping(pipeline_validation[1])["proofKind"] == "github_merge_result_or_head_required_checks"
    assert _mapping(candidate_evidence["impactScope"])["isComplete"] is True
    assert _mapping(candidate_evidence["riskClassification"])["isComplete"] is True
    source_merge_binding = _source_merge_binding(candidate_evidence)

    approved = _confirmed_tool(
        client,
        endpoint,
        headers,
        "submit_release_decision",
        {
            "releaseKind": "pipeline",
            "proposalId": proposal_id,
            "decision": "approve",
            "idempotencyKey": "gpt-pipeline-decision",
        },
    )
    assert approved["stage"] == "approved"

    with pytest.raises(PermissionDenied, match="Governed Release"):
        foundry.pipelines.execute(proposal_id, ctx=_REVIEWER)

    merged = _confirmed_tool(
        client,
        endpoint,
        headers,
        "execute_approved_release",
        {
            "releaseKind": "pipeline",
            "proposalId": proposal_id,
            **source_merge_binding,
            "idempotencyKey": "gpt-pipeline-merge",
        },
    )
    merged_version = _mapping(merged["releaseEvidence"])["mergedVersion"]
    version_id = str(_mapping(merged_version)["id"])
    assert merged["stage"] == "merged"
    assert foundry.pipelines.get_branch(str(updated["id"]), ctx=_REVIEWER)["status"] == "merged"
    assert len(source_control.merge_requests) == 1
    assert source_control.merge_requests[0].target == source_control.publication_receipts[0].to_pull_request_target()

    with pytest.raises(PermissionDenied, match="Governed Release"):
        foundry.pipelines.deploy(
            "gpt_release_pipeline",
            version_id,
            idempotency_key="direct-pipeline-deploy",
            ctx=_REVIEWER,
        )

    deployed = _confirmed_tool(
        client,
        endpoint,
        headers,
        "deploy_release",
        {
            "releaseKind": "pipeline",
            "proposalId": proposal_id,
            "pipelineId": "gpt_release_pipeline",
            "versionId": version_id,
            "idempotencyKey": "gpt-pipeline-deploy",
        },
    )
    assert deployed["stage"] == "deployed"
    rollback_target = _mapping(deployed["rollbackTarget"])
    assert rollback_target["targetVersionId"] == seed_version["id"]
    current = _mapping(_mapping(deployed["releaseEvidence"])["currentDeployment"])
    assert current["status"] == "PROMOTED"

    status = _read_tool(
        client,
        endpoint,
        headers,
        "get_release_status",
        {"releaseKind": "pipeline", "proposalId": proposal_id},
    )
    assert status["stage"] == "deployed"
    assert status["observedAs"] == "current_status"
    status_evidence = _mapping(status["releaseEvidence"])
    timeline = status_evidence["executionTimeline"]
    audit_evidence = status_evidence["auditEvidence"]
    assert isinstance(timeline, list) and timeline
    assert isinstance(audit_evidence, list) and audit_evidence
    assert any(_mapping(item)["status"] == "succeeded" for item in timeline)
    assert all("after_ref" not in _mapping(item) for item in audit_evidence)
    assert status_evidence["failureDetails"] is None

    with pytest.raises(PermissionDenied, match="Governed Release"):
        foundry.pipelines.deploy(
            "gpt_release_pipeline",
            str(seed_version["id"]),
            idempotency_key="direct-pipeline-rollback",
            ctx=_REVIEWER,
        )

    rolled_back = _confirmed_tool(
        client,
        endpoint,
        headers,
        "rollback_release",
        {
            "releaseKind": "pipeline",
            "proposalId": proposal_id,
            **rollback_target,
            "idempotencyKey": "gpt-pipeline-rollback",
        },
    )
    assert rolled_back["stage"] == "superseded"
    assert _mapping(rolled_back["lastOperation"])["operation"] == "rollback_deployed"
    deployment_items = foundry.pipelines.list_deployments("gpt_release_pipeline", ctx=_REVIEWER)["items"]
    assert isinstance(deployment_items, list) and deployment_items
    latest = _mapping(deployment_items[0])
    assert latest["status"] == "PROMOTED"
    assert latest["versionId"] == seed_version["id"]
    assert latest["rolledBackFromId"] == current["id"]


def _protected_foundry(
    dependencies: CoreDependencies,
    *,
    source_control: _DeterministicSourceControlRelease | None = None,
) -> FoundryLite:
    """Compose protected services over a schema initialized by the local setup phase."""

    aip = dependencies.aip
    if source_control is not None:
        local_release = aip.governed_release
        governed_release = GovernedReleaseDependencies(
            config=GovernedReleaseDeliveryConfig(
                source_repository=source_control.repository,
                source_base_ref="main",
                source_head_prefix="codex/",
                is_source_control_required=True,
            ),
            source_control_adapter=source_control,
            infrastructure_deployment_adapter=local_release.infrastructure_deployment_adapter,
            delivery_repository=local_release.delivery_repository,
        )
        aip = replace(aip, governed_release=governed_release)
    protected_dependencies = CoreDependencies(
        paths=dependencies.paths,
        security=dependencies.security,
        action=dependencies.action,
        data=dependencies.data,
        object_store=dependencies.object_store,
        runtime=dependencies.runtime,
        aip=aip,
        media=dependencies.media,
        source=dependencies.source,
        pipeline_dag_orchestrator=dependencies.pipeline_dag_orchestrator,
        profile="production",
    )
    return FoundryLite(dependencies=protected_dependencies, should_initialize_schema=False)


class _DeterministicSourceControlRelease:
    """Provider-neutral fake that preserves exact publication and merge bindings."""

    profile_name = "github-release-test-double"

    def __init__(self) -> None:
        self.repository = SourceRepositoryRef("github", 4242, "foundry-lite", "governed-release-e2e")
        self.publication_requests: list[SourceCandidatePublicationRequest] = []
        self.publication_receipts: list[SourceCandidatePublicationReceipt] = []
        self.candidate_lookups: list[SourceCandidatePublicationRequest] = []
        self.merge_requests: list[SourceControlMergeRequest] = []
        self._publication_by_proposal: dict[str, SourceCandidatePublicationReceipt] = {}
        self._merge_by_pull: dict[int, SourceControlMergeReceipt] = {}

    def inspect_source_ref(self, repository: SourceRepositoryRef, ref: str) -> SourceRefSnapshot:
        assert repository == self.repository
        assert ref == "main"
        return SourceRefSnapshot(repository, ref, _sha("b"), _sha("c"))

    def publish_pull_request_candidate(
        self,
        request: SourceCandidatePublicationRequest,
    ) -> SourceCandidatePublicationReceipt:
        self.publication_requests.append(request)
        existing = self._publication_by_proposal.get(request.proposal_id)
        if existing is not None:
            return existing
        receipt = _source_publication_receipt(request, pull_number=40 + len(self.publication_receipts))
        self.publication_receipts.append(receipt)
        self._publication_by_proposal[request.proposal_id] = receipt
        return receipt

    def lookup_pull_request_candidate(
        self,
        request: SourceCandidatePublicationRequest,
    ) -> SourceCandidatePublicationReceipt:
        self.candidate_lookups.append(request)
        receipt = self._publication_by_proposal.get(request.proposal_id)
        assert receipt is not None
        assert receipt.idempotency_key == request.idempotency_key
        assert receipt.manifest_fingerprint == request.manifest.manifest_fingerprint
        return receipt

    def find_pull_request(self, search: PullRequestSearch) -> PullRequestSnapshot:
        receipt = next(
            receipt
            for receipt in self.publication_receipts
            if receipt.expected_base_ref == search.expected_base_ref
            and receipt.expected_head_ref == search.expected_head_ref
        )
        assert receipt.repository == search.repository
        return _ready_pull_request_snapshot(receipt.to_pull_request_target())

    def inspect_pull_request(self, target: PullRequestTarget) -> PullRequestSnapshot:
        assert target in tuple(receipt.to_pull_request_target() for receipt in self.publication_receipts)
        return _ready_pull_request_snapshot(target)

    def merge_pull_request(self, request: SourceControlMergeRequest) -> SourceControlMergeReceipt:
        snapshot = self.inspect_pull_request(request.target)
        assert request.expected_base_sha == snapshot.base_sha
        assert request.expected_rules_fingerprint == snapshot.rules_fingerprint
        assert request.expected_checks_fingerprint == snapshot.checks_fingerprint
        self.merge_requests.append(request)
        receipt = SourceControlMergeReceipt(
            status=SourceControlMergeStatus.LANDED,
            repository_id=request.target.repository.repository_id,
            pull_number=request.target.pull_number,
            head_sha=request.target.expected_head_sha,
            merge_commit_sha=_stable_sha(f"merge:{request.idempotency_key}"),
            merged_at="2026-08-09T12:00:00Z",
            provider_request_id=f"github-merge-{request.target.pull_number}",
            idempotency_key=request.idempotency_key,
            evidence={"mergeMethod": request.merge_method.value, "exactHead": True},
        )
        self._merge_by_pull[request.target.pull_number] = receipt
        return receipt

    def lookup_merge(self, target: PullRequestTarget) -> SourceControlMergeReceipt:
        receipt = self._merge_by_pull.get(target.pull_number)
        if receipt is not None:
            return receipt
        return SourceControlMergeReceipt(
            status=SourceControlMergeStatus.ABSENT,
            repository_id=target.repository.repository_id,
            pull_number=target.pull_number,
            head_sha=target.expected_head_sha,
            evidence={"reason": "not_merged"},
        )

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())


def _source_publication_receipt(
    request: SourceCandidatePublicationRequest,
    *,
    pull_number: int,
) -> SourceCandidatePublicationReceipt:
    head_sha = _stable_sha(f"head:{request.proposal_id}:{request.manifest.manifest_fingerprint}")
    binding = SourceCandidateCommitBinding(
        expected_base_sha=request.expected_base_sha,
        expected_tree_sha=_stable_sha(f"tree:{request.manifest.manifest_fingerprint}"),
        expected_head_ref=request.expected_head_ref,
        manifest=request.manifest,
    )
    return SourceCandidatePublicationReceipt(
        status=SourceCandidatePublicationStatus.PUBLISHED,
        repository=request.repository,
        expected_base_ref=request.expected_base_ref,
        expected_head_ref=request.expected_head_ref,
        expected_base_sha=request.expected_base_sha,
        manifest_artifact_path=request.manifest.artifact_path,
        manifest_fingerprint=request.manifest.manifest_fingerprint,
        idempotency_key=request.idempotency_key,
        head_sha=head_sha,
        pull_number=pull_number,
        commit_binding=binding,
        provider_request_id=f"github-publish-{pull_number}",
        evidence={"readBackVerified": True, "manifestOnlyCommit": True},
    )


def _ready_pull_request_snapshot(target: PullRequestTarget) -> PullRequestSnapshot:
    approval = PullRequestReviewEvidence(
        reviewer_id=202,
        reviewer_login="independent-source-reviewer",
        state="APPROVED",
        commit_sha=target.expected_head_sha,
        submitted_at="2026-08-09T11:55:00Z",
    )
    rule = BranchRuleEvidence(
        rule_type="required_status_checks",
        ruleset_id=42,
        source_type="Repository",
        source="foundry-lite/governed-release-e2e",
        parameters_fingerprint=_fingerprint("branch-rule"),
    )
    check = RequiredCheckEvidence(
        context="quality-governed-release",
        commit_sha=target.expected_head_sha,
        status="completed",
        conclusion="success",
        source="check_run",
        source_app_id=1,
        is_successful=True,
    )
    binding = target.candidate_binding
    assert binding is not None
    return PullRequestSnapshot(
        target=target,
        state="open",
        is_draft=False,
        is_merged=False,
        base_sha=binding.expected_base_sha,
        head_ref=binding.expected_head_ref,
        author_id=101,
        author_login="proposal-author",
        mergeable_state="clean",
        test_merge_commit_sha=None,
        checks_commit_sha=target.expected_head_sha,
        review_decision=SourceControlReviewDecision.APPROVED,
        required_approval_count=1,
        approvals=(approval,),
        active_rules=(rule,),
        required_checks=(check,),
        is_merge_queue_required=False,
        rules_fingerprint=_fingerprint("rules"),
        checks_fingerprint=_fingerprint("checks"),
        blocking_reasons=(),
        is_ready_to_merge=True,
        provider_request_id=f"github-inspect-{target.pull_number}",
    )


def _seed_ontology(foundry: FoundryLite, tmp_path: Path) -> None:
    source = tmp_path / "governed_release_orders.csv"
    source.write_text("order_id,status\nO-1,PENDING\n", encoding="utf-8")
    foundry.datasets.ensure("clean.gpt_release_orders", ctx=_SUBMITTER, primary_key=["order_id"])
    foundry.datasets.upload_csv("clean.gpt_release_orders", source, ctx=_SUBMITTER)
    foundry.ontology.apply_text(_ontology_yaml(is_status_deprecated=False), ctx=_SUBMITTER)


def _ontology_yaml(*, is_status_deprecated: bool) -> str:
    properties: list[dict[str, object]] = [
        {"apiName": "orderId", "column": "order_id", "type": "string", "nullable": False},
        {
            "apiName": "status",
            "column": "status",
            "type": "string",
            "deprecated": is_status_deprecated,
        },
    ]
    definition = {
        "objectTypes": [
            {
                "apiName": "GptReleaseOrder",
                "primaryKey": "orderId",
                "backing": {"dataset": "clean.gpt_release_orders"},
                "properties": properties,
            }
        ]
    }
    return yaml.safe_dump(definition, sort_keys=False)


def _ontology_object_definition(*, is_status_deprecated: bool) -> dict[str, object]:
    document = _mapping(yaml.safe_load(_ontology_yaml(is_status_deprecated=is_status_deprecated)))
    object_types = document.get("objectTypes")
    assert isinstance(object_types, list) and object_types
    return dict(_mapping(object_types[0]))


def _seed_pipeline_dataset(foundry: FoundryLite, tmp_path: Path) -> None:
    source = tmp_path / "governed_release_pipeline.csv"
    source.write_text("order_id,amount\nO-1,10\nO-2,20\n", encoding="utf-8")
    foundry.datasets.ensure("raw.gpt_release_orders", ctx=_SUBMITTER)
    foundry.datasets.upload_csv("raw.gpt_release_orders", source, ctx=_SUBMITTER)


def _seed_promoted_pipeline(foundry: FoundryLite) -> Mapping[str, object]:
    branch = foundry.pipelines.create_branch(
        pipeline_id="gpt_release_pipeline",
        name="seed",
        idempotency_key="gpt-pipeline-seed-branch",
        ctx=_SUBMITTER,
    )
    updated = foundry.pipelines.update_graph(
        str(branch["id"]),
        graph=_pipeline_graph("clean.gpt_release_v1"),
        expected_fingerprint=str(branch["graphFingerprint"]),
        ctx=_SUBMITTER,
    )
    foundry.pipelines.run_tests(str(updated["id"]), ctx=_SUBMITTER)
    proposal = foundry.pipelines.propose(
        str(updated["id"]),
        title="Seed promoted version",
        idempotency_key="gpt-pipeline-seed-proposal",
        ctx=_SUBMITTER,
    )
    seed_reviewer = RequestContext(
        tenant_id=_SUBMITTER.tenant_id,
        actor_user_id="seed-release-reviewer",
        roles=_SUBMITTER.roles,
    )
    foundry.pipelines.assign(
        str(proposal["id"]),
        assignee_user_id=seed_reviewer.actor_user_id,
        ctx=_SUBMITTER,
    )
    foundry.pipelines.approve(str(proposal["id"]), ctx=seed_reviewer)
    version = foundry.pipelines.execute(str(proposal["id"]), ctx=_SUBMITTER)
    foundry.pipelines.deploy(
        "gpt_release_pipeline",
        str(version["id"]),
        idempotency_key="gpt-pipeline-seed-deploy",
        ctx=_SUBMITTER,
    )
    return version


def _pipeline_graph(output_ref: str) -> dict[str, object]:
    columns = [_column("order_id", "string"), _column("amount", "int")]
    return {
        "nodes": [
            {
                "id": "raw_orders",
                "type": "dataset",
                "config": {"datasetRef": "raw.gpt_release_orders", "schema": columns},
            },
            {
                "id": "clean_sql",
                "type": "sql",
                "config": {
                    "sql": "select order_id, amount from {{ input('raw.gpt_release_orders') }}",
                    "outputDatasetRef": "work.gpt_release_orders",
                    "schema": columns,
                },
            },
            {
                "id": "out",
                "type": "output_dataset",
                "config": {"outputDatasetRef": output_ref},
            },
        ],
        "edges": [
            {"source": "raw_orders", "target": "clean_sql"},
            {"source": "clean_sql", "target": "out"},
        ],
        "layout": {},
        "outputContract": {"columns": columns},
        "tests": [{"name": "schema contract", "expected": {"columns": columns}}],
        "schedule": {"kind": "manual"},
    }


def _column(name: str, column_type: str) -> dict[str, object]:
    return {"name": name, "type": column_type, "nullable": False}


def _release_client(
    foundry: FoundryLite,
    monkeypatch: MonkeyPatch,
    suffix: str,
    *,
    actor: RequestContext = _REVIEWER,
) -> tuple[TestClient, str, dict[str, str]]:
    client_id = f"gpt-release-{suffix}-client"
    redirect_uri = f"https://chat.example.test/gpt-release/{suffix}"
    api_suffix = "".join(part.title() for part in suffix.split("-"))
    created = foundry.developer_console.create_osdk_application(
        app_api_name=f"GptRelease{api_suffix}",
        display_name=f"GPT governed release {suffix}",
        resources=[
            {
                "resourceType": "connector",
                "resourceApiName": "governed_release",
                "scopes": [_RELEASE_SCOPE],
            }
        ],
        idempotency_key=f"gpt-release-{suffix}-app",
        ctx=actor,
    )
    app_id = str(created["application"]["id"])
    foundry.developer_console.create_osdk_application_client(
        app_id,
        client_id=client_id,
        redirect_uris=(redirect_uri,),
        allowed_scopes=(_RELEASE_SCOPE,),
        idempotency_key=f"gpt-release-{suffix}-client",
        ctx=actor,
    )
    verifier = f"gpt-release-{suffix}-verifier"
    endpoint = f"/mcp/release/{app_id}"
    resource = f"http://testserver{endpoint}"
    authorization = foundry.auth.osdk_oauth_authorize(
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=_s256(verifier),
        scopes=(_RELEASE_SCOPE,),
        resource=resource,
        resource_application_id=app_id,
        ctx=actor,
    )
    token = foundry.auth.osdk_oauth_token(
        client_id=client_id,
        code=str(authorization["code"]),
        redirect_uri=redirect_uri,
        code_verifier=verifier,
        resource=resource,
        resource_application_id=app_id,
        ctx=actor,
    )
    issuer = foundry._services.osdk_oauth_sessions.oauth_token_issuer
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer=issuer.issuer,
            audience=issuer.audience,
            jwks=issuer.public_jwks(),
            grant_type_claim="gty",
            grant_type_value="authorization_code",
        )
    )
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    monkeypatch.setattr(api_runtime, "get_auth_provider", lambda: provider)
    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {token['accessToken']}",
        "MCP-Protocol-Version": "2025-06-18",
        "X-Request-ID": f"gpt-release-{suffix}",
    }
    initialized = client.post(
        endpoint,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": f"initialize-{suffix}",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "gpt-release-e2e", "version": "1.0.0"},
            },
        },
    )
    assert initialized.status_code == 200, initialized.text
    assert "error" not in initialized.json(), initialized.text
    return client, endpoint, {**headers, "Mcp-Session-Id": initialized.headers["Mcp-Session-Id"]}


def _builder_client(
    foundry: FoundryLite,
    monkeypatch: MonkeyPatch,
    mode: str,
    suffix: str,
    actor: RequestContext,
) -> tuple[TestClient, str, dict[str, str]]:
    scope = f"osdk:connector:fde_{mode}:execute"
    client_id = f"gpt-builder-{suffix}-{mode}-client"
    redirect_uri = f"https://chat.example.test/gpt-builder/{suffix}/{mode}"
    api_suffix = "".join(part.title() for part in f"{suffix}-{mode}".replace("_", "-").split("-"))
    created = foundry.developer_console.create_osdk_application(
        app_api_name=f"GptBuilder{api_suffix}",
        display_name=f"GPT Builder {suffix} {mode}",
        resources=[
            {
                "resourceType": "connector",
                "resourceApiName": f"fde_{mode}",
                "scopes": [scope],
            }
        ],
        idempotency_key=f"gpt-builder-{suffix}-{mode}-app",
        ctx=actor,
    )
    app_id = str(created["application"]["id"])
    foundry.developer_console.create_osdk_application_client(
        app_id,
        client_id=client_id,
        redirect_uris=(redirect_uri,),
        allowed_scopes=(scope,),
        idempotency_key=f"gpt-builder-{suffix}-{mode}-client",
        ctx=actor,
    )
    verifier = f"gpt-builder-{suffix}-{mode}-verifier"
    endpoint = f"/mcp/builder/{app_id}"
    resource = f"http://testserver{endpoint}"
    authorization = foundry.auth.osdk_oauth_authorize(
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=_s256(verifier),
        scopes=(scope,),
        resource=resource,
        resource_application_id=app_id,
        ctx=actor,
    )
    token = foundry.auth.osdk_oauth_token(
        client_id=client_id,
        code=str(authorization["code"]),
        redirect_uri=redirect_uri,
        code_verifier=verifier,
        resource=resource,
        resource_application_id=app_id,
        ctx=actor,
    )
    issuer = foundry._services.osdk_oauth_sessions.oauth_token_issuer
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer=issuer.issuer,
            audience=issuer.audience,
            jwks=issuer.public_jwks(),
            grant_type_claim="gty",
            grant_type_value="authorization_code",
        )
    )
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    monkeypatch.setattr(api_runtime, "get_auth_provider", lambda: provider)
    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {token['accessToken']}",
        "MCP-Protocol-Version": "2025-06-18",
        "X-Request-ID": f"gpt-builder-{suffix}-{mode}",
    }
    initialized = client.post(
        endpoint,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": f"initialize-{suffix}-{mode}",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "gpt-builder-e2e", "version": "1.0.0"},
            },
        },
    )
    assert initialized.status_code == 200, initialized.text
    assert "error" not in initialized.json(), initialized.text
    session_headers = {**headers, "Mcp-Session-Id": initialized.headers["Mcp-Session-Id"]}
    return client, app_id, session_headers


def _builder_tool(
    client: TestClient,
    app_id: str,
    headers: Mapping[str, str],
    mode: str,
    workspace_ref: str,
    name: str,
    arguments: Mapping[str, object],
) -> dict[str, object]:
    outer_arguments: dict[str, object] = {
        "mode": mode,
        "workspaceRef": workspace_ref,
        "arguments": dict(arguments),
    }
    payload = {
        "jsonrpc": "2.0",
        "id": f"builder-{name}-{arguments.get('idempotencyKey', 'call')}",
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": outer_arguments,
        },
    }
    response = client.post(f"/mcp/builder/{app_id}", headers=dict(headers), json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "error" not in body, body
    result = _mapping(body["result"])
    structured = _mapping(result["structuredContent"])
    if structured.get("status") == "approval_required":
        challenge_id = str(structured["challengeId"])
        widget_token = str(_mapping(result["_meta"])["widgetApprovalToken"])
        assert widget_token
        assert widget_token not in json.dumps(result["structuredContent"], sort_keys=True)
        assert widget_token not in json.dumps(result["content"], sort_keys=True)
        approval_result = _builder_mcp_call(
            client,
            app_id,
            headers,
            "approve_builder_mutation",
            {"challengeId": challenge_id, "widgetApprovalToken": widget_token},
            rpc_id=f"builder-widget-approval-{challenge_id}",
        )
        assert approval_result.get("isError") is not True, approval_result
        receipt = str(_mapping(approval_result["_meta"])["confirmationReceipt"])
        assert receipt
        assert receipt not in json.dumps(approval_result["structuredContent"], sort_keys=True)
        assert receipt not in json.dumps(approval_result["content"], sort_keys=True)
        assert widget_token not in json.dumps(approval_result, sort_keys=True)
        outer_arguments["confirmationReceipt"] = receipt
        response = client.post(f"/mcp/builder/{app_id}", headers=dict(headers), json=payload)
        assert response.status_code == 200, response.text
        body = response.json()
        assert "error" not in body, body
        assert receipt not in json.dumps(body, sort_keys=True)
    result = _mapping(body["result"])
    assert result.get("isError") is not True, result
    return dict(_mapping(result["structuredContent"]))


def _builder_mcp_call(
    client: TestClient,
    app_id: str,
    headers: Mapping[str, str],
    name: str,
    arguments: Mapping[str, object],
    *,
    rpc_id: str,
) -> dict[str, object]:
    response = client.post(
        f"/mcp/builder/{app_id}",
        headers=dict(headers),
        json={
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": dict(arguments)},
        },
    )
    assert response.status_code == 200, response.text
    envelope = response.json()
    assert "error" not in envelope, envelope
    return dict(_mapping(envelope["result"]))


def _read_tool(
    client: TestClient,
    endpoint: str,
    headers: Mapping[str, str],
    name: str,
    arguments: Mapping[str, object],
) -> dict[str, object]:
    result = _call_tool(client, endpoint, headers, name, arguments)
    assert result["isError"] is False
    return _release_payload(result)


def _confirmed_tool(
    client: TestClient,
    endpoint: str,
    headers: Mapping[str, str],
    name: str,
    arguments: Mapping[str, object],
) -> dict[str, object]:
    prepared = _call_tool(
        client,
        endpoint,
        headers,
        "prepare_release_action",
        {"targetTool": name, "arguments": dict(arguments)},
    )
    token = _mapping(prepared["_meta"])["widgetConfirmationToken"]
    assert isinstance(token, str) and token
    assert token not in json.dumps(prepared["structuredContent"], sort_keys=True)
    result = _call_tool(
        client,
        endpoint,
        headers,
        name,
        {**arguments, "widgetConfirmationToken": token},
    )
    assert result["isError"] is False, result
    assert token not in json.dumps(result, sort_keys=True)
    return _release_payload(result)


def _call_tool(
    client: TestClient,
    endpoint: str,
    headers: Mapping[str, str],
    name: str,
    arguments: Mapping[str, object],
) -> dict[str, object]:
    response = client.post(
        endpoint,
        headers=dict(headers),
        json={
            "jsonrpc": "2.0",
            "id": f"{name}-{arguments.get('idempotencyKey', arguments.get('proposalId', 'read'))}",
            "method": "tools/call",
            "params": {"name": name, "arguments": dict(arguments)},
        },
    )
    assert response.status_code == 200, response.text
    envelope = response.json()
    assert "error" not in envelope, envelope
    return dict(_mapping(envelope["result"]))


def _release_payload(result: Mapping[str, object]) -> dict[str, object]:
    structured = _mapping(result["structuredContent"])
    return dict(_mapping(structured["release"]))


def _mapping(value: object) -> Mapping[str, object]:
    assert isinstance(value, Mapping)
    return value


def _source_merge_binding(release_evidence: Mapping[str, object]) -> dict[str, object]:
    source = _mapping(release_evidence["externalSourceControl"])
    target = _mapping(source["target"])
    candidate = _mapping(source["candidate"])
    return {
        "expectedSourceBaseSha": candidate["baseSha"],
        "expectedSourceHeadSha": target["headSha"],
        "expectedSourceChecksFingerprint": candidate["checksFingerprint"],
        "expectedSourceRulesFingerprint": candidate["rulesFingerprint"],
    }


def _sha(character: str) -> str:
    return character * 40


def _stable_sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:40]


def _fingerprint(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
