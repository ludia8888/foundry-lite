"""Provider-neutral boundary for exact pull-request release mutations.

The immutable repository id, base ref, and full head SHA bind each target so a
caller cannot redirect an approved release to another repository or commit.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureMode,
)
from foundry_lite.application.ports.source_control_candidate import (
    PullRequestTarget,
    SourceCandidateCommitBinding,
    SourceCandidatePublicationReceipt,
    SourceCandidatePublicationRequest,
    SourceRefSnapshot,
    SourceRepositoryRef,
    freeze_source_control_evidence,
)

_FULL_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")


class SourceControlMergeStatus(StrEnum):
    """Authoritative outcome of a remote pull-request merge lookup or write."""

    LANDED = "landed"
    AMBIGUOUS = "ambiguous"
    ABSENT = "absent"


class SourceControlMergeMethod(StrEnum):
    """Merge methods supported by the GitHub pull-request merge endpoint."""

    MERGE = "merge"
    SQUASH = "squash"
    REBASE = "rebase"


class SourceControlReviewDecision(StrEnum):
    """Normalized review decision used by the release admission check."""

    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REVIEW_REQUIRED = "review_required"


@dataclass(frozen=True)
class PullRequestSearch:
    """Bounded exact search used when the caller does not yet know the PR number."""

    repository: SourceRepositoryRef
    expected_base_ref: str
    expected_head_ref: str

    def __post_init__(self) -> None:
        if not self.expected_base_ref.strip() or not self.expected_head_ref.strip():
            raise ValueError("expected_base_ref and expected_head_ref are required")


@dataclass(frozen=True)
class BranchRuleEvidence:
    """Safe evidence for one active rule applying to the target base branch."""

    rule_type: str
    ruleset_id: int | None
    source_type: str
    source: str
    parameters_fingerprint: str

    def __post_init__(self) -> None:
        if not self.rule_type or not self.source_type or not self.source:
            raise ValueError("branch rule type and source identity are required")
        if self.ruleset_id is not None and not _is_positive_int(self.ruleset_id):
            raise ValueError("ruleset_id must be positive when present")
        if not _is_sha256(self.parameters_fingerprint):
            raise ValueError("branch rule parameters_fingerprint must be a full sha256 value")


@dataclass(frozen=True)
class RequiredCheckEvidence:
    """Evidence for one required check on the provider-selected merge result or head."""

    context: str
    commit_sha: str
    status: str
    conclusion: str | None
    source: str
    source_app_id: int | None
    is_successful: bool

    def __post_init__(self) -> None:
        if not self.context or not self.source or not _is_full_git_sha(self.commit_sha):
            raise ValueError("required check context and full commit SHA are required")
        if self.source_app_id is not None and not _is_positive_int(self.source_app_id):
            raise ValueError("required check source_app_id must be positive when present")


@dataclass(frozen=True)
class PullRequestReviewEvidence:
    """Safe reviewer identity and decision evidence without review body text."""

    reviewer_id: int
    reviewer_login: str
    state: str
    commit_sha: str
    submitted_at: str | None

    def __post_init__(self) -> None:
        if not _is_positive_int(self.reviewer_id) or not self.reviewer_login:
            raise ValueError("reviewer identity is required")
        if self.state not in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            raise ValueError("review state is unsupported")
        if not _is_full_git_sha(self.commit_sha):
            raise ValueError("review commit_sha must be a full lowercase Git SHA")


@dataclass(frozen=True)
class PullRequestSnapshot:
    """Fresh, immutable release admission evidence for one reviewed PR head."""

    target: PullRequestTarget
    state: str
    is_draft: bool
    is_merged: bool
    base_sha: str
    head_ref: str
    author_id: int
    author_login: str
    mergeable_state: str
    test_merge_commit_sha: str | None
    checks_commit_sha: str
    review_decision: SourceControlReviewDecision
    required_approval_count: int
    approvals: tuple[PullRequestReviewEvidence, ...]
    active_rules: tuple[BranchRuleEvidence, ...]
    required_checks: tuple[RequiredCheckEvidence, ...]
    is_merge_queue_required: bool
    rules_fingerprint: str
    checks_fingerprint: str
    blocking_reasons: tuple[str, ...]
    is_ready_to_merge: bool
    provider_request_id: str | None = None

    def __post_init__(self) -> None:
        """Reject internally inconsistent admission evidence."""

        _validate_snapshot_identity(self)
        _validate_snapshot_fingerprints(self)
        _validate_snapshot_readiness(self)


def _validate_snapshot_identity(snapshot: PullRequestSnapshot) -> None:
    """Validate immutable commit, author, and approval-count identity."""

    if not _is_full_git_sha(snapshot.base_sha):
        raise ValueError("base_sha must be a full lowercase Git SHA")
    if not _is_positive_int(snapshot.author_id) or not snapshot.author_login:
        raise ValueError("pull request author identity is required")
    _validate_snapshot_check_commit_identity(snapshot)
    if isinstance(snapshot.required_approval_count, bool) or snapshot.required_approval_count < 0:
        raise ValueError("required_approval_count must be non-negative")


def _validate_snapshot_check_commit_identity(snapshot: PullRequestSnapshot) -> None:
    """Bind required-check evidence to the reviewed head or its test merge commit."""

    test_merge_sha = snapshot.test_merge_commit_sha
    if test_merge_sha is not None and not _is_full_git_sha(test_merge_sha):
        raise ValueError("test_merge_commit_sha must be a full lowercase Git SHA when present")
    if not _is_full_git_sha(snapshot.checks_commit_sha):
        raise ValueError("checks_commit_sha must be a full lowercase Git SHA")
    allowed_commits = {snapshot.target.expected_head_sha, test_merge_sha}
    if snapshot.checks_commit_sha not in allowed_commits:
        raise ValueError("checks_commit_sha must be the reviewed head or test merge commit")


def _validate_snapshot_fingerprints(snapshot: PullRequestSnapshot) -> None:
    """Require full policy and check fingerprints."""

    if not _is_sha256(snapshot.rules_fingerprint) or not _is_sha256(snapshot.checks_fingerprint):
        raise ValueError("rules and checks fingerprints must be full sha256 values")


def _validate_snapshot_readiness(snapshot: PullRequestSnapshot) -> None:
    """Keep the ready flag derived from complete server evidence."""

    if snapshot.is_ready_to_merge != (not snapshot.blocking_reasons):
        raise ValueError("readiness must match blocking_reasons")
    if snapshot.is_ready_to_merge and not _has_ready_snapshot_evidence(snapshot):
        raise ValueError("ready pull request is missing approval or policy evidence")


def _has_ready_snapshot_evidence(snapshot: PullRequestSnapshot) -> bool:
    """Return whether state, review, and provider-selected checks permit merge."""

    return (
        _has_open_snapshot_state(snapshot)
        and _has_snapshot_approval_evidence(snapshot)
        and _has_snapshot_check_evidence(snapshot)
    )


def _has_open_snapshot_state(snapshot: PullRequestSnapshot) -> bool:
    """Require one open, mergeable, non-draft, non-queued PR."""

    return (
        snapshot.state == "open"
        and not snapshot.is_draft
        and not snapshot.is_merged
        and not snapshot.is_merge_queue_required
        and snapshot.mergeable_state in {"clean", "has_hooks"}
    )


def _has_snapshot_approval_evidence(snapshot: PullRequestSnapshot) -> bool:
    """Require the configured number of current-head approvals."""

    reviewer_ids = tuple(approval.reviewer_id for approval in snapshot.approvals)
    has_acceptable_decision = snapshot.review_decision is not SourceControlReviewDecision.CHANGES_REQUESTED and (
        snapshot.required_approval_count == 0 or snapshot.review_decision is SourceControlReviewDecision.APPROVED
    )
    approvals_are_current = all(
        approval.state == "APPROVED" and approval.commit_sha == snapshot.target.expected_head_sha
        for approval in snapshot.approvals
    )
    approvals_are_independent = snapshot.author_id not in reviewer_ids and len(set(reviewer_ids)) == len(reviewer_ids)
    return (
        has_acceptable_decision
        and len(snapshot.approvals) >= snapshot.required_approval_count
        and approvals_are_current
        and approvals_are_independent
    )


def _has_snapshot_check_evidence(snapshot: PullRequestSnapshot) -> bool:
    """Require every check on the provider-selected merge result or head."""

    return all(
        check.commit_sha == snapshot.checks_commit_sha and _has_consistent_successful_check(check)
        for check in snapshot.required_checks
    )


def _has_consistent_successful_check(check: RequiredCheckEvidence) -> bool:
    """Reject missing, nonterminal, or contradictory successful-check claims."""

    if not check.is_successful or check.source == "missing":
        return False
    if check.status not in {"completed", "success"}:
        return False
    return check.conclusion in {"success", "neutral", "skipped"}


@dataclass(frozen=True)
class SourceControlMergeRequest:
    """Idempotent application request for one exact, already-reviewed PR head."""

    target: PullRequestTarget
    merge_method: SourceControlMergeMethod
    idempotency_key: str
    expected_base_sha: str
    expected_rules_fingerprint: str
    expected_checks_fingerprint: str

    def __post_init__(self) -> None:
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        _require_merge_method(self.merge_method)
        if not _is_full_git_sha(self.expected_base_sha):
            raise ValueError("expected_base_sha must be a full lowercase Git SHA")
        if not _is_sha256(self.expected_rules_fingerprint) or not _is_sha256(self.expected_checks_fingerprint):
            raise ValueError("expected rules and checks fingerprints must be full sha256 values")


def _empty_receipt_evidence() -> Mapping[str, object]:
    return {}


@dataclass(frozen=True)
class SourceControlMergeReceipt:
    """Remote merge outcome safe to persist in the governed release ledger."""

    status: SourceControlMergeStatus
    repository_id: int
    pull_number: int
    head_sha: str
    merge_commit_sha: str | None = None
    merged_at: str | None = None
    provider_request_id: str | None = None
    idempotency_key: str | None = None
    evidence: Mapping[str, object] = field(default_factory=_empty_receipt_evidence)

    def __post_init__(self) -> None:
        """Validate and recursively freeze durable provider evidence."""

        _require_merge_status(self.status)
        _validate_receipt_identity(self)
        _validate_receipt_outcome(self)
        _validate_receipt_idempotency_key(self)
        object.__setattr__(self, "evidence", freeze_source_control_evidence(self.evidence))


def _validate_receipt_identity(receipt: SourceControlMergeReceipt) -> None:
    """Validate repository, PR, and reviewed-head identity."""

    if not _is_positive_int(receipt.repository_id) or not _is_positive_int(receipt.pull_number):
        raise ValueError("receipt repository_id and pull_number must be positive")
    if not _is_full_git_sha(receipt.head_sha):
        raise ValueError("receipt head_sha must be a full lowercase Git SHA")


def _validate_receipt_outcome(receipt: SourceControlMergeReceipt) -> None:
    """Keep landed-only fields consistent with the provider outcome."""

    is_landed = receipt.status is SourceControlMergeStatus.LANDED
    has_merge_sha = receipt.merge_commit_sha is not None and _is_full_git_sha(receipt.merge_commit_sha)
    invalid_states = (
        (is_landed and not has_merge_sha, "landed receipt requires a full merge_commit_sha"),
        (not is_landed and receipt.merge_commit_sha is not None, "non-landed receipt cannot carry merge_commit_sha"),
        (not is_landed and receipt.merged_at is not None, "non-landed receipt cannot carry merged_at"),
    )
    for is_invalid, message in invalid_states:
        if is_invalid:
            raise ValueError(message)


def _validate_receipt_idempotency_key(receipt: SourceControlMergeReceipt) -> None:
    """Reject an explicitly supplied empty idempotency key."""

    if receipt.idempotency_key is not None and not receipt.idempotency_key.strip():
        raise ValueError("receipt idempotency_key cannot be empty")


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_FINGERPRINT.fullmatch(value) is not None


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_full_git_sha(value: object) -> bool:
    return isinstance(value, str) and _FULL_GIT_SHA.fullmatch(value) is not None


def _require_merge_method(value: object) -> None:
    if not isinstance(value, SourceControlMergeMethod):
        raise ValueError("merge_method must be a SourceControlMergeMethod")


def _require_merge_status(value: object) -> None:
    if not isinstance(value, SourceControlMergeStatus):
        raise ValueError("receipt status must be a SourceControlMergeStatus")


@runtime_checkable
class SourceControlReleasePort(Protocol):
    """Inspect, merge, and reconcile an immutable source-control release target."""

    @property
    def profile_name(self) -> str:
        """Return the configured adapter profile for durable evidence."""
        ...

    def inspect_source_ref(self, repository: SourceRepositoryRef, ref: str) -> SourceRefSnapshot:
        """Inspect an exact repository ref before candidate publication."""
        ...

    def publish_pull_request_candidate(
        self, request: SourceCandidatePublicationRequest
    ) -> SourceCandidatePublicationReceipt:
        """Publish or reconcile an exact commit, branch, and pull request candidate."""
        ...

    def lookup_pull_request_candidate(
        self, request: SourceCandidatePublicationRequest
    ) -> SourceCandidatePublicationReceipt:
        """Look up authoritative publication state after an ambiguous outcome."""
        ...

    def find_pull_request(self, search: PullRequestSearch) -> PullRequestSnapshot:
        """Find the unique pull request matching exact repository coordinates."""
        ...

    def inspect_pull_request(self, target: PullRequestTarget) -> PullRequestSnapshot:
        """Read fresh pull request, protection, review, and CI evidence."""
        ...

    def merge_pull_request(self, request: SourceControlMergeRequest) -> SourceControlMergeReceipt:
        """Merge an immutable approved target under provider-side guards."""
        ...

    def lookup_merge(self, target: PullRequestTarget) -> SourceControlMergeReceipt:
        """Reconcile authoritative merge state for an exact target."""
        ...

    def failure_contract(self) -> AdapterFailureContract:
        """Describe operation-specific retry and outcome-ambiguity behavior."""
        ...


class UnavailableSourceControlReleasePort:
    """Fail-closed default until a source-control adapter is explicitly composed."""

    profile_name = "source-control-unavailable"

    def inspect_source_ref(self, repository: SourceRepositoryRef, ref: str) -> SourceRefSnapshot:
        del repository, ref
        raise _unavailable_error("inspect_source_ref")

    def publish_pull_request_candidate(
        self, request: SourceCandidatePublicationRequest
    ) -> SourceCandidatePublicationReceipt:
        del request
        raise _unavailable_error("publish_pull_request_candidate")

    def lookup_pull_request_candidate(
        self, request: SourceCandidatePublicationRequest
    ) -> SourceCandidatePublicationReceipt:
        del request
        raise _unavailable_error("lookup_pull_request_candidate")

    def find_pull_request(self, search: PullRequestSearch) -> PullRequestSnapshot:
        del search
        raise _unavailable_error("find_pull_request")

    def inspect_pull_request(self, target: PullRequestTarget) -> PullRequestSnapshot:
        del target
        raise _unavailable_error("inspect_pull_request")

    def merge_pull_request(self, request: SourceControlMergeRequest) -> SourceControlMergeReceipt:
        del request
        raise _unavailable_error("merge_pull_request")

    def lookup_merge(self, target: PullRequestTarget) -> SourceControlMergeReceipt:
        del target
        raise _unavailable_error("lookup_merge")

    def failure_contract(self) -> AdapterFailureContract:
        operations = (
            "inspect_source_ref",
            "publish_pull_request_candidate",
            "lookup_pull_request_candidate",
            "find_pull_request",
            "inspect_pull_request",
            "merge_pull_request",
            "lookup_merge",
        )
        modes = tuple(
            AdapterFailureMode(operation, "unsupported", False, "Source-control release adapter is unavailable.")
            for operation in operations
        )
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=modes)


def _unavailable_error(operation: str) -> AdapterError:
    return AdapterError(
        AdapterFailure(
            adapter_profile="source-control-unavailable",
            operation=operation,
            kind="unsupported",
            is_retryable=False,
            operator_message="Source-control release adapter is unavailable.",
            details={"reason": "source_control_release_unavailable"},
        )
    )


__all__ = [
    "BranchRuleEvidence",
    "PullRequestReviewEvidence",
    "PullRequestSearch",
    "PullRequestSnapshot",
    "PullRequestTarget",
    "RequiredCheckEvidence",
    "SourceCandidateCommitBinding",
    "SourceCandidatePublicationReceipt",
    "SourceCandidatePublicationRequest",
    "SourceControlMergeMethod",
    "SourceControlMergeReceipt",
    "SourceControlMergeRequest",
    "SourceControlMergeStatus",
    "SourceControlReleasePort",
    "SourceControlReviewDecision",
    "SourceRepositoryRef",
    "SourceRefSnapshot",
    "UnavailableSourceControlReleasePort",
]
