from __future__ import annotations

import hashlib
from base64 import b64encode
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.governed_release_delivery_config import GovernedReleaseDeliveryConfig
from foundry_lite.application.ports.infrastructure_deployment_adapter import (
    InfrastructureDeploymentAdapter,
    InfrastructureDeploymentCandidateQuery,
    InfrastructureDeploymentGetRequest,
    InfrastructureDeploymentMutationResult,
    InfrastructureDeploymentObservation,
    InfrastructureDeploymentRollbackRequest,
    InfrastructureDeploymentServicePolicyObservation,
    InfrastructureDeploymentServicePolicyRequest,
    InfrastructureDeploymentSourceBinding,
    InfrastructureDeploymentStartRequest,
    InfrastructureDeploymentStatus,
)
from foundry_lite.application.ports.release_delivery_repository import (
    ReleaseDeliveryKind,
    ReleaseDeliveryOperation,
    ReleaseDeliveryRecord,
)
from foundry_lite.application.ports.source_control_candidate import (
    PullRequestTarget,
    SourceCandidatePublicationReceipt,
    SourceCandidatePublicationRequest,
    SourceRefSnapshot,
    SourceRepositoryRef,
)
from foundry_lite.application.ports.source_control_release import (
    PullRequestSearch,
    PullRequestSnapshot,
    SourceControlMergeReceipt,
    SourceControlMergeRequest,
    SourceControlMergeStatus,
    SourceControlReleasePort,
)
from foundry_lite.application.services.aip.external_release_delivery_payloads import (
    deployment_observation_ref,
)
from foundry_lite.application.services.aip.governed_release_live_collection_contract import (
    DeliveryOperation,
    ReleaseKind,
)
from foundry_lite.application.services.aip.governed_release_live_provider_collector import (
    GovernedReleaseLiveProviderCollector,
    LiveProviderObservation,
    LiveProviderSnapshot,
    pair_provider_snapshots,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected

_REPOSITORY = SourceRepositoryRef("github", 42, "acme", "foundry-lite")
_BASE_SHA = "a" * 40
_BASE_TREE_SHA = "b" * 40
_ONTOLOGY_HEAD_SHA = "c" * 40
_ONTOLOGY_TREE_SHA = "d" * 40
_ONTOLOGY_MERGE_SHA = "e" * 40
_PIPELINE_HEAD_SHA = "1" * 40
_PIPELINE_TREE_SHA = "2" * 40
_PIPELINE_MERGE_SHA = "3" * 40
_PRIOR_COMMIT_SHA = "4" * 40
_TIMESTAMP = datetime(2026, 8, 9, 1, 0, tzinfo=UTC)


@dataclass
class _Harness:
    collector: GovernedReleaseLiveProviderCollector
    source: _SourceControl
    infrastructure: _Infrastructure
    records: tuple[ReleaseDeliveryRecord, ...]
    context: RequestContext


class _SourceControl(SourceControlReleasePort):
    def __init__(self) -> None:
        self._profile_name = "github-release"
        self.request_count = 0
        self.repository_id = _REPOSITORY.repository_id
        self.head_sha_by_pull = {17: _ONTOLOGY_HEAD_SHA, 18: _PIPELINE_HEAD_SHA}
        self.merge_sha_by_pull = {17: _ONTOLOGY_MERGE_SHA, 18: _PIPELINE_MERGE_SHA}
        self.is_request_id_present = True

    @property
    def profile_name(self) -> str:
        return self._profile_name

    @property
    def provider_name(self) -> str:
        return "github"

    @property
    def is_live_provider(self) -> bool:
        return self._profile_name == "github-release"

    def set_profile(self, value: str) -> None:
        self._profile_name = value

    def inspect_source_ref(self, repository: SourceRepositoryRef, ref: str) -> SourceRefSnapshot:
        assert repository == _REPOSITORY
        assert ref == "main"
        return SourceRefSnapshot(repository, ref, _BASE_SHA, _BASE_TREE_SHA)

    def lookup_merge(self, target: PullRequestTarget) -> SourceControlMergeReceipt:
        self.request_count += 1
        request_id = f"github-read-{self.request_count}" if self.is_request_id_present else None
        return SourceControlMergeReceipt(
            status=SourceControlMergeStatus.LANDED,
            repository_id=self.repository_id,
            pull_number=target.pull_number,
            head_sha=self.head_sha_by_pull[target.pull_number],
            merge_commit_sha=self.merge_sha_by_pull[target.pull_number],
            merged_at="2026-08-09T00:30:00Z",
            provider_request_id=request_id,
            evidence={"manifestReadBack": True},
        )

    def publish_pull_request_candidate(
        self,
        request: SourceCandidatePublicationRequest,
    ) -> SourceCandidatePublicationReceipt:
        del request
        raise AssertionError("collector is read-only")

    def lookup_pull_request_candidate(
        self,
        request: SourceCandidatePublicationRequest,
    ) -> SourceCandidatePublicationReceipt:
        del request
        raise AssertionError("collector uses the sealed pull request target")

    def find_pull_request(self, search: PullRequestSearch) -> PullRequestSnapshot:
        del search
        raise AssertionError("collector uses exact pull request identity")

    def inspect_pull_request(self, target: PullRequestTarget) -> PullRequestSnapshot:
        del target
        raise AssertionError("lookup_merge performs the exact provider readback")

    def merge_pull_request(self, request: SourceControlMergeRequest) -> SourceControlMergeReceipt:
        del request
        raise AssertionError("collector cannot mutate GitHub")

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())


class _Infrastructure(InfrastructureDeploymentAdapter):
    def __init__(self, observations: dict[str, InfrastructureDeploymentObservation]) -> None:
        self._profile_name = "render-infrastructure-deployment"
        self.observations = observations
        self.request_count = 0
        self.policy_request_count = 0
        self.is_request_id_present = True
        self.is_policy_request_id_present = True
        self.is_auto_deploy_enabled = False
        self.source_repository_owner = _REPOSITORY.owner
        self.observation_commit_override: str | None = None

    @property
    def profile_name(self) -> str:
        return self._profile_name

    @property
    def provider_name(self) -> str:
        return "render"

    @property
    def is_live_provider(self) -> bool:
        return self._profile_name == "render-infrastructure-deployment"

    def set_profile(self, value: str) -> None:
        self._profile_name = value

    def get(self, request: InfrastructureDeploymentGetRequest) -> InfrastructureDeploymentObservation:
        self.request_count += 1
        observation = self.observations[request.deploy_id]
        request_id = f"render-read-{self.request_count}" if self.is_request_id_present else None
        return replace(
            observation,
            commit_id=self.observation_commit_override or observation.commit_id,
            provider_request_id=request_id,
        )

    def get_service_policy(
        self,
        request: InfrastructureDeploymentServicePolicyRequest,
    ) -> InfrastructureDeploymentServicePolicyObservation:
        self.policy_request_count += 1
        request_id = f"render-policy-{self.policy_request_count}" if self.is_policy_request_id_present else None
        return InfrastructureDeploymentServicePolicyObservation(
            provider="render",
            service_id=request.service_id,
            release_mode="source_revision",
            trigger_mode="automatic" if self.is_auto_deploy_enabled else "manual",
            source_binding=InfrastructureDeploymentSourceBinding(
                "github",
                self.source_repository_owner,
                _REPOSITORY.name,
                "main",
            ),
            workload_kind="web_service",
            is_suspended=False,
            provider_request_id=request_id,
        )

    def start(self, request: InfrastructureDeploymentStartRequest) -> InfrastructureDeploymentMutationResult:
        del request
        raise AssertionError("collector cannot deploy")

    def list_candidates(
        self,
        query: InfrastructureDeploymentCandidateQuery,
    ) -> tuple[InfrastructureDeploymentObservation, ...]:
        del query
        raise AssertionError("collector reads exact provider resource ids")

    def rollback(self, request: InfrastructureDeploymentRollbackRequest) -> InfrastructureDeploymentMutationResult:
        del request
        raise AssertionError("collector cannot roll back")

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())


def _harness() -> _Harness:
    observations = {
        "dep-current": _deployment_observation(
            "dep-current",
            _PIPELINE_MERGE_SHA,
            "api",
            status="deactivated",
        ),
        "dep-rollback": _deployment_observation("dep-rollback", _PRIOR_COMMIT_SHA, "rollback"),
    }
    source = _SourceControl()
    infrastructure = _Infrastructure(observations)
    config = GovernedReleaseDeliveryConfig(
        source_repository=_REPOSITORY,
        source_base_ref="main",
        is_source_control_required=True,
        deployment_service_id="srv-foundry-lite",
        is_deployment_required=True,
    )
    return _Harness(
        GovernedReleaseLiveProviderCollector(config, source, infrastructure),
        source,
        infrastructure,
        _records(observations),
        RequestContext(tenant_id="tenant-1", actor_user_id="collector", request_id="request-1"),
    )


def _records(
    observations: dict[str, InfrastructureDeploymentObservation],
) -> tuple[ReleaseDeliveryRecord, ...]:
    ontology_publish = _publication("ontology", 17, _ONTOLOGY_HEAD_SHA, _ONTOLOGY_TREE_SHA)
    ontology_merge = _source_merge(ontology_publish, _ONTOLOGY_MERGE_SHA)
    pipeline_publish = _publication("pipeline", 18, _PIPELINE_HEAD_SHA, _PIPELINE_TREE_SHA)
    pipeline_merge = _source_merge(pipeline_publish, _PIPELINE_MERGE_SHA)
    deploy = _application_delivery(
        pipeline_merge,
        "application_deploy",
        "dep-current",
        _PIPELINE_MERGE_SHA,
        _deployment_observation("dep-current", _PIPELINE_MERGE_SHA, "api"),
    )
    rollback = _application_delivery(
        deploy,
        "application_rollback",
        "dep-rollback",
        _PRIOR_COMMIT_SHA,
        observations["dep-rollback"],
    )
    return ontology_publish, ontology_merge, pipeline_publish, pipeline_merge, deploy, rollback


def _publication(
    release_kind: ReleaseDeliveryKind,
    pull_number: int,
    head_sha: str,
    tree_sha: str,
) -> ReleaseDeliveryRecord:
    proposal_id = f"{release_kind}-proposal"
    delivery_id = f"delivery-{release_kind}-source-publish"
    canonical_bytes = f'{{"releaseKind":"{release_kind}"}}\n'.encode()
    manifest_fingerprint = _byte_digest(canonical_bytes)
    artifact_path = f".foundry-lite/releases/{release_kind}/{proposal_id}.json"
    target = {
        "repositoryId": _REPOSITORY.repository_id,
        "repositoryOwner": _REPOSITORY.owner,
        "repositoryName": _REPOSITORY.name,
        "baseRef": "main",
        "headRef": f"codex/{proposal_id}",
        "baseSha": _BASE_SHA,
    }
    candidate: dict[str, object] = {
        "releaseKind": release_kind,
        "proposalId": proposal_id,
        "artifactPath": artifact_path,
        "manifestFingerprint": manifest_fingerprint,
        "manifestCanonicalBytesBase64": b64encode(canonical_bytes).decode("ascii"),
    }
    result: dict[str, object] = {
        "status": "published",
        **target,
        "manifestFingerprint": manifest_fingerprint,
        "headSha": head_sha,
        "treeSha": tree_sha,
        "pullNumber": pull_number,
    }
    return _record(
        release_kind,
        "source_publish",
        delivery_id,
        delivery_id,
        None,
        "github",
        f"pull:{pull_number}",
        target,
        candidate,
        result,
    )


def _source_merge(publication: ReleaseDeliveryRecord, merge_sha: str) -> ReleaseDeliveryRecord:
    delivery_id = f"delivery-{publication.release_kind}-source-merge"
    return _record(
        publication.release_kind,
        "source_merge",
        delivery_id,
        publication.workflow_run_id,
        publication.delivery_id,
        "github",
        publication.provider_resource_id,
        dict(publication.target_ref),
        dict(publication.candidate_ref or {}),
        {"mergeCommitSha": merge_sha},
    )


def _application_delivery(
    parent: ReleaseDeliveryRecord,
    operation: ReleaseDeliveryOperation,
    deploy_id: str,
    commit_id: str,
    observation: InfrastructureDeploymentObservation,
) -> ReleaseDeliveryRecord:
    candidate: dict[str, object] = (
        {"commitId": commit_id}
        if operation == "application_deploy"
        else {"targetCommitId": commit_id, "targetDeployId": "dep-prior"}
    )
    return _record(
        "pipeline",
        operation,
        f"delivery-pipeline-{operation}",
        parent.workflow_run_id,
        parent.delivery_id,
        "render",
        deploy_id,
        {"serviceId": "srv-foundry-lite"},
        candidate,
        deployment_observation_ref(observation),
        prior_resource_id="dep-current" if operation == "application_rollback" else "dep-prior",
    )


def _record(
    release_kind: ReleaseDeliveryKind,
    operation: ReleaseDeliveryOperation,
    delivery_id: str,
    workflow_run_id: str,
    parent_delivery_id: str | None,
    provider: str,
    provider_resource_id: str | None,
    target_ref: dict[str, object],
    candidate_ref: dict[str, object],
    result_ref: dict[str, object],
    *,
    prior_resource_id: str | None = None,
) -> ReleaseDeliveryRecord:
    return ReleaseDeliveryRecord(
        delivery_id=delivery_id,
        tenant_id="tenant-1",
        application_id="release-app",
        proposal_id=f"{release_kind}-proposal",
        release_kind=release_kind,
        workflow_run_id=workflow_run_id,
        parent_delivery_id=parent_delivery_id,
        provider=provider,
        operation=operation,
        status="landed",
        target_ref=target_ref,
        candidate_ref=candidate_ref,
        environment="main" if provider == "github" else "production",
        idempotency_key=f"idempotency-{delivery_id}",
        request_fingerprint=_digest(f"request:{delivery_id}"),
        provider_operation_id=f"operation:{delivery_id}",
        provider_resource_id=provider_resource_id,
        prior_resource_id=prior_resource_id,
        result_ref=result_ref,
        error_ref=None,
        ai_run_id=workflow_run_id if operation == "source_publish" else f"ai-run:{delivery_id}",
        binding_hash=_digest(f"binding:{delivery_id}"),
        execution_attempt=1,
        request_id=f"request:{delivery_id}",
        created_by="reviewer",
        created_at="2026-08-09T00:00:00Z",
        updated_at="2026-08-09T00:01:00Z",
        dispatch_started_at="2026-08-09T00:00:30Z",
        completed_at="2026-08-09T00:01:00Z",
    )


def _deployment_observation(
    deploy_id: str,
    commit_id: str,
    trigger: str,
    *,
    status: InfrastructureDeploymentStatus = "live",
) -> InfrastructureDeploymentObservation:
    return InfrastructureDeploymentObservation(
        provider="render",
        service_id="srv-foundry-lite",
        deploy_id=deploy_id,
        status=status,
        provider_status=status,
        commit_id=commit_id,
        trigger=trigger,
        created_at=_TIMESTAMP,
        started_at=_TIMESTAMP,
        updated_at=_TIMESTAMP,
        finished_at=_TIMESTAMP,
        is_terminal=True,
        is_successful=status == "live",
        provider_request_id=f"original:{deploy_id}",
    )


def _digest(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def _byte_digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _observation(
    snapshot: LiveProviderSnapshot,
    operation: DeliveryOperation,
    kind: ReleaseKind = "pipeline",
) -> LiveProviderObservation:
    return next(item for item in snapshot.observations if item.release_kind == kind and item.operation == operation)


def test_stable_two_pass_requires_historical_deploy_deactivated_and_rollback_live() -> None:
    harness = _harness()

    initial = harness.collector.read_once(harness.context, harness.records, "collection-1")
    final = harness.collector.read_once(harness.context, harness.records, "collection-1")
    paired = pair_provider_snapshots(initial, final)

    assert len(paired) == 6
    assert all(item.is_exact_target and item.is_terminal_success for item in paired)
    assert all(item.initial_evidence_fingerprint == item.final_evidence_fingerprint for item in paired)
    assert all(len(set(item.provider_request_ids)) == 2 for item in paired)
    assert initial.target_configuration.fingerprint == final.target_configuration.fingerprint
    assert initial.target_configuration.provider_request_id != final.target_configuration.provider_request_id
    assert _observation(final, "application_deploy").evidence["status"] == "deactivated"
    assert _observation(final, "application_rollback").evidence["status"] == "live"


def test_third_live_deploy_deactivates_rollback_and_blocks_completion() -> None:
    harness = _harness()
    harness.infrastructure.observations["dep-rollback"] = _deployment_observation(
        "dep-rollback",
        _PRIOR_COMMIT_SHA,
        "rollback",
        status="deactivated",
    )
    harness.infrastructure.observations["dep-third"] = _deployment_observation(
        "dep-third",
        "f" * 40,
        "api",
    )

    initial = harness.collector.read_once(harness.context, harness.records, "collection-1")
    final = harness.collector.read_once(harness.context, harness.records, "collection-1")
    paired = pair_provider_snapshots(initial, final)

    assert _observation(final, "application_deploy").is_terminal_success is True
    assert _observation(final, "application_rollback").is_terminal_success is False
    rollback = next(item for item in paired if item.operation == "application_rollback")
    assert rollback.is_exact_target is False
    assert rollback.is_terminal_success is False


def test_original_deploy_still_live_after_claimed_rollback_blocks_completion() -> None:
    harness = _harness()
    harness.infrastructure.observations["dep-current"] = _deployment_observation(
        "dep-current",
        _PIPELINE_MERGE_SHA,
        "api",
    )

    snapshot = harness.collector.read_once(harness.context, harness.records, "collection-1")

    original = _observation(snapshot, "application_deploy")
    rollback = _observation(snapshot, "application_rollback")
    assert original.is_exact_target is False
    assert original.is_terminal_success is False
    assert rollback.is_exact_target is True


def test_source_publication_does_not_require_a_merge_sha_in_its_ledger_result() -> None:
    harness = _harness()
    publication = harness.records[0]
    assert "mergeCommitSha" not in (publication.result_ref or {})

    snapshot = harness.collector.read_once(harness.context, harness.records, "collection-1")

    observed = _observation(snapshot, "source_publish", "ontology")
    assert observed.is_exact_target is True


@pytest.mark.parametrize("replacement", [{}, {"mergeCommitSha": "f" * 40}])
def test_source_merge_requires_the_exact_landed_merge_sha(replacement: dict[str, object]) -> None:
    harness = _harness()
    changed = replace(harness.records[1], result_ref=replacement)
    records = (harness.records[0], changed, *harness.records[2:])

    snapshot = harness.collector.read_once(harness.context, records, "collection-1")

    observed = _observation(snapshot, "source_merge", "ontology")
    assert observed.is_exact_target is False


@pytest.mark.parametrize(
    ("source_profile", "infrastructure_profile"),
    [("fake-github", "render-infrastructure-deployment"), ("github-release", "fake-render")],
)
def test_collector_requires_exact_concrete_adapter_profiles(
    source_profile: str,
    infrastructure_profile: str,
) -> None:
    harness = _harness()
    harness.source.set_profile(source_profile)
    harness.infrastructure.set_profile(infrastructure_profile)

    with pytest.raises(ConflictDetected, match="concrete network provider adapters"):
        harness.collector.read_once(harness.context, harness.records, "collection-1")


@pytest.mark.parametrize("provider", ["github", "render", "render-policy"])
def test_every_provider_read_requires_a_nonempty_provider_request_id(provider: str) -> None:
    harness = _harness()
    if provider == "github":
        harness.source.is_request_id_present = False
    elif provider == "render":
        harness.infrastructure.is_request_id_present = False
    else:
        harness.infrastructure.is_policy_request_id_present = False

    with pytest.raises(ConflictDetected, match="provider .*request id"):
        harness.collector.read_once(harness.context, harness.records, "collection-1")


def test_github_identity_mismatch_is_collected_as_non_exact() -> None:
    harness = _harness()
    harness.source.repository_id = 84

    snapshot = harness.collector.read_once(harness.context, harness.records, "collection-1")

    github_rows = [item for item in snapshot.observations if item.provider == "github"]
    assert github_rows
    assert all(item.is_exact_target is False for item in github_rows)


def test_render_commit_mismatch_is_collected_as_non_exact() -> None:
    harness = _harness()
    harness.infrastructure.observation_commit_override = "f" * 40

    snapshot = harness.collector.read_once(harness.context, harness.records, "collection-1")

    render_rows = [item for item in snapshot.observations if item.provider == "render"]
    assert render_rows
    assert all(item.is_exact_target is False for item in render_rows)


def test_non_manual_or_wrong_target_policy_blocks_pairing() -> None:
    harness = _harness()
    harness.infrastructure.is_auto_deploy_enabled = True
    initial = harness.collector.read_once(harness.context, harness.records, "collection-1")
    final = harness.collector.read_once(harness.context, harness.records, "collection-1")
    assert initial.target_configuration.is_exact_target is False

    with pytest.raises(ConflictDetected, match="target policy"):
        pair_provider_snapshots(initial, final)


def test_changed_provider_resource_set_blocks_pairing() -> None:
    harness = _harness()
    initial = harness.collector.read_once(harness.context, harness.records, "collection-1")
    final = harness.collector.read_once(harness.context, harness.records, "collection-1")
    changed = replace(final, observations=final.observations[:-1])

    with pytest.raises(ConflictDetected, match="resources changed"):
        pair_provider_snapshots(initial, changed)


def test_changed_provider_resource_identity_blocks_pairing() -> None:
    harness = _harness()
    initial = harness.collector.read_once(harness.context, harness.records, "collection-1")
    final = harness.collector.read_once(harness.context, harness.records, "collection-1")
    changed_row = replace(final.observations[0], provider_resource_id="pull:999")
    changed = replace(final, observations=(changed_row, *final.observations[1:]))

    with pytest.raises(ConflictDetected, match="identity changed"):
        pair_provider_snapshots(initial, changed)
