"""Focused unit coverage for the fail-closed GitHub release adapter."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailureContract
from foundry_lite.application.ports.secret_provider import SecretValue
from foundry_lite.application.ports.source_control_release import (
    PullRequestSearch,
    PullRequestTarget,
    SourceControlMergeMethod,
    SourceControlMergeRequest,
    SourceControlMergeStatus,
    SourceControlReleasePort,
    SourceControlReviewDecision,
    SourceRepositoryRef,
)
from foundry_lite.infrastructure.adapters.github_release import (
    GitHubHttpRequest,
    GitHubHttpResponse,
    GitHubReleaseAdapter,
    GitHubReleaseConfig,
    GitHubTransportFailure,
)

_TOKEN = "ghs_test_installation_token_new_format_not_fixed_length"
_HEAD_SHA = "a" * 40
_BASE_SHA = "b" * 40
_MERGE_SHA = "c" * 40
_CHECK_APP_ID = 15368


class _SecretProvider:
    profile_name = "test-secret"

    def __init__(self) -> None:
        self.names: list[str] = []

    def get_secret(self, name: str, *, version: str | None = None) -> SecretValue:
        assert version is None
        self.names.append(name)
        return SecretValue(name=name, version="v1", value=_TOKEN)

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())


class _GitHubFixtureTransport:
    def __init__(self) -> None:
        self.requests: list[GitHubHttpRequest] = []
        self.pull = _pull_response()
        self.search_results: list[object] = [deepcopy(self.pull)]
        self.rules: list[object] = _rules()
        self.protection_status = 404
        self.protection_body: object = {}
        self.reviews: list[object] = _reviews()
        self.check_runs: list[object] = _check_runs()
        self.check_runs_by_sha: dict[str, list[object]] = {}
        self.statuses: list[object] = []
        self.statuses_by_sha: dict[str, list[object]] = {}
        self.reviewer_permissions: dict[str, str] = {"reviewer": "write"}
        self.pull_status = 200
        self.merge_status = 200
        self.merge_body: object = {"merged": True, "sha": _MERGE_SHA, "message": "merged"}
        self.merge_failure: GitHubTransportFailure | None = None

    @property
    def merge_put_count(self) -> int:
        return sum(request.method == "PUT" for request in self.requests)

    def __call__(self, request: GitHubHttpRequest) -> GitHubHttpResponse:
        self.requests.append(request)
        parsed = urlsplit(request.url)
        page = int(parse_qs(parsed.query).get("page", ["1"])[0])
        if request.method == "PUT":
            if self.merge_failure is not None:
                raise self.merge_failure
            return _response(self.merge_status, self.merge_body, "REQ-MERGE")
        if parsed.path.endswith("/pulls"):
            return _response(200, self.search_results if page == 1 else [], "REQ-SEARCH")
        if parsed.path.endswith("/reviews"):
            return _response(200, self.reviews if page == 1 else [], "REQ-REVIEWS")
        if "/rules/branches/" in parsed.path:
            return _response(200, self.rules if page == 1 else [], "REQ-RULES")
        if "/branches/" in parsed.path and parsed.path.endswith("/protection"):
            return _response(self.protection_status, self.protection_body, "REQ-PROTECTION")
        if parsed.path.endswith("/check-runs"):
            commit_sha = parsed.path.split("/commits/", 1)[1].split("/", 1)[0]
            source_rows = self.check_runs if commit_sha == _HEAD_SHA else self.check_runs_by_sha.get(commit_sha, [])
            rows = source_rows if page == 1 else []
            return _response(200, {"total_count": len(rows), "check_runs": rows}, "REQ-CHECKS")
        if parsed.path.endswith("/statuses"):
            commit_sha = parsed.path.split("/commits/", 1)[1].split("/", 1)[0]
            source_rows = self.statuses if commit_sha == _HEAD_SHA else self.statuses_by_sha.get(commit_sha, [])
            return _response(200, source_rows if page == 1 else [], "REQ-STATUSES")
        if "/collaborators/" in parsed.path and parsed.path.endswith("/permission"):
            login = parsed.path.split("/collaborators/", 1)[1].split("/", 1)[0]
            permission = self.reviewer_permissions.get(login, "none")
            return _response(200, {"permission": permission}, "REQ-PERMISSION")
        if "/pulls/" in parsed.path:
            return _response(self.pull_status, self.pull, "REQ-PULL")
        raise AssertionError(f"unexpected GitHub request: {request.method} {request.url}")


def _repository() -> SourceRepositoryRef:
    return SourceRepositoryRef("github", 123, "ludia8888", "foundry-lite")


def _target(*, head_sha: str = _HEAD_SHA, base_ref: str = "main") -> PullRequestTarget:
    return PullRequestTarget(_repository(), 17, base_ref, head_sha)


def _request() -> SourceControlMergeRequest:
    snapshot = _adapter(_GitHubFixtureTransport()).inspect_pull_request(_target())
    return SourceControlMergeRequest(
        _target(),
        SourceControlMergeMethod.SQUASH,
        "merge-pr-17",
        snapshot.base_sha,
        snapshot.rules_fingerprint,
        snapshot.checks_fingerprint,
    )


def _adapter(
    transport: _GitHubFixtureTransport,
    secret_provider: _SecretProvider | None = None,
) -> GitHubReleaseAdapter:
    config = GitHubReleaseConfig(
        repository=_repository(),
        installation_token_secret_ref="github_installation_token",
        allowed_base_refs=("main",),
        allowed_head_ref_prefixes=("codex/",),
        minimum_approvals=1,
        is_bypass_policy_verified=True,
    )
    return GitHubReleaseAdapter(config, secret_provider or _SecretProvider(), transport=transport)


def _pull_response() -> dict[str, object]:
    return {
        "number": 17,
        "state": "open",
        "draft": False,
        "merged": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "merge_commit_sha": None,
        "merged_at": None,
        "user": {"id": 7, "login": "author"},
        "base": {"ref": "main", "sha": _BASE_SHA, "repo": {"id": 123}},
        "head": {"ref": "codex/release-17", "sha": _HEAD_SHA, "repo": {"id": 123}},
    }


def _rules() -> list[object]:
    return [
        {
            "type": "required_status_checks",
            "ruleset_id": 42,
            "ruleset_source_type": "Repository",
            "ruleset_source": "ludia8888/foundry-lite",
            "parameters": {
                "strict_required_status_checks_policy": True,
                "required_status_checks": [
                    {"context": "quality-gate", "integration_id": _CHECK_APP_ID},
                ],
            },
        },
        {
            "type": "pull_request",
            "ruleset_id": 43,
            "ruleset_source_type": "Repository",
            "ruleset_source": "ludia8888/foundry-lite",
            "parameters": {
                "dismiss_stale_reviews_on_push": True,
                "require_code_owner_review": False,
                "require_last_push_approval": False,
                "required_approving_review_count": 1,
                "required_review_thread_resolution": False,
            },
        },
    ]


def _classic_protection() -> dict[str, object]:
    return {
        "required_status_checks": {
            "strict": True,
            "contexts": ["quality-gate"],
            "checks": [{"context": "quality-gate", "app_id": _CHECK_APP_ID}],
        },
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": True,
            "require_code_owner_reviews": False,
            "required_approving_review_count": 1,
            "require_last_push_approval": False,
        },
        "required_conversation_resolution": {"enabled": False},
        "enforce_admins": {"enabled": True},
    }


def _reviews() -> list[object]:
    return [
        {
            "id": 100,
            "state": "APPROVED",
            "commit_id": _HEAD_SHA,
            "submitted_at": "2026-08-09T01:00:00Z",
            "user": {"id": 8, "login": "reviewer"},
            "body": f"provider text must never be persisted {_TOKEN}",
        }
    ]


def _check_runs() -> list[object]:
    return [
        {
            "name": "quality-gate",
            "head_sha": _HEAD_SHA,
            "status": "completed",
            "conclusion": "success",
            "app": {"id": _CHECK_APP_ID, "name": "GitHub Actions"},
        }
    ]


def _response(status: int, body: object, request_id: str) -> GitHubHttpResponse:
    return GitHubHttpResponse(status, {"x-github-request-id": request_id}, body)


def _mark_pull_merged(transport: _GitHubFixtureTransport) -> None:
    transport.pull.update(
        {
            "state": "closed",
            "merged": True,
            "mergeable": False,
            "mergeable_state": "unknown",
            "merge_commit_sha": _MERGE_SHA,
            "merged_at": "2026-08-09T02:00:00Z",
        }
    )


def test_github_adapter_implements_source_control_release_port() -> None:
    assert isinstance(_adapter(_GitHubFixtureTransport()), SourceControlReleasePort)


def test_find_pull_request_uses_bounded_exact_base_and_head_search() -> None:
    transport = _GitHubFixtureTransport()
    adapter = _adapter(transport)

    snapshot = adapter.find_pull_request(PullRequestSearch(_repository(), "main", "codex/release-17"))

    search_request = transport.requests[0]
    query = parse_qs(urlsplit(search_request.url).query)
    assert query["state"] == ["open"]
    assert query["base"] == ["main"]
    assert query["head"] == ["ludia8888:codex/release-17"]
    assert snapshot.target == _target()
    assert snapshot.is_ready_to_merge is True


def test_find_pull_request_rejects_zero_or_multiple_exact_candidates() -> None:
    transport = _GitHubFixtureTransport()
    adapter = _adapter(transport)
    search = PullRequestSearch(_repository(), "main", "codex/release-17")
    transport.search_results = []

    with pytest.raises(AdapterError) as missing:
        adapter.find_pull_request(search)

    transport.search_results = [deepcopy(transport.pull), deepcopy(transport.pull)]
    with pytest.raises(AdapterError) as duplicate:
        adapter.find_pull_request(search)

    assert missing.value.failure.kind == "not_found"
    assert duplicate.value.failure.kind == "conflict"


def test_find_pull_request_rejects_non_allowlisted_head_before_secret_or_http() -> None:
    transport = _GitHubFixtureTransport()
    secret_provider = _SecretProvider()
    adapter = _adapter(transport, secret_provider)

    with pytest.raises(AdapterError) as excinfo:
        adapter.find_pull_request(PullRequestSearch(_repository(), "main", "feature/unreviewed"))

    assert excinfo.value.failure.kind == "validation"
    assert secret_provider.names == []
    assert transport.requests == []


def test_inspect_returns_active_rules_exact_head_checks_and_review_evidence() -> None:
    transport = _GitHubFixtureTransport()

    snapshot = _adapter(transport).inspect_pull_request(_target())

    assert snapshot.review_decision is SourceControlReviewDecision.APPROVED
    assert snapshot.required_approval_count == 1
    assert snapshot.approvals[0].reviewer_login == "reviewer"
    assert snapshot.required_checks[0].context == "quality-gate"
    assert snapshot.required_checks[0].commit_sha == _HEAD_SHA
    assert snapshot.required_checks[0].source_app_id == _CHECK_APP_ID
    assert snapshot.required_checks[0].is_successful is True
    assert {rule.rule_type for rule in snapshot.active_rules} == {"pull_request", "required_status_checks"}


def test_inspect_allows_zero_extra_github_reviews_when_repository_rules_do_not_require_them() -> None:
    transport = _GitHubFixtureTransport()
    transport.rules = [transport.rules[0]]
    transport.reviews = []
    config = GitHubReleaseConfig(
        repository=_repository(),
        installation_token_secret_ref="github_installation_token",
        allowed_base_refs=("main",),
        allowed_head_ref_prefixes=("codex/",),
        minimum_approvals=0,
        is_bypass_policy_verified=True,
    )

    snapshot = GitHubReleaseAdapter(config, _SecretProvider(), transport=transport).inspect_pull_request(_target())

    assert snapshot.required_approval_count == 0
    assert snapshot.approvals == ()
    assert snapshot.is_ready_to_merge is True
    assert snapshot.rules_fingerprint.startswith("sha256:")
    assert snapshot.checks_fingerprint.startswith("sha256:")
    assert snapshot.provider_request_id == "REQ-PULL"


def test_inspect_blocks_positive_required_team_reviewers_until_supported() -> None:
    transport = _GitHubFixtureTransport()
    pull_rule = transport.rules[1]
    assert isinstance(pull_rule, dict)
    parameters = pull_rule["parameters"]
    assert isinstance(parameters, dict)
    parameters["required_approving_review_count"] = 0
    parameters["required_reviewers"] = [
        {
            "file_patterns": ["src/**"],
            "minimum_approvals": 1,
            "reviewer": {"id": 1234, "type": "Team"},
        }
    ]
    transport.reviews = []
    config = GitHubReleaseConfig(
        repository=_repository(),
        installation_token_secret_ref="github_installation_token",
        allowed_base_refs=("main",),
        allowed_head_ref_prefixes=("codex/",),
        minimum_approvals=0,
        is_bypass_policy_verified=True,
    )

    snapshot = GitHubReleaseAdapter(config, _SecretProvider(), transport=transport).inspect_pull_request(_target())

    assert snapshot.required_approval_count == 0
    assert snapshot.is_ready_to_merge is False
    assert "required_team_reviewers_not_supported" in snapshot.blocking_reasons


def test_inspect_combines_classic_branch_protection_with_ruleset_policy() -> None:
    transport = _GitHubFixtureTransport()
    transport.rules = []
    transport.protection_status = 200
    transport.protection_body = _classic_protection()
    config = GitHubReleaseConfig(
        repository=_repository(),
        installation_token_secret_ref="github_installation_token",
        allowed_base_refs=("main",),
        allowed_head_ref_prefixes=("codex/",),
        minimum_approvals=0,
        is_bypass_policy_verified=True,
    )

    snapshot = GitHubReleaseAdapter(config, _SecretProvider(), transport=transport).inspect_pull_request(_target())

    assert snapshot.required_approval_count == 1
    assert snapshot.required_checks[0].context == "quality-gate"
    assert {rule.rule_type for rule in snapshot.active_rules} == {"classic_branch_protection"}
    assert snapshot.is_ready_to_merge is True


def test_inspect_blocks_classic_code_owner_requirement_and_protection_read_failure() -> None:
    transport = _GitHubFixtureTransport()
    transport.protection_status = 200
    transport.protection_body = _classic_protection()
    required_reviews = transport.protection_body["required_pull_request_reviews"]
    assert isinstance(required_reviews, dict)
    required_reviews["require_code_owner_reviews"] = True

    snapshot = _adapter(transport).inspect_pull_request(_target())

    assert snapshot.is_ready_to_merge is False
    assert "code_owner_review_not_supported" in snapshot.blocking_reasons

    transport = _GitHubFixtureTransport()
    transport.protection_status = 403
    transport.protection_body = {"message": f"provider echoed {_TOKEN}"}
    with pytest.raises(AdapterError) as excinfo:
        _adapter(transport).inspect_pull_request(_target())
    assert excinfo.value.failure.kind == "authorization"
    assert _TOKEN not in json.dumps(excinfo.value.failure.to_payload())


def test_inspect_uses_test_merge_commit_checks_when_present() -> None:
    transport = _GitHubFixtureTransport()
    transport.pull["merge_commit_sha"] = _MERGE_SHA
    merge_check = deepcopy(_check_runs()[0])
    assert isinstance(merge_check, dict)
    merge_check["head_sha"] = _MERGE_SHA
    transport.check_runs_by_sha[_MERGE_SHA] = [merge_check]

    snapshot = _adapter(transport).inspect_pull_request(_target())

    assert snapshot.test_merge_commit_sha == _MERGE_SHA
    assert snapshot.checks_commit_sha == _MERGE_SHA
    assert snapshot.required_checks[0].commit_sha == _MERGE_SHA
    assert snapshot.is_ready_to_merge is True


def test_inspect_falls_back_to_head_when_test_merge_has_no_status() -> None:
    transport = _GitHubFixtureTransport()
    transport.pull["merge_commit_sha"] = _MERGE_SHA

    snapshot = _adapter(transport).inspect_pull_request(_target())

    assert snapshot.test_merge_commit_sha == _MERGE_SHA
    assert snapshot.checks_commit_sha == _HEAD_SHA
    assert snapshot.required_checks[0].commit_sha == _HEAD_SHA
    assert snapshot.is_ready_to_merge is True


def test_inspect_does_not_let_head_success_override_test_merge_failure() -> None:
    transport = _GitHubFixtureTransport()
    transport.pull["merge_commit_sha"] = _MERGE_SHA
    merge_check = deepcopy(_check_runs()[0])
    assert isinstance(merge_check, dict)
    merge_check["head_sha"] = _MERGE_SHA
    merge_check["conclusion"] = "failure"
    transport.check_runs_by_sha[_MERGE_SHA] = [merge_check]

    snapshot = _adapter(transport).inspect_pull_request(_target())

    assert snapshot.required_checks[0].commit_sha == _MERGE_SHA
    assert snapshot.required_checks[0].is_successful is False
    assert snapshot.is_ready_to_merge is False
    assert "required_checks_not_successful" in snapshot.blocking_reasons


def test_inspect_fails_binding_when_repository_base_or_head_changed() -> None:
    mutations = (
        lambda pull: pull["base"]["repo"].update({"id": 999}),
        lambda pull: pull["base"].update({"ref": "production"}),
        lambda pull: pull["head"].update({"sha": "d" * 40}),
        lambda pull: pull["head"]["repo"].update({"id": 999}),
    )

    for mutate in mutations:
        transport = _GitHubFixtureTransport()
        mutate(transport.pull)
        with pytest.raises(AdapterError) as excinfo:
            _adapter(transport).inspect_pull_request(_target())
        assert excinfo.value.failure.kind == "conflict"
        assert excinfo.value.failure.details["reason"] == "pull_request_binding_changed"


def test_inspect_treats_check_from_wrong_sha_or_app_as_missing() -> None:
    for key, value in (("head_sha", "d" * 40), ("app", {"id": 999})):
        transport = _GitHubFixtureTransport()
        check_run = transport.check_runs[0]
        assert isinstance(check_run, dict)
        check_run[key] = value

        snapshot = _adapter(transport).inspect_pull_request(_target())

        assert snapshot.required_checks[0].status == "missing"
        assert snapshot.is_ready_to_merge is False
        assert "required_checks_not_successful" in snapshot.blocking_reasons


def test_inspect_accepts_required_commit_status_when_rule_does_not_pin_an_app() -> None:
    transport = _GitHubFixtureTransport()
    rule = transport.rules[0]
    assert isinstance(rule, dict)
    parameters = rule["parameters"]
    assert isinstance(parameters, dict)
    parameters["required_status_checks"] = [{"context": "legacy-ci"}]
    transport.check_runs = []
    transport.statuses = [{"context": "legacy-ci", "state": "success"}]

    snapshot = _adapter(transport).inspect_pull_request(_target())

    assert snapshot.required_checks[0].source == "github_commit_status"
    assert snapshot.required_checks[0].is_successful is True
    assert snapshot.is_ready_to_merge is True


def test_inspect_requires_same_name_check_run_and_commit_status_to_both_pass() -> None:
    transport = _GitHubFixtureTransport()
    transport.statuses = [{"context": "quality-gate", "state": "failure"}]

    snapshot = _adapter(transport).inspect_pull_request(_target())

    assert len(snapshot.required_checks) == 2
    assert {check.source for check in snapshot.required_checks} == {
        "github_check_run",
        "github_commit_status",
    }
    assert any(not check.is_successful for check in snapshot.required_checks)
    assert snapshot.is_ready_to_merge is False
    assert "required_checks_not_successful" in snapshot.blocking_reasons


def test_inspect_does_not_let_legacy_status_replace_missing_app_pinned_check() -> None:
    transport = _GitHubFixtureTransport()
    transport.check_runs = []
    transport.statuses = [{"context": "quality-gate", "state": "success"}]

    snapshot = _adapter(transport).inspect_pull_request(_target())

    assert {check.source for check in snapshot.required_checks} == {"missing", "github_commit_status"}
    assert snapshot.is_ready_to_merge is False
    assert "required_checks_not_successful" in snapshot.blocking_reasons


def test_inspect_counts_only_reviews_from_write_or_admin_collaborators() -> None:
    transport = _GitHubFixtureTransport()
    transport.reviewer_permissions["reviewer"] = "read"

    snapshot = _adapter(transport).inspect_pull_request(_target())

    assert snapshot.approvals == ()
    assert snapshot.review_decision is SourceControlReviewDecision.REVIEW_REQUIRED
    assert snapshot.is_ready_to_merge is False


def test_inspect_blocks_unknown_active_release_rule() -> None:
    transport = _GitHubFixtureTransport()
    transport.rules.append(
        {
            "type": "required_deployments",
            "ruleset_id": 99,
            "ruleset_source_type": "Repository",
            "ruleset_source": "ludia8888/foundry-lite",
            "parameters": {"required_deployment_environments": ["production"]},
        }
    )

    snapshot = _adapter(transport).inspect_pull_request(_target())

    assert snapshot.is_ready_to_merge is False
    assert "unsupported_active_rule:required_deployments" in snapshot.blocking_reasons


def test_inspect_blocks_until_operator_verifies_app_has_no_ruleset_bypass() -> None:
    transport = _GitHubFixtureTransport()
    config = GitHubReleaseConfig(
        _repository(),
        "github_installation_token",
        allowed_head_ref_prefixes=("codex/",),
    )

    snapshot = GitHubReleaseAdapter(config, _SecretProvider(), transport=transport).inspect_pull_request(_target())

    assert snapshot.is_ready_to_merge is False
    assert "bypass_policy_not_verified" in snapshot.blocking_reasons


def test_inspect_rejects_malformed_supported_rule_instead_of_weakening_policy() -> None:
    transport = _GitHubFixtureTransport()
    pull_rule = transport.rules[1]
    assert isinstance(pull_rule, dict)
    pull_rule["parameters"] = {"required_approving_review_count": "1"}

    with pytest.raises(AdapterError) as excinfo:
        _adapter(transport).inspect_pull_request(_target())

    assert excinfo.value.failure.kind == "validation"
    assert excinfo.value.failure.operation == "decode_response"


@pytest.mark.parametrize("blocker", ["draft", "review", "merge_queue", "last_push", "check"])
def test_merge_fails_closed_before_put_for_unsafe_candidate(blocker: str) -> None:
    transport = _GitHubFixtureTransport()
    if blocker == "draft":
        transport.pull["draft"] = True
    elif blocker == "review":
        transport.reviews = []
    elif blocker == "merge_queue":
        transport.rules.append(
            {
                "type": "merge_queue",
                "ruleset_id": 99,
                "ruleset_source_type": "Repository",
                "ruleset_source": "ludia8888/foundry-lite",
            }
        )
    elif blocker == "last_push":
        pull_request_rule = transport.rules[1]
        assert isinstance(pull_request_rule, dict)
        parameters = pull_request_rule["parameters"]
        assert isinstance(parameters, dict)
        parameters["require_last_push_approval"] = True
    else:
        transport.check_runs[0]["conclusion"] = "failure"  # type: ignore[index]

    with pytest.raises(AdapterError) as excinfo:
        _adapter(transport).merge_pull_request(_request())

    assert excinfo.value.failure.kind == "conflict"
    assert transport.merge_put_count == 0
    assert excinfo.value.failure.idempotency_key == "merge-pr-17"


def test_merge_fails_closed_when_approved_base_rules_or_checks_change() -> None:
    for field_name in ("expected_base_sha", "expected_rules_fingerprint", "expected_checks_fingerprint"):
        transport = _GitHubFixtureTransport()
        request = _request()
        if field_name == "expected_base_sha":
            request = replace(request, expected_base_sha="d" * 40)
        elif field_name == "expected_rules_fingerprint":
            request = replace(request, expected_rules_fingerprint=f"sha256:{'f' * 64}")
        else:
            request = replace(request, expected_checks_fingerprint=f"sha256:{'f' * 64}")

        with pytest.raises(AdapterError) as excinfo:
            _adapter(transport).merge_pull_request(request)

        assert excinfo.value.failure.kind == "conflict"
        assert transport.merge_put_count == 0


def test_merge_rejects_non_allowlisted_method_before_secret_or_http() -> None:
    transport = _GitHubFixtureTransport()
    secret_provider = _SecretProvider()
    request = replace(_request(), merge_method=SourceControlMergeMethod.MERGE)

    with pytest.raises(AdapterError) as excinfo:
        _adapter(transport, secret_provider).merge_pull_request(request)

    assert excinfo.value.failure.details["blockingReasons"] == ("merge_method_not_allowed",)
    assert secret_provider.names == []
    assert transport.requests == []


def test_merge_uses_fixed_host_secret_token_api_version_and_exact_sha() -> None:
    transport = _GitHubFixtureTransport()
    secret_provider = _SecretProvider()

    receipt = _adapter(transport, secret_provider).merge_pull_request(_request())

    merge_request = next(request for request in transport.requests if request.method == "PUT")
    assert all(urlsplit(request.url).scheme == "https" for request in transport.requests)
    assert all(urlsplit(request.url).netloc == "api.github.com" for request in transport.requests)
    assert merge_request.url == "https://api.github.com/repos/ludia8888/foundry-lite/pulls/17/merge"
    assert merge_request.headers["authorization"] == f"Bearer {_TOKEN}"
    assert merge_request.headers["x-github-api-version"] == "2026-03-10"
    assert merge_request.body == {"sha": _HEAD_SHA, "merge_method": "squash"}
    assert _TOKEN not in repr(merge_request)
    assert receipt.status is SourceControlMergeStatus.LANDED
    assert receipt.merge_commit_sha == _MERGE_SHA
    assert receipt.provider_request_id == "REQ-MERGE"
    assert receipt.idempotency_key == "merge-pr-17"
    assert receipt.evidence["checksCommitSha"] == _HEAD_SHA
    assert transport.merge_put_count == 1
    assert secret_provider.names == ["github_installation_token"]


@pytest.mark.parametrize("failure_kind", ["timeout", "unavailable", "validation"])
def test_merge_transport_failure_is_ambiguous_and_never_blindly_retried(failure_kind: str) -> None:
    transport = _GitHubFixtureTransport()
    transport.merge_failure = GitHubTransportFailure(failure_kind)  # type: ignore[arg-type]
    adapter = _adapter(transport)

    ambiguous = adapter.merge_pull_request(_request())

    assert ambiguous.status is SourceControlMergeStatus.AMBIGUOUS
    assert ambiguous.evidence == {
        "reason": "merge_transport_outcome_unknown",
        "knownNotCommitted": False,
        "safeToRetry": False,
    }
    assert transport.merge_put_count == 1

    transport.merge_failure = None
    _mark_pull_merged(transport)
    reconciled = adapter.lookup_merge(_target())

    assert reconciled.status is SourceControlMergeStatus.LANDED
    assert reconciled.merge_commit_sha == _MERGE_SHA
    assert transport.merge_put_count == 1


def test_merge_server_error_and_malformed_success_are_ambiguous() -> None:
    for status, body in (
        (201, {"merged": True, "sha": _MERGE_SHA}),
        (501, {"message": f"never echo {_TOKEN}"}),
        (599, {"message": "nonstandard upstream failure"}),
        (200, {"merged": True, "sha": "bad"}),
        (200, ["unexpected response shape"]),
    ):
        transport = _GitHubFixtureTransport()
        transport.merge_status = status
        transport.merge_body = body

        receipt = _adapter(transport).merge_pull_request(_request())

        assert receipt.status is SourceControlMergeStatus.AMBIGUOUS
        assert receipt.provider_request_id == "REQ-MERGE"
        assert _TOKEN not in json.dumps(dict(receipt.evidence))
        assert transport.merge_put_count == 1


@pytest.mark.parametrize("status", [405, 409, 422])
def test_merge_conflict_status_is_outcome_unknown_for_exact_reconciliation(status: int) -> None:
    transport = _GitHubFixtureTransport()
    transport.merge_status = status
    transport.merge_body = {"message": f"provider echoed {_TOKEN}"}

    receipt = _adapter(transport).merge_pull_request(_request())

    assert receipt.status is SourceControlMergeStatus.AMBIGUOUS
    assert receipt.provider_request_id == "REQ-MERGE"
    assert receipt.idempotency_key == "merge-pr-17"
    assert receipt.evidence == {
        "reason": "merge_conflict_outcome_unknown",
        "knownNotCommitted": False,
        "safeToRetry": False,
    }
    assert transport.merge_put_count == 1
    assert _TOKEN not in json.dumps(dict(receipt.evidence))


def test_auth_error_never_exposes_token_or_provider_body() -> None:
    transport = _GitHubFixtureTransport()
    transport.pull_status = 403
    unsafe_provider_body: dict[str, object] = {"message": f"bad token {_TOKEN}"}
    transport.pull = unsafe_provider_body

    with pytest.raises(AdapterError) as excinfo:
        _adapter(transport).inspect_pull_request(_target())

    assert excinfo.value.failure.kind == "authorization"
    assert _TOKEN not in str(excinfo.value)
    assert _TOKEN not in json.dumps(excinfo.value.failure.to_payload())
    assert all(_TOKEN not in repr(request) for request in transport.requests)


def test_lookup_distinguishes_absent_and_landed_without_mutation() -> None:
    transport = _GitHubFixtureTransport()
    adapter = _adapter(transport)

    absent = adapter.lookup_merge(_target())
    _mark_pull_merged(transport)
    landed = adapter.lookup_merge(_target())

    assert absent.status is SourceControlMergeStatus.ABSENT
    assert landed.status is SourceControlMergeStatus.LANDED
    assert landed.merged_at == "2026-08-09T02:00:00Z"
    assert transport.merge_put_count == 0


def test_fresh_merge_rejects_pull_already_merged_before_governed_dispatch() -> None:
    transport = _GitHubFixtureTransport()
    _mark_pull_merged(transport)

    with pytest.raises(AdapterError) as excinfo:
        _adapter(transport).merge_pull_request(_request())

    failure = excinfo.value.failure
    assert failure.kind == "conflict"
    assert failure.idempotency_key == "merge-pr-17"
    assert failure.details["blockingReasons"] == ("pull_request_already_merged_before_governed_dispatch",)
    assert transport.merge_put_count == 0


def test_rate_limit_failure_keeps_retry_evidence_without_provider_body() -> None:
    transport = _GitHubFixtureTransport()
    transport.pull_status = 403

    def rate_limited(request: GitHubHttpRequest) -> GitHubHttpResponse:
        transport.requests.append(request)
        return GitHubHttpResponse(
            403,
            {"x-github-request-id": "REQ-RATE", "retry-after": "2.5"},
            {"message": f"secondary rate limit {_TOKEN}"},
        )

    config = GitHubReleaseConfig(
        _repository(),
        "github_installation_token",
        allowed_head_ref_prefixes=("codex/",),
    )
    adapter = GitHubReleaseAdapter(config, _SecretProvider(), transport=rate_limited)

    with pytest.raises(AdapterError) as excinfo:
        adapter.inspect_pull_request(_target())

    assert excinfo.value.failure.kind == "rate_limited"
    assert excinfo.value.failure.is_retryable is True
    assert excinfo.value.failure.details["retryAfterSeconds"] == 2.5
    assert _TOKEN not in json.dumps(excinfo.value.failure.to_payload())


def test_read_timeout_is_typed_with_bounded_timeout_evidence() -> None:
    def timed_out(_request: GitHubHttpRequest) -> GitHubHttpResponse:
        raise GitHubTransportFailure("timeout")

    config = GitHubReleaseConfig(
        _repository(),
        "github_installation_token",
        allowed_head_ref_prefixes=("codex/",),
        timeout_seconds=9,
    )
    adapter = GitHubReleaseAdapter(config, _SecretProvider(), transport=timed_out)

    with pytest.raises(AdapterError) as excinfo:
        adapter.inspect_pull_request(_target())

    assert excinfo.value.failure.kind == "timeout"
    assert excinfo.value.failure.is_retryable is True
    assert excinfo.value.failure.timeout_seconds == 9
    assert _TOKEN not in str(excinfo.value)


def test_failure_contract_declares_idempotency_timeout_and_reconciliation() -> None:
    contract = _adapter(_GitHubFixtureTransport()).failure_contract()
    modes = {(mode.operation, mode.kind): mode for mode in contract.modes}

    assert contract.adapter_profile == "github-release"
    assert modes[("merge_pull_request", "conflict")].has_required_idempotency_key is True
    assert modes[("merge_pull_request", "timeout")].has_required_idempotency_key is True
    assert modes[("merge_pull_request", "timeout")].timeout_seconds == 15
    assert ("lookup_merge", "unavailable") in modes


def test_config_rejects_untrusted_repository_coordinates_or_refs() -> None:
    with pytest.raises(ValueError, match="unsupported characters"):
        GitHubReleaseConfig(
            SourceRepositoryRef("github", 123, "api.github.com/evil", "foundry-lite"),
            "github_installation_token",
        )
    with pytest.raises(ValueError, match="allowed_base_ref"):
        GitHubReleaseConfig(_repository(), "github_installation_token", allowed_base_refs=("../main",))
    with pytest.raises(ValueError, match="allowed_head_ref_prefix"):
        GitHubReleaseConfig(
            _repository(),
            "github_installation_token",
            allowed_head_ref_prefixes=("../",),
        )


def test_http_request_rejects_non_github_or_non_https_urls_and_redacts_headers() -> None:
    headers: Mapping[str, str] = {"authorization": f"Bearer {_TOKEN}"}

    with pytest.raises(ValueError, match="fixed"):
        GitHubHttpRequest("GET", "https://evil.example/repos/a/b", headers)
    with pytest.raises(ValueError, match="fixed"):
        GitHubHttpRequest("GET", "http://api.github.com/repos/a/b", headers)

    request = GitHubHttpRequest("GET", "https://api.github.com/repos/a/b", headers)
    assert _TOKEN not in repr(request)
