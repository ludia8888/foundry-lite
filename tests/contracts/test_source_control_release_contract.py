"""Per-port contract for exact, outcome-aware source-control releases."""

from __future__ import annotations

from dataclasses import replace

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailureContract
from foundry_lite.application.ports.source_control_candidate import (
    SourceCandidateCommitBinding,
    SourceCandidatePublicationReceipt,
    SourceCandidatePublicationRequest,
    SourceCandidatePublicationStatus,
    SourceRefSnapshot,
)
from foundry_lite.application.ports.source_control_release import (
    PullRequestReviewEvidence,
    PullRequestSearch,
    PullRequestSnapshot,
    PullRequestTarget,
    RequiredCheckEvidence,
    SourceControlMergeMethod,
    SourceControlMergeReceipt,
    SourceControlMergeRequest,
    SourceControlMergeStatus,
    SourceControlReleasePort,
    SourceControlReviewDecision,
    SourceRepositoryRef,
    UnavailableSourceControlReleasePort,
)

_HEAD_SHA = "a" * 40
_MERGE_SHA = "b" * 40
_BASE_SHA = "c" * 40
_RULES_FINGERPRINT = f"sha256:{'d' * 64}"
_CHECKS_FINGERPRINT = f"sha256:{'e' * 64}"


class _FakeSourceControlReleasePort:
    profile_name = "fake-source-control-release"

    def __init__(self) -> None:
        self.is_merged = False
        self.is_ambiguous = False

    def inspect_source_ref(self, repository: SourceRepositoryRef, ref: str) -> SourceRefSnapshot:
        return SourceRefSnapshot(repository, ref, _BASE_SHA, "f" * 40)

    def publish_pull_request_candidate(
        self,
        request: SourceCandidatePublicationRequest,
    ) -> SourceCandidatePublicationReceipt:
        binding = SourceCandidateCommitBinding(
            request.expected_base_sha,
            "f" * 40,
            request.expected_head_ref,
            request.manifest,
        )
        return SourceCandidatePublicationReceipt(
            SourceCandidatePublicationStatus.PUBLISHED,
            request.repository,
            request.expected_base_ref,
            request.expected_head_ref,
            request.expected_base_sha,
            request.manifest.artifact_path,
            request.manifest.manifest_fingerprint,
            request.idempotency_key,
            head_sha=_HEAD_SHA,
            pull_number=17,
            commit_binding=binding,
        )

    def lookup_pull_request_candidate(
        self,
        request: SourceCandidatePublicationRequest,
    ) -> SourceCandidatePublicationReceipt:
        return SourceCandidatePublicationReceipt(
            SourceCandidatePublicationStatus.ABSENT,
            request.repository,
            request.expected_base_ref,
            request.expected_head_ref,
            request.expected_base_sha,
            request.manifest.artifact_path,
            request.manifest.manifest_fingerprint,
            request.idempotency_key,
        )

    def find_pull_request(self, search: PullRequestSearch) -> PullRequestSnapshot:
        target = PullRequestTarget(search.repository, 17, search.expected_base_ref, _HEAD_SHA)
        return _snapshot(target)

    def inspect_pull_request(self, target: PullRequestTarget) -> PullRequestSnapshot:
        return _snapshot(target)

    def merge_pull_request(self, request: SourceControlMergeRequest) -> SourceControlMergeReceipt:
        if self.is_ambiguous:
            return _receipt(request.target, SourceControlMergeStatus.AMBIGUOUS, request.idempotency_key)
        self.is_merged = True
        return _receipt(request.target, SourceControlMergeStatus.LANDED, request.idempotency_key)

    def lookup_merge(self, target: PullRequestTarget) -> SourceControlMergeReceipt:
        status = SourceControlMergeStatus.LANDED if self.is_merged else SourceControlMergeStatus.ABSENT
        return _receipt(target, status, None)

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())


def _repository() -> SourceRepositoryRef:
    return SourceRepositoryRef("github", 123, "ludia8888", "foundry-lite")


def _target() -> PullRequestTarget:
    return PullRequestTarget(_repository(), 17, "main", _HEAD_SHA)


def _snapshot(target: PullRequestTarget) -> PullRequestSnapshot:
    return PullRequestSnapshot(
        target=target,
        state="open",
        is_draft=False,
        is_merged=False,
        base_sha=_BASE_SHA,
        head_ref="codex/release-17",
        author_id=7,
        author_login="author",
        mergeable_state="clean",
        test_merge_commit_sha=None,
        checks_commit_sha=target.expected_head_sha,
        review_decision=SourceControlReviewDecision.APPROVED,
        required_approval_count=1,
        approvals=(PullRequestReviewEvidence(8, "reviewer", "APPROVED", _HEAD_SHA, None),),
        active_rules=(),
        required_checks=(),
        is_merge_queue_required=False,
        rules_fingerprint=_RULES_FINGERPRINT,
        checks_fingerprint=_CHECKS_FINGERPRINT,
        blocking_reasons=(),
        is_ready_to_merge=True,
    )


def _receipt(
    target: PullRequestTarget,
    status: SourceControlMergeStatus,
    idempotency_key: str | None,
) -> SourceControlMergeReceipt:
    return SourceControlMergeReceipt(
        status=status,
        repository_id=target.repository.repository_id,
        pull_number=target.pull_number,
        head_sha=target.expected_head_sha,
        merge_commit_sha=_MERGE_SHA if status is SourceControlMergeStatus.LANDED else None,
        idempotency_key=idempotency_key,
    )


def _merge_request(method: SourceControlMergeMethod, key: str) -> SourceControlMergeRequest:
    return SourceControlMergeRequest(
        _target(),
        method,
        key,
        expected_base_sha=_BASE_SHA,
        expected_rules_fingerprint=_RULES_FINGERPRINT,
        expected_checks_fingerprint=_CHECKS_FINGERPRINT,
    )


def test_source_control_release_port_is_runtime_checkable() -> None:
    assert isinstance(_FakeSourceControlReleasePort(), SourceControlReleasePort)
    assert isinstance(UnavailableSourceControlReleasePort(), SourceControlReleasePort)


def test_source_control_release_contract_exact_search_returns_one_sealed_target() -> None:
    adapter = _FakeSourceControlReleasePort()

    snapshot = adapter.find_pull_request(PullRequestSearch(_repository(), "main", "codex/release-17"))

    assert snapshot.target == _target()
    assert snapshot.is_ready_to_merge is True


def test_source_control_release_contract_allows_internal_governance_as_the_only_required_human_review() -> None:
    snapshot = replace(
        _snapshot(_target()),
        required_approval_count=0,
        approvals=(),
        review_decision=SourceControlReviewDecision.REVIEW_REQUIRED,
    )

    assert snapshot.is_ready_to_merge is True


def test_source_control_release_contract_rejects_ready_snapshot_without_confirmed_mergeability() -> None:
    with pytest.raises(ValueError, match="missing approval or policy evidence"):
        replace(_snapshot(_target()), mergeable_state="blocked")


def test_source_control_release_contract_rejects_ready_snapshot_with_changes_requested() -> None:
    with pytest.raises(ValueError, match="missing approval or policy evidence"):
        replace(
            _snapshot(_target()),
            required_approval_count=0,
            approvals=(),
            review_decision=SourceControlReviewDecision.CHANGES_REQUESTED,
        )


def test_source_control_release_contract_rejects_duplicate_or_author_approvals() -> None:
    approval = PullRequestReviewEvidence(8, "reviewer", "APPROVED", _HEAD_SHA, None)
    with pytest.raises(ValueError, match="missing approval or policy evidence"):
        replace(_snapshot(_target()), required_approval_count=2, approvals=(approval, approval))

    author_approval = PullRequestReviewEvidence(7, "author", "APPROVED", _HEAD_SHA, None)
    with pytest.raises(ValueError, match="missing approval or policy evidence"):
        replace(_snapshot(_target()), approvals=(author_approval,))


def test_source_control_release_contract_rejects_contradictory_successful_check() -> None:
    contradictory = RequiredCheckEvidence(
        context="quality-gate",
        commit_sha=_HEAD_SHA,
        status="completed",
        conclusion="failure",
        source="github_check_run",
        source_app_id=15368,
        is_successful=True,
    )

    with pytest.raises(ValueError, match="missing approval or policy evidence"):
        replace(_snapshot(_target()), required_checks=(contradictory,))


def test_source_control_release_contract_binds_checks_to_head_or_test_merge_commit() -> None:
    merge_check = RequiredCheckEvidence(
        context="quality-gate",
        commit_sha=_MERGE_SHA,
        status="completed",
        conclusion="success",
        source="github_check_run",
        source_app_id=15368,
        is_successful=True,
    )

    snapshot = replace(
        _snapshot(_target()),
        test_merge_commit_sha=_MERGE_SHA,
        checks_commit_sha=_MERGE_SHA,
        required_checks=(merge_check,),
    )
    assert snapshot.is_ready_to_merge is True

    with pytest.raises(ValueError, match="reviewed head or test merge commit"):
        replace(_snapshot(_target()), checks_commit_sha="f" * 40)


def test_source_control_release_contract_merge_and_lookup_distinguish_landed_and_absent() -> None:
    adapter = _FakeSourceControlReleasePort()
    request = _merge_request(SourceControlMergeMethod.SQUASH, "merge-17")

    assert adapter.lookup_merge(_target()).status is SourceControlMergeStatus.ABSENT
    receipt = adapter.merge_pull_request(request)
    replay = adapter.lookup_merge(_target())

    assert receipt.status is SourceControlMergeStatus.LANDED
    assert receipt.idempotency_key == "merge-17"
    assert replay.status is SourceControlMergeStatus.LANDED
    assert replay.merge_commit_sha == receipt.merge_commit_sha


def test_source_control_release_contract_ambiguous_is_not_reported_as_failure_or_landed() -> None:
    adapter = _FakeSourceControlReleasePort()
    adapter.is_ambiguous = True
    request = _merge_request(SourceControlMergeMethod.MERGE, "merge-ambiguous")

    receipt = adapter.merge_pull_request(request)

    assert receipt.status is SourceControlMergeStatus.AMBIGUOUS
    assert receipt.merge_commit_sha is None
    assert adapter.lookup_merge(_target()).status is SourceControlMergeStatus.ABSENT


def test_source_control_release_contract_rejects_short_or_uppercase_head_sha() -> None:
    with pytest.raises(ValueError, match="full lowercase"):
        replace(_target(), expected_head_sha="abc123")
    with pytest.raises(ValueError, match="full lowercase"):
        replace(_target(), expected_head_sha="A" * 40)


def test_source_control_release_contract_unavailable_profile_fails_closed() -> None:
    adapter = UnavailableSourceControlReleasePort()

    with pytest.raises(AdapterError) as excinfo:
        adapter.merge_pull_request(_merge_request(SourceControlMergeMethod.SQUASH, "unavailable"))

    assert excinfo.value.failure.kind == "unsupported"
    assert excinfo.value.failure.is_retryable is False
    assert adapter.failure_contract().adapter_profile == "source-control-unavailable"


def test_source_control_release_contract_rejects_impossible_receipts() -> None:
    with pytest.raises(ValueError, match="landed receipt requires"):
        SourceControlMergeReceipt(SourceControlMergeStatus.LANDED, 123, 17, _HEAD_SHA)
    with pytest.raises(ValueError, match="non-landed receipt"):
        SourceControlMergeReceipt(
            SourceControlMergeStatus.ABSENT,
            123,
            17,
            _HEAD_SHA,
            merge_commit_sha=_MERGE_SHA,
        )


def test_source_control_release_contract_receipt_evidence_is_defensively_immutable() -> None:
    original: dict[str, object] = {"provider": "github"}
    receipt = SourceControlMergeReceipt(
        SourceControlMergeStatus.ABSENT,
        123,
        17,
        _HEAD_SHA,
        evidence=original,
    )

    original["provider"] = "changed"

    assert receipt.evidence == {"provider": "github"}
    with pytest.raises(TypeError):
        receipt.evidence["provider"] = "changed"  # type: ignore[index]
