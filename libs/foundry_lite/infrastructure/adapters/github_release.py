"""GitHub App installation-token adapter for exact, governed PR releases.

This adapter never discovers an arbitrary API host and never accepts a short
commit ref.  All calls target ``https://api.github.com`` and every mutation is
bound to the configured numeric repository id, an allowlisted base branch, an
allowlisted same-repository head branch, and the reviewed full head SHA.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Literal, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureKind,
    AdapterFailureMode,
)
from foundry_lite.application.ports.secret_provider import SecretProvider
from foundry_lite.application.ports.source_control_candidate import (
    SourceCandidateCommitBinding,
    SourceCandidateManifest,
    SourceCandidatePublicationReceipt,
    SourceCandidatePublicationRequest,
    SourceCandidatePublicationStatus,
    SourceRefSnapshot,
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
    SourceRepositoryRef,
)

_GITHUB_API_ROOT = "https://api.github.com"
_GITHUB_API_VERSION = "2026-03-10"
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_MANIFEST_BYTES = 64 * 1024
_PAGE_SIZE = 100
_FULL_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SAFE_COORDINATE = re.compile(r"^[A-Za-z0-9_.-]+$")
_SUPPORTED_ACTIVE_RULE_TYPES = frozenset({"required_status_checks", "pull_request", "merge_queue"})
_MERGE_OUTCOME_UNKNOWN_HTTP_STATUSES = frozenset({405, 409, 422})
_EXACT_HTTP_FAILURES: Mapping[int, tuple[AdapterFailureKind, bool]] = {
    401: ("authentication", False),
    403: ("authorization", False),
    404: ("not_found", False),
    405: ("conflict", False),
    409: ("conflict", False),
    422: ("validation", False),
    429: ("rate_limited", True),
}


@dataclass(frozen=True)
class GitHubReleaseConfig:
    """Static allowlist and installation-token reference for one repository."""

    repository: SourceRepositoryRef
    installation_token_secret_ref: str
    allowed_base_refs: tuple[str, ...] = ("main",)
    allowed_head_ref_prefixes: tuple[str, ...] = ("codex/",)
    minimum_approvals: int = 1
    allowed_merge_methods: tuple[SourceControlMergeMethod, ...] = (SourceControlMergeMethod.SQUASH,)
    is_bypass_policy_verified: bool = False
    timeout_seconds: int = 15
    max_pages: int = 10
    max_reviewers: int = 100

    def __post_init__(self) -> None:
        """Validate fixed repository, credential reference, policy, and I/O bounds."""

        _validate_github_repository_config(self)
        _validate_github_ref_config(self)
        _validate_github_merge_config(self)
        _validate_github_config_bounds(self)


def _validate_github_repository_config(config: GitHubReleaseConfig) -> None:
    """Require one safe GitHub repository and a nonempty secret reference."""

    if config.repository.provider != "github":
        raise ValueError("GitHub release config requires provider='github'")
    if not _is_safe_coordinate(config.repository.owner) or not _is_safe_coordinate(config.repository.name):
        raise ValueError("GitHub repository owner and name contain unsupported characters")
    if not isinstance(config.installation_token_secret_ref, str) or not config.installation_token_secret_ref.strip():
        raise ValueError("installation_token_secret_ref is required")


def _validate_github_ref_config(config: GitHubReleaseConfig) -> None:
    """Require bounded base refs and same-repository head prefixes."""

    if not config.allowed_base_refs or not all(_is_safe_git_ref(ref) for ref in config.allowed_base_refs):
        raise ValueError("at least one safe allowed_base_ref is required")
    if not config.allowed_head_ref_prefixes or not all(
        _is_safe_ref_prefix(prefix) for prefix in config.allowed_head_ref_prefixes
    ):
        raise ValueError("at least one safe allowed_head_ref_prefix is required")


def _validate_github_merge_config(config: GitHubReleaseConfig) -> None:
    """Require at least one typed provider-supported merge method."""

    if not config.allowed_merge_methods or not all(
        isinstance(method, SourceControlMergeMethod) for method in config.allowed_merge_methods
    ):
        raise ValueError("at least one typed allowed_merge_method is required")


def _validate_github_config_bounds(config: GitHubReleaseConfig) -> None:
    """Keep reviewer, pagination, timeout, and approval counts bounded."""

    numeric_bounds = (config.minimum_approvals, config.timeout_seconds, config.max_pages, config.max_reviewers)
    has_valid_bounds = (
        all(isinstance(value, int) and not isinstance(value, bool) for value in numeric_bounds)
        and config.minimum_approvals >= 0
        and config.timeout_seconds >= 1
        and 1 <= config.max_pages <= 100
        and 1 <= config.max_reviewers <= 1000
        and isinstance(config.is_bypass_policy_verified, bool)
    )
    if not has_valid_bounds:
        raise ValueError("approval must be non-negative and timeout, pagination, and reviewer bounds must be positive")


@dataclass(frozen=True)
class GitHubHttpRequest:
    """Transport-neutral request whose representation cannot expose credentials."""

    method: Literal["GET", "POST", "PUT"]
    url: str
    headers: Mapping[str, str] = field(repr=False)
    body: Mapping[str, object] | None = field(default=None, repr=False)
    timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        parsed = urlsplit(self.url)
        if parsed.scheme != "https" or parsed.netloc != "api.github.com":
            raise ValueError("GitHub transport URL must use fixed https://api.github.com")
        if self.method not in {"GET", "POST", "PUT"}:
            raise ValueError("GitHub transport method is unsupported")
        if (
            not isinstance(self.timeout_seconds, int | float)
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("GitHub transport timeout must be finite and positive")


@dataclass(frozen=True)
class GitHubHttpResponse:
    """Bounded JSON response returned by the injected or urllib transport."""

    status_code: int
    headers: Mapping[str, str]
    body: object

    def __post_init__(self) -> None:
        has_valid_status = (
            not isinstance(self.status_code, bool)
            and isinstance(self.status_code, int)
            and 100 <= self.status_code <= 599
        )
        if not has_valid_status:
            raise ValueError("GitHub response status_code must be an HTTP status")
        if not isinstance(self.headers, Mapping):
            raise ValueError("GitHub response headers must be a mapping")


class GitHubTransportFailure(Exception):
    """Redacted network failure; raw exception text is deliberately discarded."""

    def __init__(self, kind: AdapterFailureKind) -> None:
        super().__init__(f"github_transport_{kind}")
        self.kind: AdapterFailureKind = kind


GitHubTransport = Callable[[GitHubHttpRequest], GitHubHttpResponse]


@dataclass(frozen=True)
class _PullRequestRecord:
    number: int
    state: str
    is_draft: bool
    is_merged: bool
    is_mergeable: bool | None
    mergeable_state: str
    base_ref: str
    base_sha: str
    base_repository_id: int
    head_ref: str
    head_sha: str
    head_repository_id: int
    author_id: int
    author_login: str
    merge_commit_sha: str | None
    merged_at: str | None


@dataclass(frozen=True)
class _RequiredCheckPolicy:
    context: str
    source_app_id: int | None


@dataclass(frozen=True)
class _RuleSummary:
    evidence: tuple[BranchRuleEvidence, ...]
    required_checks: tuple[_RequiredCheckPolicy, ...]
    required_approvals: int
    is_merge_queue_required: bool
    requires_code_owner_review: bool
    requires_thread_resolution: bool
    requires_last_push_approval: bool
    has_required_team_reviewers: bool
    unsupported_rule_types: tuple[str, ...]
    is_bypass_policy_verified: bool
    fingerprint: str


@dataclass(frozen=True)
class _ClassicProtectionSummary:
    evidence: tuple[BranchRuleEvidence, ...]
    required_checks: tuple[_RequiredCheckPolicy, ...]
    required_approvals: int
    requires_code_owner_review: bool
    requires_thread_resolution: bool
    requires_last_push_approval: bool


@dataclass(frozen=True)
class _GitCommitRecord:
    sha: str
    tree_sha: str
    parent_shas: tuple[str, ...]


class GitHubReleaseAdapter:
    """Fail-closed GitHub PR inspection, merge, and outcome reconciliation."""

    profile_name = "github-release"
    provider_name = "github"
    is_live_provider = True

    def __init__(
        self,
        config: GitHubReleaseConfig,
        secret_provider: SecretProvider,
        *,
        transport: GitHubTransport | None = None,
    ) -> None:
        self._config = config
        self._secret_provider = secret_provider
        self._transport = transport or _urllib_transport

    def inspect_source_ref(self, repository: SourceRepositoryRef, ref: str) -> SourceRefSnapshot:
        self._require_repository(repository, "inspect_source_ref")
        self._require_allowed_ref(ref, "inspect_source_ref")
        headers = self._headers()
        snapshot = self._source_ref_snapshot(repository, ref, headers, "inspect_source_ref")
        if snapshot is None:
            raise _adapter_error("inspect_source_ref", "not_found", False, "source_ref_not_found")
        return snapshot

    def publish_pull_request_candidate(
        self,
        request: SourceCandidatePublicationRequest,
    ) -> SourceCandidatePublicationReceipt:
        self._require_publication_request(request, "publish_pull_request_candidate")
        headers = self._headers()
        existing = self._lookup_candidate_with_headers(request, headers, "publish_pull_request_candidate")
        if existing.status is SourceCandidatePublicationStatus.PUBLISHED:
            return existing
        if existing.status is SourceCandidatePublicationStatus.ABSENT:
            existing = self._create_candidate_branch(request, headers)
        if existing.status is SourceCandidatePublicationStatus.PARTIAL:
            return self._create_candidate_pull_request(request, existing, headers)
        return existing

    def lookup_pull_request_candidate(
        self,
        request: SourceCandidatePublicationRequest,
    ) -> SourceCandidatePublicationReceipt:
        self._require_publication_request(request, "lookup_pull_request_candidate")
        return self._lookup_candidate_with_headers(request, self._headers(), "lookup_pull_request_candidate")

    def find_pull_request(self, search: PullRequestSearch) -> PullRequestSnapshot:
        self._require_search(search)
        headers = self._headers()
        candidates = self._search_candidates(search, headers)
        if not candidates:
            raise _adapter_error("find_pull_request", "not_found", False, "pull_request_not_found")
        if len(candidates) != 1:
            raise _adapter_error("find_pull_request", "conflict", False, "multiple_pull_requests_found")
        return self._inspect_with_headers(candidates[0], headers)

    def inspect_pull_request(self, target: PullRequestTarget) -> PullRequestSnapshot:
        self._require_target(target)
        return self._inspect_with_headers(target, self._headers())

    def merge_pull_request(self, request: SourceControlMergeRequest) -> SourceControlMergeReceipt:
        self._require_target(request.target)
        if request.merge_method not in self._config.allowed_merge_methods:
            raise _policy_error(request, ("merge_method_not_allowed",))
        headers = self._headers()
        existing = self._lookup_with_headers(request.target, headers)
        if existing.status is SourceControlMergeStatus.LANDED:
            raise _policy_error(request, ("pull_request_already_merged_before_governed_dispatch",))
        snapshot = self._inspect_with_headers(request.target, headers)
        blockers = (*snapshot.blocking_reasons, *_approval_binding_reasons(request, snapshot))
        if blockers:
            raise _policy_error(request, blockers)
        return self._put_merge(request, snapshot, headers)

    def lookup_merge(self, target: PullRequestTarget) -> SourceControlMergeReceipt:
        self._require_target(target)
        return self._lookup_with_headers(target, self._headers())

    def failure_contract(self) -> AdapterFailureContract:
        modes = (
            AdapterFailureMode("inspect_source_ref", "not_found", False, "Exact GitHub source ref was not found."),
            AdapterFailureMode(
                "publish_pull_request_candidate",
                "conflict",
                False,
                "GitHub candidate identity conflicts with existing source state.",
                has_required_idempotency_key=True,
            ),
            AdapterFailureMode(
                "publish_pull_request_candidate",
                "timeout",
                True,
                "GitHub candidate publication outcome is unknown.",
                timeout_seconds=self._config.timeout_seconds,
                has_required_idempotency_key=True,
            ),
            AdapterFailureMode(
                "lookup_pull_request_candidate",
                "unavailable",
                True,
                "GitHub candidate lookup is unavailable.",
            ),
            AdapterFailureMode("find_pull_request", "not_found", False, "Exact GitHub pull request was not found."),
            AdapterFailureMode("inspect_pull_request", "conflict", False, "GitHub release target changed."),
            AdapterFailureMode(
                "merge_pull_request",
                "conflict",
                False,
                "GitHub merge preconditions failed.",
                has_required_idempotency_key=True,
            ),
            AdapterFailureMode(
                "merge_pull_request",
                "timeout",
                True,
                "GitHub merge outcome is unknown.",
                timeout_seconds=self._config.timeout_seconds,
                has_required_idempotency_key=True,
            ),
            AdapterFailureMode("lookup_merge", "unavailable", True, "GitHub merge lookup is unavailable."),
            AdapterFailureMode("inspect_pull_request", "authentication", False, "GitHub credential was rejected."),
            AdapterFailureMode("inspect_pull_request", "rate_limited", True, "GitHub API rate limit was reached."),
        )
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=modes)

    def _headers(self) -> Mapping[str, str]:
        secret = self._secret_provider.get_secret(self._config.installation_token_secret_ref)
        if not secret.value.strip():
            raise _adapter_error("authenticate", "authentication", False, "empty_installation_token")
        return {
            "accept": "application/vnd.github+json",
            "authorization": f"Bearer {secret.value}",
            "user-agent": "Foundry-lite/github-release",
            "x-github-api-version": _GITHUB_API_VERSION,
        }

    def _require_search(self, search: PullRequestSearch) -> None:
        self._require_repository(search.repository, "find_pull_request")
        self._require_base_ref(search.expected_base_ref, "find_pull_request")
        if not self._is_allowed_head_ref(search.expected_head_ref):
            raise _adapter_error("find_pull_request", "validation", False, "head_ref_not_allowed")

    def _require_publication_request(self, request: SourceCandidatePublicationRequest, operation: str) -> None:
        self._require_repository(request.repository, operation)
        self._require_base_ref(request.expected_base_ref, operation)
        if not self._is_allowed_head_ref(request.expected_head_ref):
            raise _adapter_error(operation, "validation", False, "head_ref_not_allowed")

    def _require_allowed_ref(self, ref: str, operation: str) -> None:
        if ref not in self._config.allowed_base_refs and not self._is_allowed_head_ref(ref):
            raise _adapter_error(operation, "validation", False, "source_ref_not_allowed")

    def _require_target(self, target: PullRequestTarget) -> None:
        self._require_repository(target.repository, "target_validation")
        self._require_base_ref(target.expected_base_ref, "target_validation")
        binding = target.candidate_binding
        if binding is not None and not self._is_allowed_head_ref(binding.expected_head_ref):
            raise _adapter_error("target_validation", "validation", False, "candidate_head_ref_not_allowed")

    def _require_repository(self, repository: SourceRepositoryRef, operation: str) -> None:
        expected = self._config.repository
        if repository != expected:
            raise _adapter_error(operation, "validation", False, "repository_binding_mismatch")

    def _require_base_ref(self, base_ref: str, operation: str) -> None:
        if base_ref not in self._config.allowed_base_refs:
            raise _adapter_error(operation, "validation", False, "base_ref_not_allowed")

    def _is_allowed_head_ref(self, head_ref: str) -> bool:
        return _is_safe_git_ref(head_ref) and any(
            head_ref.startswith(prefix) for prefix in self._config.allowed_head_ref_prefixes
        )

    def _source_ref_snapshot(
        self,
        repository: SourceRepositoryRef,
        ref: str,
        headers: Mapping[str, str],
        operation: str,
    ) -> SourceRefSnapshot | None:
        path = f"{self._repo_path()}/git/ref/heads/{quote(ref, safe='')}"
        response = self._get_optional(path, {}, headers, operation)
        if response is None:
            return None
        row = _mapping(response.body)
        target = _mapping(row.get("object"))
        if _text(row.get("ref")) != f"refs/heads/{ref}" or _text(target.get("type")) != "commit":
            raise _adapter_error(operation, "conflict", False, "source_ref_binding_invalid")
        commit = self._git_commit(_require_sha(target.get("sha"), "source_ref_sha"), headers, operation)
        return SourceRefSnapshot(repository, ref, commit.sha, commit.tree_sha)

    def _git_commit(
        self,
        commit_sha: str,
        headers: Mapping[str, str],
        operation: str,
    ) -> _GitCommitRecord:
        response = self._get(f"{self._repo_path()}/git/commits/{commit_sha}", {}, headers, operation)
        record = _parse_git_commit(response.body)
        if record.sha != commit_sha:
            raise _adapter_error(operation, "conflict", False, "git_commit_binding_changed")
        return record

    def _lookup_candidate_with_headers(
        self,
        request: SourceCandidatePublicationRequest,
        headers: Mapping[str, str],
        operation: str,
    ) -> SourceCandidatePublicationReceipt:
        snapshot = self._source_ref_snapshot(request.repository, request.expected_head_ref, headers, operation)
        if snapshot is None:
            return _candidate_receipt(request, SourceCandidatePublicationStatus.ABSENT, reason="not_published")
        binding = self._verified_candidate_binding(request, snapshot, headers, operation)
        targets = self._candidate_pull_targets(request, headers, operation)
        if len(targets) > 1:
            raise _adapter_error(operation, "conflict", False, "multiple_candidate_pull_requests")
        if not targets:
            return _candidate_receipt(
                request,
                SourceCandidatePublicationStatus.PARTIAL,
                head_sha=snapshot.commit_sha,
                binding=binding,
                reason="pull_request_not_published",
            )
        target = targets[0]
        if target.expected_head_sha != snapshot.commit_sha:
            raise _adapter_error(operation, "conflict", False, "candidate_pull_request_head_changed")
        return _candidate_receipt(
            request,
            SourceCandidatePublicationStatus.PUBLISHED,
            head_sha=snapshot.commit_sha,
            pull_number=target.pull_number,
            binding=binding,
            reason="published",
        )

    def _candidate_pull_targets(
        self,
        request: SourceCandidatePublicationRequest,
        headers: Mapping[str, str],
        operation: str,
    ) -> tuple[PullRequestTarget, ...]:
        search = PullRequestSearch(request.repository, request.expected_base_ref, request.expected_head_ref)
        query = {
            "state": "all",
            "base": request.expected_base_ref,
            "head": f"{self._config.repository.owner}:{request.expected_head_ref}",
        }
        rows = self._paged_list(self._pulls_path(), query, headers, operation)
        targets = tuple(self._publication_candidate_target(row, search, operation) for row in rows)
        return tuple(target for target in targets if target is not None)

    def _publication_candidate_target(
        self,
        value: object,
        search: PullRequestSearch,
        operation: str,
    ) -> PullRequestTarget | None:
        row = _mapping(value)
        base = _mapping(row.get("base"))
        head = _mapping(row.get("head"))
        repository_id = self._config.repository.repository_id
        if not _search_side_matches(base, search.expected_base_ref, repository_id):
            return None
        if not _search_side_matches(head, search.expected_head_ref, repository_id):
            return None
        number = _positive_int(row.get("number"))
        head_sha = _full_sha(head.get("sha"))
        if number is None or head_sha is None:
            raise _adapter_error(operation, "validation", False, "candidate_pull_request_identity_invalid")
        return PullRequestTarget(search.repository, number, search.expected_base_ref, head_sha)

    def _verified_candidate_binding(
        self,
        request: SourceCandidatePublicationRequest,
        snapshot: SourceRefSnapshot,
        headers: Mapping[str, str],
        operation: str,
    ) -> SourceCandidateCommitBinding:
        commit = self._git_commit(snapshot.commit_sha, headers, operation)
        if commit.tree_sha != snapshot.tree_sha:
            raise _adapter_error(operation, "conflict", False, "candidate_tree_binding_changed")
        if commit.parent_shas != (request.expected_base_sha,):
            raise _adapter_error(operation, "conflict", False, "candidate_parent_binding_changed")
        compare_blob_sha = self._verify_candidate_compare(request, snapshot.commit_sha, headers, operation)
        tree_blob_sha, observed = self._candidate_manifest_bytes(request.manifest, commit.tree_sha, headers, operation)
        if compare_blob_sha != tree_blob_sha or observed != request.manifest.canonical_bytes:
            raise _adapter_error(operation, "conflict", False, "candidate_manifest_bytes_changed")
        return SourceCandidateCommitBinding(
            request.expected_base_sha,
            commit.tree_sha,
            request.expected_head_ref,
            request.manifest,
        )

    def _verify_target_candidate_binding(
        self,
        target: PullRequestTarget,
        headers: Mapping[str, str],
        operation: str,
    ) -> None:
        binding = target.candidate_binding
        if binding is None:
            return
        release_kind, proposal_id = _candidate_manifest_identity(binding.manifest.artifact_path)
        request = SourceCandidatePublicationRequest(
            target.repository,
            release_kind,
            proposal_id,
            target.expected_base_ref,
            binding.expected_head_ref,
            binding.expected_base_sha,
            binding.manifest,
            "merge-time-candidate-readback",
        )
        snapshot = SourceRefSnapshot(
            target.repository,
            binding.expected_head_ref,
            target.expected_head_sha,
            binding.expected_tree_sha,
        )
        observed = self._verified_candidate_binding(request, snapshot, headers, operation)
        if observed != binding:
            raise _adapter_error(operation, "conflict", False, "candidate_commit_binding_changed")

    def _verify_candidate_compare(
        self,
        request: SourceCandidatePublicationRequest,
        head_sha: str,
        headers: Mapping[str, str],
        operation: str,
    ) -> str:
        path = f"{self._repo_path()}/compare/{request.expected_base_sha}...{head_sha}"
        response = self._get(path, {}, headers, operation)
        blob_sha = _exact_manifest_only_compare_blob_sha(response.body, request, head_sha)
        if blob_sha is None:
            raise _adapter_error(operation, "conflict", False, "candidate_commit_not_manifest_only")
        return blob_sha

    def _candidate_manifest_bytes(
        self,
        manifest: SourceCandidateManifest,
        tree_sha: str,
        headers: Mapping[str, str],
        operation: str,
    ) -> tuple[str, bytes]:
        path = f"{self._repo_path()}/git/trees/{tree_sha}"
        response = self._get(path, {"recursive": "1"}, headers, operation)
        blob_sha = _candidate_tree_blob_sha(response.body, tree_sha, manifest.artifact_path, operation)
        blob = self._get(f"{self._repo_path()}/git/blobs/{blob_sha}", {}, headers, operation)
        return blob_sha, _decode_candidate_blob(blob.body, blob_sha, operation)

    def _create_candidate_branch(
        self,
        request: SourceCandidatePublicationRequest,
        headers: Mapping[str, str],
    ) -> SourceCandidatePublicationReceipt:
        operation = "publish_pull_request_candidate"
        base = self._source_ref_snapshot(request.repository, request.expected_base_ref, headers, operation)
        if base is None or base.commit_sha != request.expected_base_sha:
            raise _adapter_error(operation, "conflict", False, "candidate_base_ref_changed")
        self._require_manifest_absent_from_base(request, base.tree_sha, headers)
        blob_sha = self._create_manifest_blob(request, headers)
        tree_sha = self._create_manifest_tree(request, base.tree_sha, blob_sha, headers)
        commit_sha = self._create_manifest_commit(request, tree_sha, headers)
        return self._create_candidate_ref(request, commit_sha, headers)

    def _require_manifest_absent_from_base(
        self,
        request: SourceCandidatePublicationRequest,
        base_tree_sha: str,
        headers: Mapping[str, str],
    ) -> None:
        operation = "publish_pull_request_candidate"
        path = f"{self._repo_path()}/git/trees/{base_tree_sha}"
        response = self._get(path, {"recursive": "1"}, headers, operation)
        if _tree_contains_path(response.body, base_tree_sha, request.manifest.artifact_path, operation):
            raise _adapter_error(operation, "conflict", False, "candidate_manifest_already_exists_on_base")

    def _create_manifest_blob(
        self,
        request: SourceCandidatePublicationRequest,
        headers: Mapping[str, str],
    ) -> str:
        body = {
            "content": base64.b64encode(request.manifest.canonical_bytes).decode("ascii"),
            "encoding": "base64",
        }
        response = self._post_required(f"{self._repo_path()}/git/blobs", body, headers, request.idempotency_key)
        return _require_sha(_mapping(response.body).get("sha"), "candidate_blob_sha")

    def _create_manifest_tree(
        self,
        request: SourceCandidatePublicationRequest,
        base_tree_sha: str,
        blob_sha: str,
        headers: Mapping[str, str],
    ) -> str:
        body = {
            "base_tree": base_tree_sha,
            "tree": [{"path": request.manifest.artifact_path, "mode": "100644", "type": "blob", "sha": blob_sha}],
        }
        response = self._post_required(f"{self._repo_path()}/git/trees", body, headers, request.idempotency_key)
        return _require_sha(_mapping(response.body).get("sha"), "candidate_tree_sha")

    def _create_manifest_commit(
        self,
        request: SourceCandidatePublicationRequest,
        tree_sha: str,
        headers: Mapping[str, str],
    ) -> str:
        body = {
            "message": f"Foundry-lite governed {request.release_kind} candidate {request.proposal_id}",
            "tree": tree_sha,
            "parents": [request.expected_base_sha],
        }
        response = self._post_required(f"{self._repo_path()}/git/commits", body, headers, request.idempotency_key)
        return _require_sha(_mapping(response.body).get("sha"), "candidate_commit_sha")

    def _create_candidate_ref(
        self,
        request: SourceCandidatePublicationRequest,
        commit_sha: str,
        headers: Mapping[str, str],
    ) -> SourceCandidatePublicationReceipt:
        body = {"ref": f"refs/heads/{request.expected_head_ref}", "sha": commit_sha}
        path = f"{self._repo_path()}/git/refs"
        response = self._post_mutation(path, body, headers)
        return self._reconcile_candidate_mutation(request, response, headers, "ref_create_outcome_unknown")

    def _create_candidate_pull_request(
        self,
        request: SourceCandidatePublicationRequest,
        partial: SourceCandidatePublicationReceipt,
        headers: Mapping[str, str],
    ) -> SourceCandidatePublicationReceipt:
        if partial.status is not SourceCandidatePublicationStatus.PARTIAL:
            raise _adapter_error("publish_pull_request_candidate", "conflict", False, "candidate_branch_not_verified")
        body = {
            "title": f"Governed {request.release_kind} release {request.proposal_id}",
            "body": _candidate_pull_body(request),
            "head": request.expected_head_ref,
            "base": request.expected_base_ref,
            "draft": False,
        }
        response = self._post_mutation(self._pulls_path(), body, headers)
        return self._reconcile_candidate_mutation(request, response, headers, "pull_create_outcome_unknown")

    def _post_required(
        self,
        path: str,
        body: Mapping[str, object],
        headers: Mapping[str, str],
        idempotency_key: str,
    ) -> GitHubHttpResponse:
        operation = "publish_pull_request_candidate"
        request = self._http_request("POST", path, {}, headers, body)
        try:
            response = self._call_transport(request)
        except GitHubTransportFailure as exc:
            raise _transport_error(operation, exc, idempotency_key, self._config.timeout_seconds) from exc
        if response.status_code != 201:
            raise _http_error(operation, response, idempotency_key)
        return response

    def _post_mutation(
        self,
        path: str,
        body: Mapping[str, object],
        headers: Mapping[str, str],
    ) -> GitHubHttpResponse | None:
        request = self._http_request("POST", path, {}, headers, body)
        try:
            return self._call_transport(request)
        except GitHubTransportFailure:
            return None

    def _reconcile_candidate_mutation(
        self,
        request: SourceCandidatePublicationRequest,
        response: GitHubHttpResponse | None,
        headers: Mapping[str, str],
        reason: str,
    ) -> SourceCandidatePublicationReceipt:
        _require_candidate_mutation_response(request, response)
        receipt = self._lookup_candidate_after_mutation(request, headers)
        if receipt is not None and _is_reconciled_candidate_mutation(receipt, response, reason):
            return _with_candidate_request_id(receipt, response)
        return _ambiguous_candidate_mutation_receipt(request, response, reason)

    def _lookup_candidate_after_mutation(
        self,
        request: SourceCandidatePublicationRequest,
        headers: Mapping[str, str],
    ) -> SourceCandidatePublicationReceipt | None:
        try:
            return self._lookup_candidate_with_headers(request, headers, "publish_pull_request_candidate")
        except AdapterError:
            return None

    def _search_candidates(
        self,
        search: PullRequestSearch,
        headers: Mapping[str, str],
    ) -> tuple[PullRequestTarget, ...]:
        query = {
            "state": "open",
            "base": search.expected_base_ref,
            "head": f"{self._config.repository.owner}:{search.expected_head_ref}",
        }
        rows = self._paged_list(self._pulls_path(), query, headers, "find_pull_request")
        targets = tuple(self._candidate_target(row, search) for row in rows)
        return tuple(target for target in targets if target is not None)

    def _candidate_target(
        self,
        value: object,
        search: PullRequestSearch,
    ) -> PullRequestTarget | None:
        row = _mapping(value)
        base = _mapping(row.get("base"))
        head = _mapping(row.get("head"))
        if not _search_side_matches(base, search.expected_base_ref, self._config.repository.repository_id):
            return None
        if not _search_side_matches(head, search.expected_head_ref, self._config.repository.repository_id):
            return None
        number = _positive_int(row.get("number"))
        head_sha = _full_sha(head.get("sha"))
        if number is None or head_sha is None:
            return None
        return PullRequestTarget(search.repository, number, search.expected_base_ref, head_sha)

    def _inspect_with_headers(
        self,
        target: PullRequestTarget,
        headers: Mapping[str, str],
    ) -> PullRequestSnapshot:
        record, request_id = self._pull_request(target, headers, "inspect_pull_request")
        self._require_record_binding(record, target, "inspect_pull_request")
        self._verify_target_candidate_binding(target, headers, "inspect_pull_request")
        rule_summary = self._rule_summary(target, headers)
        approvals, review_decision = self._review_evidence(record, target, rule_summary, headers)
        checks, checks_commit_sha = self._check_evidence(record, target, rule_summary.required_checks, headers)
        blockers = _blocking_reasons(record, rule_summary, review_decision, checks)
        return PullRequestSnapshot(
            target=target,
            state=record.state,
            is_draft=record.is_draft,
            is_merged=record.is_merged,
            base_sha=record.base_sha,
            head_ref=record.head_ref,
            author_id=record.author_id,
            author_login=record.author_login,
            mergeable_state=record.mergeable_state,
            test_merge_commit_sha=record.merge_commit_sha,
            checks_commit_sha=checks_commit_sha,
            review_decision=review_decision,
            required_approval_count=rule_summary.required_approvals,
            approvals=approvals,
            active_rules=rule_summary.evidence,
            required_checks=checks,
            is_merge_queue_required=rule_summary.is_merge_queue_required,
            rules_fingerprint=rule_summary.fingerprint,
            checks_fingerprint=_checks_fingerprint(checks),
            blocking_reasons=blockers,
            is_ready_to_merge=not blockers,
            provider_request_id=request_id,
        )

    def _lookup_with_headers(
        self,
        target: PullRequestTarget,
        headers: Mapping[str, str],
    ) -> SourceControlMergeReceipt:
        self._verify_target_candidate_binding(target, headers, "lookup_merge")
        record, request_id = self._pull_request(target, headers, "lookup_merge")
        self._require_record_binding(record, target, "lookup_merge")
        if not record.is_merged:
            return _receipt(target, SourceControlMergeStatus.ABSENT, request_id=request_id)
        if _full_sha(record.merge_commit_sha) is None:
            raise _adapter_error("lookup_merge", "validation", False, "merge_commit_sha_missing")
        return _receipt(
            target,
            SourceControlMergeStatus.LANDED,
            merge_commit_sha=record.merge_commit_sha,
            merged_at=record.merged_at,
            request_id=request_id,
        )

    def _pull_request(
        self,
        target: PullRequestTarget,
        headers: Mapping[str, str],
        operation: str,
    ) -> tuple[_PullRequestRecord, str | None]:
        response = self._get(self._pull_path(target.pull_number), {}, headers, operation)
        return _parse_pull_request(response.body), _request_id(response.headers)

    def _require_record_binding(
        self,
        record: _PullRequestRecord,
        target: PullRequestTarget,
        operation: str,
    ) -> None:
        repository_id = self._config.repository.repository_id
        bindings_match = (
            record.number == target.pull_number
            and record.base_repository_id == repository_id
            and record.head_repository_id == repository_id
            and record.base_ref == target.expected_base_ref
            and record.head_sha == target.expected_head_sha
            and self._is_allowed_head_ref(record.head_ref)
            and (target.candidate_binding is None or record.head_ref == target.candidate_binding.expected_head_ref)
        )
        if not bindings_match:
            raise _adapter_error(operation, "conflict", False, "pull_request_binding_changed")

    def _rule_summary(self, target: PullRequestTarget, headers: Mapping[str, str]) -> _RuleSummary:
        path = f"{self._repo_path()}/rules/branches/{quote(target.expected_base_ref, safe='')}"
        rows = self._paged_list(path, {}, headers, "inspect_pull_request")
        classic = self._classic_branch_protection(target, headers)
        summary = _combined_rule_summary(self._config, rows, classic)
        return replace(summary, fingerprint=_rules_fingerprint(summary))

    def _classic_branch_protection(
        self,
        target: PullRequestTarget,
        headers: Mapping[str, str],
    ) -> _ClassicProtectionSummary:
        branch = quote(target.expected_base_ref, safe="")
        request = self._http_request("GET", f"{self._repo_path()}/branches/{branch}/protection", {}, headers, None)
        try:
            response = self._call_transport(request)
        except GitHubTransportFailure as exc:
            raise _transport_error(
                "inspect_pull_request",
                exc,
                timeout_seconds=self._config.timeout_seconds,
            ) from exc
        if response.status_code == 404:
            return _empty_classic_protection()
        if not 200 <= response.status_code < 300:
            raise _http_error("inspect_pull_request", response)
        return _classic_protection_summary(response.body, self._config.repository)

    def _review_evidence(
        self,
        record: _PullRequestRecord,
        target: PullRequestTarget,
        rules: _RuleSummary,
        headers: Mapping[str, str],
    ) -> tuple[tuple[PullRequestReviewEvidence, ...], SourceControlReviewDecision]:
        path = f"{self._pull_path(target.pull_number)}/reviews"
        rows = self._paged_list(path, {}, headers, "inspect_pull_request")
        current = self._eligible_current_reviews(rows, record.author_id, headers)
        approvals = tuple(
            review for review in current if review.state == "APPROVED" and review.commit_sha == target.expected_head_sha
        )
        has_changes_requested = any(review.state == "CHANGES_REQUESTED" for review in current)
        if has_changes_requested:
            decision = SourceControlReviewDecision.CHANGES_REQUESTED
        elif len(approvals) >= rules.required_approvals:
            decision = SourceControlReviewDecision.APPROVED
        else:
            decision = SourceControlReviewDecision.REVIEW_REQUIRED
        return approvals, decision

    def _eligible_current_reviews(
        self,
        rows: tuple[object, ...],
        author_id: int,
        headers: Mapping[str, str],
    ) -> tuple[PullRequestReviewEvidence, ...]:
        current = tuple(review for review in _current_reviews(rows, author_id) if review.state != "DISMISSED")
        if len(current) > self._config.max_reviewers:
            raise _adapter_error("inspect_pull_request", "unsupported", False, "reviewer_limit_exceeded")
        return tuple(review for review in current if self._reviewer_has_write_access(review, headers))

    def _reviewer_has_write_access(
        self,
        review: PullRequestReviewEvidence,
        headers: Mapping[str, str],
    ) -> bool:
        login = quote(review.reviewer_login, safe="")
        path = f"{self._repo_path()}/collaborators/{login}/permission"
        try:
            response = self._get(path, {}, headers, "inspect_pull_request")
        except AdapterError as exc:
            if exc.failure.kind == "not_found":
                return False
            raise
        permission = _require_text(_mapping(response.body).get("permission"), "reviewer_permission").lower()
        return permission in {"write", "admin"}

    def _check_evidence(
        self,
        record: _PullRequestRecord,
        target: PullRequestTarget,
        policies: tuple[_RequiredCheckPolicy, ...],
        headers: Mapping[str, str],
    ) -> tuple[tuple[RequiredCheckEvidence, ...], str]:
        if not policies:
            return (), target.expected_head_sha
        commit_sha, check_runs, statuses = self._required_check_inputs(record, target, headers)
        evidence = tuple(
            evidence for policy in policies for evidence in _checks_for_policy(policy, commit_sha, check_runs, statuses)
        )
        return evidence, commit_sha

    def _required_check_inputs(
        self,
        record: _PullRequestRecord,
        target: PullRequestTarget,
        headers: Mapping[str, str],
    ) -> tuple[str, tuple[object, ...], tuple[object, ...]]:
        merge_sha = record.merge_commit_sha
        if merge_sha is not None:
            merge_runs = self._paged_check_runs(merge_sha, headers)
            merge_statuses = self._paged_statuses(merge_sha, headers)
            if merge_runs or merge_statuses:
                return merge_sha, merge_runs, merge_statuses
        head_sha = target.expected_head_sha
        return head_sha, self._paged_check_runs(head_sha, headers), self._paged_statuses(head_sha, headers)

    def _paged_statuses(self, commit_sha: str, headers: Mapping[str, str]) -> tuple[object, ...]:
        path = f"{self._repo_path()}/commits/{commit_sha}/statuses"
        return self._paged_list(path, {}, headers, "inspect_pull_request")

    def _paged_check_runs(self, head_sha: str, headers: Mapping[str, str]) -> tuple[object, ...]:
        path = f"{self._repo_path()}/commits/{head_sha}/check-runs"
        items: list[object] = []
        for page in range(1, self._config.max_pages + 1):
            response = self._get(
                path,
                {"filter": "latest", "per_page": _PAGE_SIZE, "page": page},
                headers,
                "inspect_pull_request",
            )
            rows = _sequence(_mapping(response.body).get("check_runs"))
            items.extend(rows)
            if len(rows) < _PAGE_SIZE:
                return tuple(items)
        raise _adapter_error("inspect_pull_request", "unsupported", False, "check_run_page_limit_exceeded")

    def _paged_list(
        self,
        path: str,
        query: Mapping[str, object],
        headers: Mapping[str, str],
        operation: str,
    ) -> tuple[object, ...]:
        items: list[object] = []
        for page in range(1, self._config.max_pages + 1):
            page_query = {**query, "per_page": _PAGE_SIZE, "page": page}
            rows = _sequence(self._get(path, page_query, headers, operation).body)
            items.extend(rows)
            if len(rows) < _PAGE_SIZE:
                return tuple(items)
        raise _adapter_error(operation, "unsupported", False, "github_page_limit_exceeded")

    def _put_merge(
        self,
        request: SourceControlMergeRequest,
        snapshot: PullRequestSnapshot,
        headers: Mapping[str, str],
    ) -> SourceControlMergeReceipt:
        http_request = self._http_request(
            "PUT",
            f"{self._pull_path(request.target.pull_number)}/merge",
            {},
            headers,
            {"sha": request.target.expected_head_sha, "merge_method": request.merge_method.value},
        )
        try:
            response = self._call_transport(http_request)
        except GitHubTransportFailure:
            return _ambiguous_receipt(request, None, "merge_transport_outcome_unknown")
        if response.status_code in _MERGE_OUTCOME_UNKNOWN_HTTP_STATUSES:
            return _ambiguous_receipt(request, _request_id(response.headers), "merge_conflict_outcome_unknown")
        if not 400 <= response.status_code < 500 and response.status_code != 200:
            return _ambiguous_receipt(request, _request_id(response.headers), "merge_http_outcome_unknown")
        if response.status_code != 200:
            raise _http_error("merge_pull_request", response, request.idempotency_key)
        if not isinstance(response.body, Mapping):
            return _ambiguous_receipt(request, _request_id(response.headers), "merge_response_not_authoritative")
        body = cast(Mapping[str, object], response.body)
        merge_sha = _full_sha(body.get("sha"))
        if body.get("merged") is not True or merge_sha is None:
            return _ambiguous_receipt(request, _request_id(response.headers), "merge_response_not_authoritative")
        return _receipt(
            request.target,
            SourceControlMergeStatus.LANDED,
            merge_commit_sha=merge_sha,
            request_id=_request_id(response.headers),
            idempotency_key=request.idempotency_key,
            evidence={
                "provider": "github",
                "baseRef": request.target.expected_base_ref,
                "baseSha": snapshot.base_sha,
                "rulesFingerprint": snapshot.rules_fingerprint,
                "checksFingerprint": snapshot.checks_fingerprint,
                "testMergeCommitSha": snapshot.test_merge_commit_sha,
                "checksCommitSha": snapshot.checks_commit_sha,
            },
        )

    def _get(
        self,
        path: str,
        query: Mapping[str, object],
        headers: Mapping[str, str],
        operation: str,
    ) -> GitHubHttpResponse:
        request = self._http_request("GET", path, query, headers, None)
        try:
            response = self._call_transport(request)
        except GitHubTransportFailure as exc:
            raise _transport_error(operation, exc, timeout_seconds=self._config.timeout_seconds) from exc
        if not 200 <= response.status_code < 300:
            raise _http_error(operation, response)
        return response

    def _get_optional(
        self,
        path: str,
        query: Mapping[str, object],
        headers: Mapping[str, str],
        operation: str,
    ) -> GitHubHttpResponse | None:
        request = self._http_request("GET", path, query, headers, None)
        try:
            response = self._call_transport(request)
        except GitHubTransportFailure as exc:
            raise _transport_error(operation, exc, timeout_seconds=self._config.timeout_seconds) from exc
        if response.status_code == 404:
            return None
        if not 200 <= response.status_code < 300:
            raise _http_error(operation, response)
        return response

    def _http_request(
        self,
        method: Literal["GET", "POST", "PUT"],
        path: str,
        query: Mapping[str, object],
        headers: Mapping[str, str],
        body: Mapping[str, object] | None,
    ) -> GitHubHttpRequest:
        query_text = urlencode(query) if query else ""
        url = f"{_GITHUB_API_ROOT}{path}{'?' + query_text if query_text else ''}"
        return GitHubHttpRequest(method, url, headers, body, float(self._config.timeout_seconds))

    def _call_transport(self, request: GitHubHttpRequest) -> GitHubHttpResponse:
        return self._transport(request)

    def _repo_path(self) -> str:
        repository = self._config.repository
        return f"/repos/{quote(repository.owner, safe='')}/{quote(repository.name, safe='')}"

    def _pulls_path(self) -> str:
        return f"{self._repo_path()}/pulls"

    def _pull_path(self, pull_number: int) -> str:
        return f"{self._pulls_path()}/{pull_number}"


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


def _urllib_transport(request: GitHubHttpRequest) -> GitHubHttpResponse:
    body = None if request.body is None else json.dumps(request.body, separators=(",", ":")).encode("utf-8")
    raw_request = Request(request.url, data=body, headers=dict(request.headers), method=request.method)
    opener = build_opener(_NoRedirectHandler())
    try:
        with opener.open(raw_request, timeout=request.timeout_seconds) as response:  # nosec B310 - fixed host.
            return GitHubHttpResponse(
                int(response.status),
                _normalized_headers(response.headers),
                _response_json(response),
            )
    except HTTPError as exc:
        return GitHubHttpResponse(exc.code, _normalized_headers(exc.headers), _response_json(exc))
    except TimeoutError as exc:
        raise GitHubTransportFailure("timeout") from exc
    except URLError as exc:
        kind: AdapterFailureKind = "timeout" if isinstance(exc.reason, TimeoutError) else "unavailable"
        raise GitHubTransportFailure(kind) from exc
    except OSError as exc:
        raise GitHubTransportFailure("unavailable") from exc


def _response_json(response: object) -> object:
    read = getattr(response, "read", None)
    if not callable(read):
        raise GitHubTransportFailure("validation")
    raw = read(_MAX_RESPONSE_BYTES + 1)
    if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
        raise GitHubTransportFailure("validation")
    try:
        return (
            json.loads(
                raw.decode("utf-8"),
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid JSON constant {value}")),
            )
            if raw
            else {}
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise GitHubTransportFailure("validation") from exc


def _normalized_headers(headers: object) -> Mapping[str, str]:
    items = getattr(headers, "items", None)
    if not callable(items):
        return {}
    pairs = cast(Iterable[tuple[object, object]], items())
    return {str(key).lower(): str(value) for key, value in pairs}


def _parse_git_commit(value: object) -> _GitCommitRecord:
    row = _mapping(value)
    tree = _mapping(row.get("tree"))
    parents = _sequence(row.get("parents"))
    return _GitCommitRecord(
        sha=_require_sha(row.get("sha"), "git_commit_sha"),
        tree_sha=_require_sha(tree.get("sha"), "git_commit_tree_sha"),
        parent_shas=tuple(_require_sha(_mapping(parent).get("sha"), "git_commit_parent_sha") for parent in parents),
    )


def _require_candidate_mutation_response(
    request: SourceCandidatePublicationRequest,
    response: GitHubHttpResponse | None,
) -> None:
    if response is None or response.status_code in {201, 409, 422} or response.status_code >= 500:
        return
    raise _http_error("publish_pull_request_candidate", response, request.idempotency_key)


def _is_reconciled_candidate_mutation(
    receipt: SourceCandidatePublicationReceipt,
    response: GitHubHttpResponse | None,
    reason: str,
) -> bool:
    is_created = (
        response is not None
        and response.status_code == 201
        and receipt.status is not SourceCandidatePublicationStatus.ABSENT
    )
    is_published = receipt.status is SourceCandidatePublicationStatus.PUBLISHED
    is_verified_branch = receipt.status is SourceCandidatePublicationStatus.PARTIAL and "pull_create" not in reason
    return is_created or is_published or is_verified_branch


def _ambiguous_candidate_mutation_receipt(
    request: SourceCandidatePublicationRequest,
    response: GitHubHttpResponse | None,
    reason: str,
) -> SourceCandidatePublicationReceipt:
    return _candidate_receipt(
        request,
        SourceCandidatePublicationStatus.AMBIGUOUS,
        reason=reason,
        request_id=_candidate_mutation_request_id(response),
    )


def _candidate_receipt(
    request: SourceCandidatePublicationRequest,
    status: SourceCandidatePublicationStatus,
    *,
    head_sha: str | None = None,
    pull_number: int | None = None,
    binding: SourceCandidateCommitBinding | None = None,
    reason: str,
    request_id: str | None = None,
) -> SourceCandidatePublicationReceipt:
    evidence: dict[str, object] = {
        "provider": "github",
        "reason": reason,
        "manifestFingerprint": request.manifest.manifest_fingerprint,
        "exactBindingVerified": binding is not None,
    }
    if binding is not None:
        evidence["treeSha"] = binding.expected_tree_sha
    return SourceCandidatePublicationReceipt(
        status=status,
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
        provider_request_id=request_id,
        evidence=evidence,
    )


def _with_candidate_request_id(
    receipt: SourceCandidatePublicationReceipt,
    response: GitHubHttpResponse | None,
) -> SourceCandidatePublicationReceipt:
    request_id = _candidate_mutation_request_id(response)
    return receipt if request_id is None else replace(receipt, provider_request_id=request_id)


def _candidate_mutation_request_id(response: GitHubHttpResponse | None) -> str | None:
    return None if response is None else _request_id(response.headers)


def _candidate_manifest_identity(artifact_path: str) -> tuple[str, str]:
    parts = artifact_path.split("/")
    if len(parts) != 4 or not parts[3].endswith(".json"):
        raise _adapter_error("inspect_pull_request", "validation", False, "candidate_manifest_path_invalid")
    return parts[2], parts[3].removesuffix(".json")


def _exact_manifest_only_compare_blob_sha(
    value: object,
    request: SourceCandidatePublicationRequest,
    head_sha: str,
) -> str | None:
    row = _mapping(value)
    base = _mapping(row.get("base_commit"))
    merge_base = _mapping(row.get("merge_base_commit"))
    commits = _sequence(row.get("commits"))
    files = _sequence(row.get("files"))
    identity_matches = (
        _text(base.get("sha")) == request.expected_base_sha
        and _text(merge_base.get("sha")) == request.expected_base_sha
        and _text(row.get("status")) == "ahead"
        and _exact_int(row.get("ahead_by"), 1)
        and _exact_int(row.get("behind_by"), 0)
        and _exact_int(row.get("total_commits"), 1)
    )
    commit_matches = len(commits) == 1 and _text(_mapping(commits[0]).get("sha")) == head_sha
    if not identity_matches or not commit_matches:
        return None
    return _exact_manifest_file_blob_sha(files, request.manifest.artifact_path)


def _exact_manifest_file_blob_sha(files: tuple[object, ...], artifact_path: str) -> str | None:
    if len(files) != 1:
        return None
    row = _mapping(files[0])
    identity_matches = (
        _text(row.get("filename")) == artifact_path
        and _text(row.get("status")) == "added"
        and row.get("previous_filename") is None
    )
    return _full_sha(row.get("sha")) if identity_matches else None


def _candidate_tree_blob_sha(
    value: object,
    expected_tree_sha: str,
    artifact_path: str,
    operation: str,
) -> str:
    rows = _verified_tree_rows(value, expected_tree_sha, operation)
    matches = tuple(_mapping(item) for item in rows if _text(_mapping(item).get("path")) == artifact_path)
    if len(matches) != 1:
        raise _adapter_error(operation, "conflict", False, "candidate_manifest_tree_entry_missing")
    row = matches[0]
    size = row.get("size")
    is_bounded = isinstance(size, int) and not isinstance(size, bool) and 0 < size <= _MAX_MANIFEST_BYTES
    if _text(row.get("type")) != "blob" or _text(row.get("mode")) != "100644" or not is_bounded:
        raise _adapter_error(operation, "conflict", False, "candidate_manifest_tree_entry_invalid")
    return _require_sha(row.get("sha"), "candidate_manifest_blob_sha")


def _tree_contains_path(value: object, expected_tree_sha: str, artifact_path: str, operation: str) -> bool:
    rows = _verified_tree_rows(value, expected_tree_sha, operation)
    return any(_text(_mapping(item).get("path")) == artifact_path for item in rows)


def _verified_tree_rows(value: object, expected_tree_sha: str, operation: str) -> tuple[object, ...]:
    row = _mapping(value)
    if _text(row.get("sha")) != expected_tree_sha or row.get("truncated") is not False:
        raise _adapter_error(operation, "unsupported", False, "candidate_tree_readback_incomplete")
    return _sequence(row.get("tree"))


def _decode_candidate_blob(value: object, expected_blob_sha: str, operation: str) -> bytes:
    row = _mapping(value)
    size = row.get("size")
    content = row.get("content")
    valid_metadata = (
        _text(row.get("sha")) == expected_blob_sha
        and _text(row.get("encoding")) == "base64"
        and isinstance(size, int)
        and not isinstance(size, bool)
        and 0 < size <= _MAX_MANIFEST_BYTES
        and isinstance(content, str)
    )
    if not valid_metadata:
        raise _adapter_error(operation, "conflict", False, "candidate_manifest_blob_invalid")
    try:
        decoded = base64.b64decode("".join(cast(str, content).split()), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise _adapter_error(operation, "conflict", False, "candidate_manifest_blob_invalid") from exc
    if len(decoded) != size or _git_blob_sha(decoded) != expected_blob_sha:
        raise _adapter_error(operation, "conflict", False, "candidate_manifest_blob_binding_changed")
    return decoded


def _git_blob_sha(value: bytes) -> str:
    header = f"blob {len(value)}\0".encode("ascii")
    return hashlib.sha1(header + value, usedforsecurity=False).hexdigest()


def _candidate_pull_body(request: SourceCandidatePublicationRequest) -> str:
    return (
        "Automated Foundry-lite governed release candidate.\n\n"
        f"Proposal: `{request.proposal_id}`\n"
        f"Manifest: `{request.manifest.artifact_path}`\n"
        f"Manifest fingerprint: `{request.manifest.manifest_fingerprint}`\n\n"
        "The release service will re-read the exact commit, tree, and manifest bytes before merge."
    )


def _exact_int(value: object, expected: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == expected


def _parse_pull_request(value: object) -> _PullRequestRecord:
    row = _mapping(value)
    base = _mapping(row.get("base"))
    head = _mapping(row.get("head"))
    author = _mapping(row.get("user"))
    record = _PullRequestRecord(
        number=_require_positive_int(row.get("number"), "pull_number"),
        state=_require_text(row.get("state"), "pull_state").lower(),
        is_draft=_require_bool(row.get("draft"), "pull_draft"),
        is_merged=_require_bool(row.get("merged"), "pull_merged"),
        is_mergeable=_optional_bool(row.get("mergeable")),
        mergeable_state=_text(row.get("mergeable_state")).lower() or "unknown",
        base_ref=_require_text(base.get("ref"), "base_ref"),
        base_sha=_require_sha(base.get("sha"), "base_sha"),
        base_repository_id=_side_repository_id(base, "base_repository_id"),
        head_ref=_require_text(head.get("ref"), "head_ref"),
        head_sha=_require_sha(head.get("sha"), "head_sha"),
        head_repository_id=_side_repository_id(head, "head_repository_id"),
        author_id=_require_positive_int(author.get("id"), "author_id"),
        author_login=_require_text(author.get("login"), "author_login"),
        merge_commit_sha=_strict_optional_sha(row.get("merge_commit_sha"), "merge_commit_sha"),
        merged_at=_optional_text(row.get("merged_at")),
    )
    return record


def _rule_evidence(value: object) -> BranchRuleEvidence:
    row = _mapping(value)
    parameters = row.get("parameters")
    return BranchRuleEvidence(
        rule_type=_require_text(row.get("type"), "rule_type"),
        ruleset_id=_require_positive_int(row.get("ruleset_id"), "ruleset_id"),
        source_type=_require_text(row.get("ruleset_source_type"), "ruleset_source_type"),
        source=_require_text(row.get("ruleset_source"), "ruleset_source"),
        parameters_fingerprint=_fingerprint(parameters),
    )


def _required_check_policies(value: object) -> tuple[_RequiredCheckPolicy, ...]:
    row = _mapping(value)
    if _rule_type(row) != "required_status_checks":
        return ()
    parameters = _required_rule_parameters(row, "required_status_checks")
    _require_bool(parameters.get("strict_required_status_checks_policy"), "strict_status_checks_policy")
    checks = _sequence(parameters.get("required_status_checks"))
    policies: list[_RequiredCheckPolicy] = []
    for check in checks:
        item = _mapping(check)
        context = _require_text(item.get("context"), "required_check_context")
        app_id = _strict_optional_positive_int(item.get("integration_id"), "required_check_integration_id")
        policies.append(_RequiredCheckPolicy(context, app_id))
    return tuple(policies)


def _required_approvals(value: object) -> int:
    row = _mapping(value)
    if _rule_type(row) != "pull_request":
        return 0
    parameters = _required_rule_parameters(row, "pull_request")
    return _require_nonnegative_int(parameters.get("required_approving_review_count"), "required_approval_count")


def _empty_classic_protection() -> _ClassicProtectionSummary:
    return _ClassicProtectionSummary((), (), 0, False, False, False)


def _classic_protection_summary(
    value: object,
    repository: SourceRepositoryRef,
) -> _ClassicProtectionSummary:
    row = _mapping(value)
    reviews = _classic_review_settings(row)
    evidence = BranchRuleEvidence(
        rule_type="classic_branch_protection",
        ruleset_id=None,
        source_type="Repository",
        source=f"{repository.owner}/{repository.name}",
        parameters_fingerprint=_fingerprint(row),
    )
    return _ClassicProtectionSummary(
        evidence=(evidence,),
        required_checks=_classic_required_check_policies(row),
        required_approvals=_classic_required_approvals(reviews),
        requires_code_owner_review=_classic_bool(reviews, "require_code_owner_reviews"),
        requires_thread_resolution=_classic_enabled(row, "required_conversation_resolution"),
        requires_last_push_approval=_classic_bool(reviews, "require_last_push_approval"),
    )


def _combined_rule_summary(
    config: GitHubReleaseConfig,
    rows: tuple[object, ...],
    classic: _ClassicProtectionSummary,
) -> _RuleSummary:
    """Combine ruleset and classic protection evidence without hiding either."""

    return _RuleSummary(
        evidence=_combined_rule_evidence(rows, classic),
        required_checks=_combined_required_checks(rows, classic),
        required_approvals=_combined_required_approvals(config, rows, classic),
        is_merge_queue_required=_has_rule_type(rows, "merge_queue"),
        requires_code_owner_review=_combined_rule_bool(
            classic.requires_code_owner_review,
            rows,
            "require_code_owner_review",
        ),
        requires_thread_resolution=_combined_rule_bool(
            classic.requires_thread_resolution,
            rows,
            "required_review_thread_resolution",
        ),
        requires_last_push_approval=_combined_rule_bool(
            classic.requires_last_push_approval,
            rows,
            "require_last_push_approval",
        ),
        has_required_team_reviewers=any(_has_required_team_reviewers(row) for row in rows),
        unsupported_rule_types=_unsupported_rule_types(rows),
        is_bypass_policy_verified=config.is_bypass_policy_verified,
        fingerprint="",
    )


def _combined_rule_evidence(
    rows: tuple[object, ...],
    classic: _ClassicProtectionSummary,
) -> tuple[BranchRuleEvidence, ...]:
    return (*(_rule_evidence(row) for row in rows), *classic.evidence)


def _combined_required_checks(
    rows: tuple[object, ...],
    classic: _ClassicProtectionSummary,
) -> tuple[_RequiredCheckPolicy, ...]:
    ruleset_checks = tuple(policy for row in rows for policy in _required_check_policies(row))
    return _deduplicate_check_policies((*ruleset_checks, *classic.required_checks))


def _combined_required_approvals(
    config: GitHubReleaseConfig,
    rows: tuple[object, ...],
    classic: _ClassicProtectionSummary,
) -> int:
    counts = (config.minimum_approvals, classic.required_approvals, *(_required_approvals(row) for row in rows))
    return max(counts)


def _has_rule_type(rows: tuple[object, ...], expected: str) -> bool:
    return any(_rule_type(row) == expected for row in rows)


def _combined_rule_bool(is_classic_required: bool, rows: tuple[object, ...], key: str) -> bool:
    return is_classic_required or any(_rule_bool(row, key) for row in rows)


def _classic_required_check_policies(row: Mapping[str, object]) -> tuple[_RequiredCheckPolicy, ...]:
    value = row.get("required_status_checks")
    if value is None:
        return ()
    settings = _mapping(value)
    _require_bool(settings.get("strict"), "classic_strict_status_checks")
    checks = _sequence(settings.get("checks")) if "checks" in settings else ()
    if checks:
        return tuple(_classic_check_policy(check) for check in checks)
    contexts = _sequence(settings.get("contexts")) if "contexts" in settings else ()
    return tuple(_RequiredCheckPolicy(_require_text(context, "classic_check_context"), None) for context in contexts)


def _classic_check_policy(value: object) -> _RequiredCheckPolicy:
    row = _mapping(value)
    return _RequiredCheckPolicy(
        _require_text(row.get("context"), "classic_check_context"),
        _strict_optional_positive_int(row.get("app_id"), "classic_check_app_id"),
    )


def _classic_review_settings(row: Mapping[str, object]) -> Mapping[str, object] | None:
    value = row.get("required_pull_request_reviews")
    return None if value is None else _mapping(value)


def _classic_required_approvals(settings: Mapping[str, object] | None) -> int:
    if settings is None:
        return 0
    return _require_nonnegative_int(
        settings.get("required_approving_review_count"),
        "classic_required_approval_count",
    )


def _classic_bool(settings: Mapping[str, object] | None, key: str) -> bool:
    if settings is None or key not in settings:
        return False
    return _require_bool(settings.get(key), f"classic_{key}")


def _classic_enabled(row: Mapping[str, object], key: str) -> bool:
    value = row.get(key)
    if value is None:
        return False
    return _require_bool(_mapping(value).get("enabled"), f"classic_{key}_enabled")


def _rule_bool(value: object, key: str) -> bool:
    row = _mapping(value)
    if _rule_type(row) != "pull_request":
        return False
    parameters = _required_rule_parameters(row, "pull_request")
    return _require_bool(parameters.get(key), key)


def _has_required_team_reviewers(value: object) -> bool:
    """Fail closed while team/file-pattern reviewer admission is unsupported."""

    row = _mapping(value)
    if _rule_type(row) != "pull_request":
        return False
    parameters = _required_rule_parameters(row, "pull_request")
    if "required_reviewers" not in parameters:
        return False
    required_reviewers = _sequence(parameters.get("required_reviewers"))
    return any(
        _require_nonnegative_int(
            _mapping(value).get("minimum_approvals"),
            "required_reviewer_minimum_approvals",
        )
        > 0
        for value in required_reviewers
    )


def _rule_type(value: object) -> str:
    return _require_text(_mapping(value).get("type"), "rule_type")


def _required_rule_parameters(row: Mapping[str, object], rule_type: str) -> Mapping[str, object]:
    parameters = row.get("parameters")
    if not isinstance(parameters, Mapping):
        raise _adapter_error("decode_response", "validation", False, f"{rule_type}_parameters_invalid")
    return cast(Mapping[str, object], parameters)


def _unsupported_rule_types(rows: tuple[object, ...]) -> tuple[str, ...]:
    active_types = {_rule_type(row) for row in rows}
    return tuple(sorted(active_types - _SUPPORTED_ACTIVE_RULE_TYPES))


def _deduplicate_check_policies(policies: tuple[_RequiredCheckPolicy, ...]) -> tuple[_RequiredCheckPolicy, ...]:
    unique: dict[tuple[str, int | None], _RequiredCheckPolicy] = {}
    for policy in policies:
        unique[(policy.context, policy.source_app_id)] = policy
    return tuple(unique[key] for key in sorted(unique, key=lambda value: (value[0], value[1] or 0)))


def _current_reviews(rows: tuple[object, ...], author_id: int) -> tuple[PullRequestReviewEvidence, ...]:
    current: dict[int, tuple[int, PullRequestReviewEvidence]] = {}
    for value in rows:
        row = _mapping(value)
        user = _mapping(row.get("user"))
        reviewer_id = _positive_int(user.get("id"))
        review_id = _positive_int(row.get("id"))
        state = _text(row.get("state")).upper()
        if reviewer_id is None or review_id is None or reviewer_id == author_id:
            continue
        if state not in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            continue
        evidence = PullRequestReviewEvidence(
            reviewer_id=reviewer_id,
            reviewer_login=_require_text(user.get("login"), "reviewer_login"),
            state=state,
            commit_sha=_require_sha(row.get("commit_id"), "review_commit_sha"),
            submitted_at=_optional_text(row.get("submitted_at")),
        )
        if reviewer_id not in current or review_id > current[reviewer_id][0]:
            current[reviewer_id] = (review_id, evidence)
    return tuple(item[1] for item in sorted(current.values(), key=lambda value: value[1].reviewer_id))


def _checks_for_policy(
    policy: _RequiredCheckPolicy,
    head_sha: str,
    check_runs: tuple[object, ...],
    statuses: tuple[object, ...],
) -> tuple[RequiredCheckEvidence, ...]:
    checks = _matching_check_runs(policy, head_sha, check_runs)
    status = _matching_commit_status(policy, head_sha, statuses)
    if policy.source_app_id is not None and not checks:
        missing = RequiredCheckEvidence(
            policy.context,
            head_sha,
            "missing",
            None,
            "missing",
            policy.source_app_id,
            False,
        )
        return (missing, *((status,) if status is not None else ()))
    if checks or status is not None:
        return (*checks, *((status,) if status is not None else ()))
    return (RequiredCheckEvidence(policy.context, head_sha, "missing", None, "missing", policy.source_app_id, False),)


def _matching_check_runs(
    policy: _RequiredCheckPolicy,
    head_sha: str,
    rows: tuple[object, ...],
) -> tuple[RequiredCheckEvidence, ...]:
    matches: list[RequiredCheckEvidence] = []
    for value in rows:
        row = _mapping(value)
        app_id = _positive_int(_mapping(row.get("app")).get("id"))
        if _text(row.get("name")) != policy.context or _text(row.get("head_sha")) != head_sha:
            continue
        if policy.source_app_id is not None and app_id != policy.source_app_id:
            continue
        status = _text(row.get("status")).lower()
        conclusion = _optional_text(row.get("conclusion"))
        matches.append(
            RequiredCheckEvidence(
                policy.context,
                head_sha,
                status,
                conclusion,
                "github_check_run",
                app_id,
                status == "completed" and conclusion == "success",
            )
        )
    return tuple(matches)


def _matching_commit_status(
    policy: _RequiredCheckPolicy,
    head_sha: str,
    rows: tuple[object, ...],
) -> RequiredCheckEvidence | None:
    for value in rows:
        row = _mapping(value)
        if _text(row.get("context")) != policy.context:
            continue
        state = _text(row.get("state")).lower()
        return RequiredCheckEvidence(
            policy.context,
            head_sha,
            "completed" if state in {"success", "failure", "error"} else state,
            state,
            "github_commit_status",
            None,
            state == "success",
        )
    return None


def _blocking_reasons(
    record: _PullRequestRecord,
    rules: _RuleSummary,
    review_decision: SourceControlReviewDecision,
    checks: tuple[RequiredCheckEvidence, ...],
) -> tuple[str, ...]:
    """Return every fail-closed reason without hiding concurrent blockers."""

    candidates = (
        (record.state != "open" or record.is_merged, "pull_request_not_open"),
        (record.is_draft, "pull_request_is_draft"),
        (
            record.is_mergeable is not True or record.mergeable_state not in {"clean", "has_hooks"},
            "mergeability_not_confirmed",
        ),
        (
            review_decision is not SourceControlReviewDecision.APPROVED,
            f"review_{review_decision.value}",
        ),
        (rules.is_merge_queue_required, "merge_queue_required"),
        (rules.requires_code_owner_review, "code_owner_review_not_supported"),
        (rules.requires_thread_resolution, "review_thread_resolution_not_supported"),
        (rules.requires_last_push_approval, "last_push_approval_not_supported"),
        (rules.has_required_team_reviewers, "required_team_reviewers_not_supported"),
    )
    reasons = [reason for is_blocked, reason in candidates if is_blocked]
    reasons.extend(f"unsupported_active_rule:{rule_type}" for rule_type in rules.unsupported_rule_types)
    reasons.extend(_policy_blocking_reasons(rules, checks))
    return tuple(reasons)


def _policy_blocking_reasons(
    rules: _RuleSummary,
    checks: tuple[RequiredCheckEvidence, ...],
) -> tuple[str, ...]:
    """Return bypass and exact-check policy blockers in canonical order."""

    candidates = (
        (not rules.is_bypass_policy_verified, "bypass_policy_not_verified"),
        (any(not check.is_successful for check in checks), "required_checks_not_successful"),
    )
    return tuple(reason for is_blocked, reason in candidates if is_blocked)


def _approval_binding_reasons(
    request: SourceControlMergeRequest,
    snapshot: PullRequestSnapshot,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if snapshot.base_sha != request.expected_base_sha:
        reasons.append("approved_base_sha_changed")
    if snapshot.rules_fingerprint != request.expected_rules_fingerprint:
        reasons.append("approved_rules_changed")
    if snapshot.checks_fingerprint != request.expected_checks_fingerprint:
        reasons.append("approved_checks_changed")
    return tuple(reasons)


def _policy_error(request: SourceControlMergeRequest, reasons: tuple[str, ...]) -> AdapterError:
    return _adapter_error(
        "merge_pull_request",
        "conflict",
        False,
        "merge_preconditions_failed",
        idempotency_key=request.idempotency_key,
        details={"blockingReasons": reasons},
    )


def _http_error(
    operation: str,
    response: GitHubHttpResponse,
    idempotency_key: str | None = None,
) -> AdapterError:
    status = response.status_code
    kind, is_retryable = _http_failure_kind(status, response.headers)
    details: dict[str, object] = {"reason": "github_http_error", "statusCode": status}
    request_id = _request_id(response.headers)
    if request_id is not None:
        details["providerRequestId"] = request_id
    retry_after = _retry_after(response.headers)
    if retry_after is not None:
        details["retryAfterSeconds"] = retry_after
    return _adapter_error(operation, kind, is_retryable, "github_http_error", idempotency_key, details)


def _transport_error(
    operation: str,
    failure: GitHubTransportFailure,
    idempotency_key: str | None = None,
    timeout_seconds: int | None = None,
) -> AdapterError:
    return _adapter_error(
        operation,
        failure.kind,
        failure.kind in {"timeout", "unavailable"},
        "github_transport_failure",
        idempotency_key,
        timeout_seconds=timeout_seconds,
    )


def _adapter_error(
    operation: str,
    kind: AdapterFailureKind,
    is_retryable: bool,
    reason: str,
    idempotency_key: str | None = None,
    details: Mapping[str, object] | None = None,
    *,
    timeout_seconds: int | None = None,
) -> AdapterError:
    safe_details = {"reason": reason, **dict(details or {})}
    return AdapterError(
        AdapterFailure(
            adapter_profile="github-release",
            operation=operation,
            kind=kind,
            is_retryable=is_retryable,
            operator_message=f"GitHub release operation failed ({reason}).",
            timeout_seconds=timeout_seconds if kind == "timeout" else None,
            idempotency_key=idempotency_key,
            details=safe_details,
        )
    )


def _http_failure_kind(status: int, headers: Mapping[str, str]) -> tuple[AdapterFailureKind, bool]:
    """Normalize one GitHub HTTP status into the shared failure taxonomy."""

    if _is_rate_limited_forbidden(status, headers):
        return "rate_limited", True
    exact = _EXACT_HTTP_FAILURES.get(status)
    if exact is not None:
        return exact
    if 500 <= status < 600:
        return "unavailable", True
    return "unknown", False


def _is_rate_limited_forbidden(status: int, headers: Mapping[str, str]) -> bool:
    """Recognize GitHub's primary and secondary 403 rate-limit signals."""

    return status == 403 and (_retry_after(headers) is not None or headers.get("x-ratelimit-remaining") == "0")


def _ambiguous_receipt(
    request: SourceControlMergeRequest,
    request_id: str | None,
    reason: str,
) -> SourceControlMergeReceipt:
    return _receipt(
        request.target,
        SourceControlMergeStatus.AMBIGUOUS,
        request_id=request_id,
        idempotency_key=request.idempotency_key,
        evidence={"reason": reason, "knownNotCommitted": False, "safeToRetry": False},
    )


def _receipt(
    target: PullRequestTarget,
    status: SourceControlMergeStatus,
    *,
    merge_commit_sha: str | None = None,
    merged_at: str | None = None,
    request_id: str | None = None,
    idempotency_key: str | None = None,
    evidence: Mapping[str, object] | None = None,
) -> SourceControlMergeReceipt:
    return SourceControlMergeReceipt(
        status=status,
        repository_id=target.repository.repository_id,
        pull_number=target.pull_number,
        head_sha=target.expected_head_sha,
        merge_commit_sha=merge_commit_sha,
        merged_at=merged_at,
        provider_request_id=request_id,
        idempotency_key=idempotency_key,
        evidence=evidence or {"provider": "github", "baseRef": target.expected_base_ref},
    )


def _rules_fingerprint(summary: _RuleSummary) -> str:
    rule_rows = [
        [rule.rule_type, rule.ruleset_id, rule.source_type, rule.source, rule.parameters_fingerprint]
        for rule in summary.evidence
    ]
    payload = {
        "rules": sorted(rule_rows, key=_canonical_json),
        "checks": [[check.context, check.source_app_id] for check in summary.required_checks],
        "approvals": summary.required_approvals,
        "mergeQueue": summary.is_merge_queue_required,
        "codeOwners": summary.requires_code_owner_review,
        "threadResolution": summary.requires_thread_resolution,
        "lastPushApproval": summary.requires_last_push_approval,
        "requiredTeamReviewers": summary.has_required_team_reviewers,
        "unsupportedRuleTypes": summary.unsupported_rule_types,
        "bypassPolicyVerified": summary.is_bypass_policy_verified,
    }
    return _fingerprint(payload)


def _checks_fingerprint(checks: tuple[RequiredCheckEvidence, ...]) -> str:
    rows = [
        [check.context, check.commit_sha, check.status, check.conclusion, check.source, check.source_app_id]
        for check in checks
    ]
    return _fingerprint(sorted(rows, key=_canonical_json))


def _fingerprint(value: object) -> str:
    canonical = _canonical_json(value)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise _adapter_error("decode_response", "validation", False, "github_json_value_invalid") from exc


def _search_side_matches(side: Mapping[str, object], expected_ref: str, repository_id: int) -> bool:
    return _text(side.get("ref")) == expected_ref and _side_repository_id(side, "repository_id") == repository_id


def _side_repository_id(side: Mapping[str, object], field_name: str) -> int:
    repository_id = _positive_int(_mapping(side.get("repo")).get("id"))
    if repository_id is None:
        raise _adapter_error("decode_response", "validation", False, f"{field_name}_missing")
    return repository_id


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _adapter_error("decode_response", "validation", False, "github_object_expected")
    return cast(Mapping[str, object], value)


def _sequence(value: object) -> tuple[object, ...]:
    if not isinstance(value, list):
        raise _adapter_error("decode_response", "validation", False, "github_list_expected")
    return tuple(value)


def _require_text(value: object, field_name: str) -> str:
    text = _text(value)
    if not text:
        raise _adapter_error("decode_response", "validation", False, f"{field_name}_missing")
    return text


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _require_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise _adapter_error("decode_response", "validation", False, f"{field_name}_missing")
    return value


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _require_positive_int(value: object, field_name: str) -> int:
    number = _positive_int(value)
    if number is None:
        raise _adapter_error("decode_response", "validation", False, f"{field_name}_missing")
    return number


def _strict_optional_positive_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    return _require_positive_int(value, field_name)


def _require_nonnegative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _adapter_error("decode_response", "validation", False, f"{field_name}_invalid")
    return value


def _full_sha(value: object) -> str | None:
    return value if isinstance(value, str) and _FULL_GIT_SHA.fullmatch(value) is not None else None


def _strict_optional_sha(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_sha(value, field_name)


def _require_sha(value: object, field_name: str) -> str:
    sha = _full_sha(value)
    if sha is None:
        raise _adapter_error("decode_response", "validation", False, f"{field_name}_invalid")
    return sha


def _request_id(headers: Mapping[str, str]) -> str | None:
    value = headers.get("x-github-request-id")
    return value if value else None


def _retry_after(headers: Mapping[str, str]) -> float | None:
    value = headers.get("retry-after", "").strip()
    try:
        delay = float(value)
    except ValueError:
        return None
    return delay if math.isfinite(delay) and delay >= 0 else None


def _is_safe_coordinate(value: str) -> bool:
    return isinstance(value, str) and bool(value) and _SAFE_COORDINATE.fullmatch(value) is not None


def _is_safe_ref_prefix(value: str) -> bool:
    return isinstance(value, str) and value.endswith("/") and _is_safe_git_ref(f"{value}candidate")


def _is_safe_git_ref(value: str) -> bool:
    forbidden = ("..", "@{", "//", "\\")
    invalid_characters = set(" ~^:?*[]")
    return (
        isinstance(value, str)
        and 0 < len(value) <= 255
        and not value.startswith(("/", "."))
        and not value.endswith(("/", ".", ".lock"))
        and not any(token in value for token in forbidden)
        and not any(character in invalid_characters or ord(character) < 32 for character in value)
    )


__all__ = [
    "GitHubHttpRequest",
    "GitHubHttpResponse",
    "GitHubReleaseAdapter",
    "GitHubReleaseConfig",
    "GitHubTransportFailure",
]
