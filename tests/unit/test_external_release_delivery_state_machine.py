from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailure
from foundry_lite.application.ports.governed_release_delivery_config import GovernedReleaseDeliveryConfig
from foundry_lite.application.ports.infrastructure_deployment_adapter import (
    InfrastructureDeploymentAdapter,
    InfrastructureDeploymentMutationResult,
    InfrastructureDeploymentObservation,
    InfrastructureDeploymentRollbackRequest,
    InfrastructureDeploymentServicePolicyObservation,
    InfrastructureDeploymentServicePolicyRequest,
    InfrastructureDeploymentSourceBinding,
    InfrastructureDeploymentStatus,
)
from foundry_lite.application.ports.release_delivery_repository import (
    ReleaseDeliveryRecord,
    ReleaseDeliveryStatus,
)
from foundry_lite.application.ports.source_control_candidate import SourceCandidateManifest
from foundry_lite.application.ports.source_control_release import (
    PullRequestTarget,
    SourceCandidateCommitBinding,
    SourceControlMergeMethod,
    SourceControlMergeReceipt,
    SourceControlMergeRequest,
    SourceControlMergeStatus,
    SourceControlReleasePort,
    SourceRepositoryRef,
)
from foundry_lite.application.services.aip.external_release_delivery_ledger import ExternalReleaseDeliveryLedger
from foundry_lite.application.services.aip.external_release_delivery_payloads import (
    delivery_projection,
    deployment_observation_ref,
)
from foundry_lite.application.services.aip.external_release_delivery_state import (
    application_delivery_summary,
)
from foundry_lite.application.services.aip.external_release_delivery_support import (
    ExternalReleaseDeliveryOutcomeUnknown,
)
from foundry_lite.application.services.aip.external_release_infrastructure_coordinator import (
    ExternalReleaseInfrastructureCoordinator,
)
from foundry_lite.application.services.aip.external_release_source_coordinator import (
    ExternalReleaseSourceCoordinator,
)
from foundry_lite.application.services.aip.governed_release_evidence import release_stage
from foundry_lite.application.services.aip.governed_release_external_boundary import (
    require_external_delivery_result,
)
from foundry_lite.application.services.aip.governed_release_status_evidence import (
    release_status_evidence,
)
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected


class _CandidateAdapter:
    profile_name = "render-infrastructure-deployment"
    provider_name = "render"
    is_live_provider = True

    def __init__(self, candidates: tuple[InfrastructureDeploymentObservation, ...]) -> None:
        self.candidates = candidates
        self.candidate_calls = 0
        self.mutation_calls = 0

    def list_candidates(self, _query: object) -> tuple[InfrastructureDeploymentObservation, ...]:
        self.candidate_calls += 1
        return self.candidates


class _MutationAdapter:
    profile_name = "render-infrastructure-deployment"
    provider_name = "render"
    is_live_provider = True

    def __init__(
        self,
        result: InfrastructureDeploymentMutationResult,
        *,
        is_auto_deploy_enabled: bool = False,
    ) -> None:
        self.result = result
        self.is_auto_deploy_enabled = is_auto_deploy_enabled
        self.policy_calls = 0
        self.mutation_calls = 0

    def get_service_policy(
        self,
        request: InfrastructureDeploymentServicePolicyRequest,
    ) -> InfrastructureDeploymentServicePolicyObservation:
        self.policy_calls += 1
        return InfrastructureDeploymentServicePolicyObservation(
            provider="render",
            service_id=request.service_id,
            release_mode="source_revision",
            trigger_mode="automatic" if self.is_auto_deploy_enabled else "manual",
            source_binding=InfrastructureDeploymentSourceBinding("github", "acme", "platform", "main"),
            workload_kind="web_service",
            is_suspended=False,
            provider_request_id="render-policy-request-1",
        )

    def rollback(
        self,
        _request: InfrastructureDeploymentRollbackRequest,
    ) -> InfrastructureDeploymentMutationResult:
        self.mutation_calls += 1
        return self.result


class _Ledger:
    def __init__(self) -> None:
        self.settled_statuses: list[str] = []
        self.completed_statuses: list[str] = []

    def settle_lookup(
        self,
        _ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        status: str,
        result_ref: dict[str, object] | None,
        error_ref: dict[str, object] | None,
        provider_resource_id: str | None,
    ) -> ReleaseDeliveryRecord:
        self.settled_statuses.append(status)
        return replace(
            row,
            status=cast(ReleaseDeliveryStatus, status),
            result_ref=result_ref,
            error_ref=error_ref,
            provider_resource_id=provider_resource_id,
        )

    def claim(self, _ctx: RequestContext, row: ReleaseDeliveryRecord) -> ReleaseDeliveryRecord:
        return replace(row, status="dispatching", execution_attempt=row.execution_attempt + 1)

    def complete(
        self,
        _ctx: RequestContext,
        row: ReleaseDeliveryRecord,
        status: str,
        result_ref: dict[str, object] | None,
        error_ref: dict[str, object] | None,
        provider_resource_id: str | None,
    ) -> ReleaseDeliveryRecord:
        self.completed_statuses.append(status)
        return replace(
            row,
            status=cast(ReleaseDeliveryStatus, status),
            result_ref=result_ref,
            error_ref=error_ref,
            provider_resource_id=provider_resource_id,
        )


class _Runtime:
    def _error_payload(
        self,
        exc: Exception,
        _ctx: RequestContext,
        *,
        run_id: str,
    ) -> dict[str, object]:
        return {"type": type(exc).__name__, "runId": run_id}


class _PolicyAdapter:
    profile_name = "render-deployment"
    provider_name = "render"
    is_live_provider = True

    def __init__(
        self,
        *,
        is_auto_deploy_enabled: bool = False,
        error: AdapterError | None = None,
        source_repository_owner: str = "acme",
        source_repository_name: str = "platform",
        source_branch: str = "main",
        service_type: str = "web_service",
        is_suspended: bool = False,
    ) -> None:
        self.is_auto_deploy_enabled = is_auto_deploy_enabled
        self.error = error
        self.source_repository_owner = source_repository_owner
        self.source_repository_name = source_repository_name
        self.source_branch = source_branch
        self.service_type = service_type
        self.is_suspended = is_suspended
        self.calls = 0

    def get_service_policy(
        self,
        _request: InfrastructureDeploymentServicePolicyRequest,
    ) -> InfrastructureDeploymentServicePolicyObservation:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return InfrastructureDeploymentServicePolicyObservation(
            provider="render",
            service_id="service-1",
            release_mode="source_revision",
            trigger_mode="automatic" if self.is_auto_deploy_enabled else "manual",
            source_binding=InfrastructureDeploymentSourceBinding(
                "github",
                self.source_repository_owner,
                self.source_repository_name,
                self.source_branch,
            ),
            workload_kind=self.service_type,
            is_suspended=self.is_suspended,
            provider_request_id="render-policy-request-1",
        )


class _MergeAdapter:
    profile_name = "github-release"
    provider_name = "github"
    is_live_provider = True

    def __init__(self) -> None:
        self.calls = 0
        self.lookup_calls = 0
        self.lookup_status = SourceControlMergeStatus.LANDED

    def merge_pull_request(self, request: SourceControlMergeRequest) -> SourceControlMergeReceipt:
        self.calls += 1
        target = request.target
        return SourceControlMergeReceipt(
            status=SourceControlMergeStatus.LANDED,
            repository_id=target.repository.repository_id,
            pull_number=target.pull_number,
            head_sha=target.expected_head_sha,
            merge_commit_sha="e" * 40,
            merged_at="2026-08-09T00:00:00Z",
            provider_request_id="github-merge-request-1",
            idempotency_key=request.idempotency_key,
            evidence={"exactHeadVerified": True},
        )

    def lookup_merge(self, target: PullRequestTarget) -> SourceControlMergeReceipt:
        self.lookup_calls += 1
        is_landed = self.lookup_status is SourceControlMergeStatus.LANDED
        return SourceControlMergeReceipt(
            status=self.lookup_status,
            repository_id=target.repository.repository_id,
            pull_number=target.pull_number,
            head_sha=target.expected_head_sha,
            merge_commit_sha="e" * 40 if is_landed else None,
            merged_at="2026-08-09T00:00:00Z" if is_landed else None,
            provider_request_id="github-lookup-request-1",
            idempotency_key=None,
            evidence={"exactHeadVerified": True},
        )


def test_render_receipt_acceptance_is_not_projected_as_operational_completion() -> None:
    row = _delivery_row(status="landed")
    queued = _observation(status="queued", is_terminal=False, is_successful=False)
    observations = [{"deliveryId": row.delivery_id, **deployment_observation_ref(queued)}]

    projection = delivery_projection(row)
    summary = application_delivery_summary((row,), observations, is_configured=True, is_required=True)

    assert projection["ledgerStatus"] == "landed"
    assert projection["receiptStatus"] == "receipt_accepted"
    assert projection["isOperationallyComplete"] is False
    assert summary["status"] == "deploying"
    assert summary["isOperationallyComplete"] is False


def test_application_completion_requires_exact_terminal_successful_live_identity() -> None:
    row = _delivery_row(status="landed")
    live = _observation(status="live", is_terminal=True, is_successful=True)
    exact = {"deliveryId": row.delivery_id, **deployment_observation_ref(live)}

    completed = application_delivery_summary((row,), (exact,), is_configured=True, is_required=True)
    assert completed["status"] == "live"
    assert completed["isOperationallyComplete"] is True
    for field, value in (
        ("provider", "other-provider"),
        ("deployId", "other-deploy"),
        ("serviceId", "other-service"),
        ("commitId", "f" * 40),
    ):
        mismatch = dict(exact, **{field: value})
        blocked = application_delivery_summary((row,), (mismatch,), is_configured=True, is_required=True)
        assert blocked["status"] == "outcome_unknown"
        assert blocked["reason"] == "provider_observation_identity_mismatch"
        assert blocked["isOperationallyComplete"] is False


@pytest.mark.parametrize(
    ("application_status", "expected_stage"),
    [
        ("deploying", "deploying"),
        ("observation_unavailable", "deployment_unverified"),
        ("failed", "deployment_failed"),
        ("live", "deployed"),
    ],
)
def test_gpt_stage_does_not_call_internal_promotion_deployed_before_render_live(
    application_status: str,
    expected_stage: str,
) -> None:
    deployment = {"id": "deployment-1", "status": "PROMOTED"}
    evidence = {
        "candidateDeployment": deployment,
        "currentDeployment": deployment,
        "externalDelivery": {
            "isConfigured": True,
            "applicationStatus": {
                "status": application_status,
                "isOperationallyComplete": application_status == "live",
            },
        },
    }

    assert release_stage("pipeline", {"status": "executed"}, evidence) == expected_stage


def test_ambiguous_reconciliation_to_absent_is_durable_but_never_successful() -> None:
    row = _delivery_row(status="ambiguous")
    adapter = _CandidateAdapter(())
    ledger = _Ledger()
    coordinator = _infrastructure_coordinator(adapter, ledger)

    with pytest.raises(ConflictDetected, match="was not created"):
        coordinator.resume(_context(), row)

    assert ledger.settled_statuses == ["absent"]
    assert adapter.candidate_calls == 1
    assert adapter.mutation_calls == 0


def test_ambiguous_reconciliation_to_queued_records_receipt_without_redispatch() -> None:
    row = _delivery_row(status="ambiguous")
    adapter = _CandidateAdapter((_observation(status="queued", is_terminal=False, is_successful=False),))
    ledger = _Ledger()
    coordinator = _infrastructure_coordinator(adapter, ledger)

    result = coordinator.resume(_context(), row)

    assert result["ledgerStatus"] == "landed"
    assert result["receiptStatus"] == "receipt_accepted"
    assert result["isOperationallyComplete"] is False
    assert ledger.settled_statuses == ["landed"]
    assert adapter.candidate_calls == 1
    assert adapter.mutation_calls == 0


def test_recent_dispatch_is_not_declared_absent_from_an_old_intent_timestamp() -> None:
    now = datetime.now(UTC)
    row = replace(
        _delivery_row(status="ambiguous"),
        created_at=(now - timedelta(hours=2)).isoformat(),
        dispatch_started_at=now.isoformat(),
    )
    adapter = _CandidateAdapter(())
    ledger = _Ledger()
    coordinator = _infrastructure_coordinator(adapter, ledger)

    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown, match="remains ambiguous"):
        coordinator.resume(_context(), row)

    assert ledger.settled_statuses == []


def test_stale_same_commit_deploy_is_not_adopted_during_reconciliation() -> None:
    now = datetime.now(UTC)
    row = replace(_delivery_row(status="ambiguous"), dispatch_started_at=now.isoformat())
    stale = replace(
        _observation(status="queued", is_terminal=False, is_successful=False),
        deploy_id="deploy-stale",
        created_at=now - timedelta(days=1),
    )
    adapter = _CandidateAdapter((stale,))
    ledger = _Ledger()
    coordinator = _infrastructure_coordinator(adapter, ledger)

    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown, match="remains ambiguous"):
        coordinator.resume(_context(), row)

    assert ledger.settled_statuses == []


@pytest.mark.parametrize(
    ("commit_id", "trigger"),
    [("b" * 40, "rollback"), ("a" * 40, "api")],
)
def test_direct_rollback_receipt_must_match_the_approved_commit_and_trigger(
    commit_id: str,
    trigger: str,
) -> None:
    row = replace(
        _delivery_row(status="prepared"),
        operation="application_rollback",
        candidate_ref={"targetDeployId": "deploy-target", "targetCommitId": "a" * 40},
        provider_operation_id=None,
        provider_resource_id=None,
        prior_resource_id="deploy-current",
    )
    observation = replace(
        _observation(status="queued", is_terminal=False, is_successful=False),
        deploy_id="deploy-new-rollback",
        commit_id=commit_id,
        trigger=trigger,
    )
    result = InfrastructureDeploymentMutationResult(
        operation="rollback",
        outcome="accepted",
        provider_http_status=201,
        observation=observation,
        rollback_target_deploy_id="deploy-target",
        is_safe_to_retry=False,
        reason="provider_deploy_receipt_created",
    )
    adapter = _MutationAdapter(result)
    ledger = _Ledger()
    coordinator = _infrastructure_coordinator(adapter, ledger)

    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown, match="receipt identity"):
        coordinator.resume(_context(), row)

    assert ledger.completed_statuses == ["ambiguous"]
    assert adapter.mutation_calls == 1


def test_application_mutation_rechecks_manual_policy_before_provider_dispatch() -> None:
    row = replace(
        _delivery_row(status="prepared"),
        operation="application_rollback",
        candidate_ref={"targetDeployId": "deploy-target", "targetCommitId": "a" * 40},
        provider_operation_id=None,
        provider_resource_id=None,
        prior_resource_id="deploy-current",
    )
    result = InfrastructureDeploymentMutationResult(
        operation="rollback",
        outcome="accepted",
        provider_http_status=201,
        observation=None,
        rollback_target_deploy_id="deploy-target",
        is_safe_to_retry=False,
        reason="unused",
    )
    adapter = _MutationAdapter(result, is_auto_deploy_enabled=True)
    ledger = _Ledger()
    coordinator = _infrastructure_coordinator(adapter, ledger)

    with pytest.raises(ConflictDetected, match="manual deployment policy was not verified"):
        coordinator.resume(_context(), row)

    assert adapter.policy_calls == 1
    assert adapter.mutation_calls == 0
    assert ledger.completed_statuses == []


def test_ambiguous_rollback_does_not_call_a_generic_same_commit_candidate_authoritative_absence() -> None:
    row = replace(
        _delivery_row(status="ambiguous"),
        operation="application_rollback",
        candidate_ref={"targetDeployId": "deploy-target", "targetCommitId": "a" * 40},
        provider_resource_id=None,
        prior_resource_id="deploy-current",
    )
    generic_same_commit = _observation(status="queued", is_terminal=False, is_successful=False)
    adapter = _CandidateAdapter((generic_same_commit,))
    ledger = _Ledger()
    coordinator = _infrastructure_coordinator(adapter, ledger)

    with pytest.raises(ExternalReleaseDeliveryOutcomeUnknown, match="exact new provider deployment"):
        coordinator.resume(_context(), row)

    assert ledger.settled_statuses == []
    assert adapter.candidate_calls == 1
    assert adapter.mutation_calls == 0


def test_ambiguous_rollback_reconciles_one_exact_new_rollback_receipt_without_redispatch() -> None:
    row = replace(
        _delivery_row(status="ambiguous"),
        operation="application_rollback",
        candidate_ref={"targetDeployId": "deploy-target", "targetCommitId": "a" * 40},
        provider_resource_id=None,
        prior_resource_id="deploy-current",
    )
    exact_new_rollback = replace(
        _observation(status="queued", is_terminal=False, is_successful=False),
        deploy_id="deploy-new-rollback",
        trigger="rollback",
    )
    adapter = _CandidateAdapter((exact_new_rollback,))
    ledger = _Ledger()
    coordinator = _infrastructure_coordinator(adapter, ledger)

    result = coordinator.resume(_context(), row)

    assert result["providerResourceId"] == "deploy-new-rollback"
    assert ledger.settled_statuses == ["landed"]
    assert adapter.candidate_calls == 1
    assert adapter.mutation_calls == 0


@pytest.mark.parametrize("failure_mode", ["auto_deploy", "read_failure"])
def test_source_merge_preflight_blocks_before_github_when_manual_policy_is_not_verified(
    failure_mode: str,
) -> None:
    error = _policy_read_error() if failure_mode == "read_failure" else None
    infrastructure = _PolicyAdapter(is_auto_deploy_enabled=failure_mode == "auto_deploy", error=error)
    source = _MergeAdapter()
    coordinator = _source_coordinator(source, infrastructure)

    with pytest.raises(ConflictDetected, match="manual deployment policy was not verified"):
        coordinator.resume(_context(), _source_delivery_row(), _source_target())

    assert infrastructure.calls == 1
    assert source.calls == 0


def test_source_merge_preflight_allows_exact_manual_policy_once() -> None:
    infrastructure = _PolicyAdapter(is_auto_deploy_enabled=False)
    source = _MergeAdapter()
    coordinator = _source_coordinator(source, infrastructure)

    completed = coordinator.resume(_context(), _source_delivery_row(), _source_target())

    assert completed["status"] == "landed"
    assert infrastructure.calls == 1
    assert source.calls == 1


@pytest.mark.parametrize(
    ("policy_field", "value"),
    [
        ("source_repository_owner", "other"),
        ("source_repository_name", "other"),
        ("source_branch", "develop"),
        ("service_type", "cron_job"),
        ("is_suspended", True),
    ],
)
def test_source_merge_preflight_blocks_mismatched_render_source_binding_before_github(
    policy_field: str,
    value: str | bool,
) -> None:
    infrastructure = _PolicyAdapter(**{policy_field: value})
    source = _MergeAdapter()
    coordinator = _source_coordinator(source, infrastructure)

    with pytest.raises(ConflictDetected, match="manual deployment policy was not verified"):
        coordinator.resume(_context(), _source_delivery_row(), _source_target())

    assert infrastructure.calls == 1
    assert source.calls == 0


def test_ambiguous_source_lookup_to_absent_is_durable_and_never_redispatches() -> None:
    infrastructure = _PolicyAdapter(is_auto_deploy_enabled=False)
    source = _MergeAdapter()
    source.lookup_status = SourceControlMergeStatus.ABSENT
    ledger = _Ledger()
    coordinator = _source_coordinator(source, infrastructure, ledger)

    with pytest.raises(ConflictDetected, match="authoritatively absent"):
        coordinator.resume(_context(), replace(_source_delivery_row(), status="ambiguous"), _source_target())

    assert source.lookup_calls == 1
    assert source.calls == 0
    assert infrastructure.calls == 0
    assert ledger.settled_statuses == ["absent"]


@pytest.mark.parametrize("status", ["absent", "failed", "ambiguous", "dispatching"])
def test_central_boundary_rejects_non_success_external_results(status: str) -> None:
    with pytest.raises(ConflictDetected, match="admissible outcome"):
        require_external_delivery_result({"status": status}, "source_merge")


def test_application_landed_audit_is_presented_as_accepted_not_succeeded() -> None:
    row = {
        "id": "audit-1",
        "event_type": "governed_release.delivery.landed",
        "created_at": "2026-08-09T00:00:00+00:00",
        "after_ref": {"operation": "application_deploy"},
    }

    evidence = release_status_evidence((row,))
    timeline = cast(list[dict[str, object]], evidence["executionTimeline"])

    assert timeline[0]["status"] == "accepted"


def _delivery_row(*, status: str) -> ReleaseDeliveryRecord:
    return ReleaseDeliveryRecord(
        delivery_id="delivery-1",
        tenant_id="tenant-a",
        application_id="application-a",
        proposal_id="proposal-1",
        release_kind="pipeline",
        workflow_run_id="workflow-run-a",
        parent_delivery_id="delivery-root",
        provider="render",
        operation="application_deploy",
        status=cast(ReleaseDeliveryStatus, status),
        target_ref={"serviceId": "service-1"},
        candidate_ref={"commitId": "a" * 40},
        environment="production",
        idempotency_key="deploy-key-1",
        request_fingerprint="sha256:" + "b" * 64,
        provider_operation_id="deploy-1",
        provider_resource_id="deploy-1",
        prior_resource_id=None,
        result_ref=None,
        error_ref=None,
        ai_run_id="run-1",
        binding_hash="sha256:" + "c" * 64,
        execution_attempt=1,
        request_id="request-1",
        created_by="operator-1",
        created_at="2026-08-09T00:00:00+00:00",
        updated_at="2026-08-09T00:00:01+00:00",
        dispatch_started_at="2026-08-09T00:00:00+00:00",
        completed_at="2026-08-09T00:00:01+00:00",
    )


def _source_delivery_row() -> ReleaseDeliveryRecord:
    row = _delivery_row(status="prepared")
    return replace(
        row,
        provider="github",
        operation="source_merge",
        target_ref={
            "repositoryId": 42,
            "repositoryOwner": "acme",
            "repositoryName": "platform",
            "pullNumber": 17,
            "baseRef": "main",
            "headSha": "a" * 40,
        },
        candidate_ref={
            "baseSha": "b" * 40,
            "headSha": "a" * 40,
            "headRef": "codex/release-1",
            "candidateTreeSha": "e" * 40,
            "manifestArtifactPath": ".foundry-lite/releases/pipeline/proposal-1.json",
            "manifestFingerprint": _source_manifest().manifest_fingerprint,
            "rulesFingerprint": "sha256:" + "c" * 64,
            "checksFingerprint": "sha256:" + "d" * 64,
        },
    )


def _source_manifest() -> SourceCandidateManifest:
    content = b"{}\n"
    return SourceCandidateManifest(
        ".foundry-lite/releases/pipeline/proposal-1.json",
        content,
        f"sha256:{hashlib.sha256(content).hexdigest()}",
    )


def _source_target() -> PullRequestTarget:
    binding = SourceCandidateCommitBinding(
        expected_base_sha="b" * 40,
        expected_tree_sha="e" * 40,
        expected_head_ref="codex/release-1",
        manifest=_source_manifest(),
    )
    return PullRequestTarget(
        SourceRepositoryRef("github", 42, "acme", "platform"),
        17,
        "main",
        "a" * 40,
        candidate_binding=binding,
    )


def _source_coordinator(
    source: _MergeAdapter,
    infrastructure: _PolicyAdapter,
    ledger: _Ledger | None = None,
) -> ExternalReleaseSourceCoordinator:
    config = GovernedReleaseDeliveryConfig(
        source_repository=SourceRepositoryRef("github", 42, "acme", "platform"),
        source_base_ref="main",
        source_head_prefix="codex/",
        source_merge_method=SourceControlMergeMethod.SQUASH,
        is_source_control_required=True,
        deployment_service_id="service-1",
        deployment_environment="production",
        is_deployment_required=True,
    )
    return ExternalReleaseSourceCoordinator(
        cast(SourceControlReleasePort, source),
        cast(InfrastructureDeploymentAdapter, infrastructure),
        config,
        cast(ExternalReleaseDeliveryLedger, ledger or _Ledger()),
        cast(RuntimeEvidenceBoundary, _Runtime()),
    )


def _infrastructure_coordinator(
    adapter: _CandidateAdapter | _MutationAdapter,
    ledger: _Ledger,
) -> ExternalReleaseInfrastructureCoordinator:
    return ExternalReleaseInfrastructureCoordinator(
        cast(InfrastructureDeploymentAdapter, adapter),
        GovernedReleaseDeliveryConfig(
            source_repository=SourceRepositoryRef("github", 42, "acme", "platform"),
            deployment_service_id="service-1",
        ),
        cast(ExternalReleaseDeliveryLedger, ledger),
        cast(RuntimeEvidenceBoundary, _Runtime()),
    )


def _policy_read_error() -> AdapterError:
    return AdapterError(
        AdapterFailure(
            adapter_profile="render-deployment",
            operation="get_service_policy",
            kind="timeout",
            is_retryable=True,
            operator_message="policy read timed out",
        )
    )


def _observation(
    *,
    status: str,
    is_terminal: bool,
    is_successful: bool,
) -> InfrastructureDeploymentObservation:
    now = datetime(2026, 8, 9, 0, 0, tzinfo=UTC)
    return InfrastructureDeploymentObservation(
        provider="render",
        service_id="service-1",
        deploy_id="deploy-1",
        status=cast(InfrastructureDeploymentStatus, status),
        provider_status="live" if status == "live" else "build_in_progress",
        commit_id="a" * 40,
        trigger="api",
        created_at=now,
        started_at=now,
        updated_at=now,
        finished_at=now if is_terminal else None,
        is_terminal=is_terminal,
        is_successful=is_successful,
        provider_request_id="render-request-1",
    )


def _context() -> RequestContext:
    return RequestContext(
        tenant_id="tenant-a",
        actor_user_id="operator-1",
        request_id="request-1",
        roles=("admin",),
        governed_release_run_id="run-1",
        governed_release_binding_hash="sha256:" + "c" * 64,
    )
