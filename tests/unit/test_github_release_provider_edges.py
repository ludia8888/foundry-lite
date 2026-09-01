"""Malformed-provider and transport bounds for governed GitHub releases."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import replace
from io import BytesIO

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailureContract
from foundry_lite.application.ports.secret_provider import SecretValue
from foundry_lite.application.ports.source_control_candidate import SourceRepositoryRef
from foundry_lite.application.ports.source_control_release import (
    PullRequestReviewEvidence,
    PullRequestSearch,
    SourceControlMergeMethod,
)
from foundry_lite.infrastructure.adapters import github_release as github

_SHA = "a" * 40


class _SecretProvider:
    profile_name = "test"

    def get_secret(self, name: str, *, version: str | None = None) -> SecretValue:
        return SecretValue(name=name, version=version or "v1", value="token")

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())


def _repository(**overrides: object) -> SourceRepositoryRef:
    values: dict[str, object] = {
        "provider": "github",
        "repository_id": 42,
        "owner": "example",
        "name": "foundry-lite",
    }
    values.update(overrides)
    return SourceRepositoryRef(**values)  # type: ignore[arg-type]


def _config(**overrides: object) -> github.GitHubReleaseConfig:
    values: dict[str, object] = {
        "repository": _repository(),
        "installation_token_secret_ref": "github-token",
        "allowed_base_refs": ("main",),
        "allowed_head_ref_prefixes": ("codex/",),
        "minimum_approvals": 1,
        "allowed_merge_methods": (SourceControlMergeMethod.SQUASH,),
        "is_bypass_policy_verified": True,
        "timeout_seconds": 15,
        "max_pages": 10,
        "max_reviewers": 100,
    }
    values.update(overrides)
    return github.GitHubReleaseConfig(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [
        {"repository": _repository(provider="gitlab")},
        {"repository": _repository(owner="bad/name")},
        {"installation_token_secret_ref": 7},
        {"installation_token_secret_ref": "   "},
        {"allowed_base_refs": ()},
        {"allowed_base_refs": ("../main",)},
        {"allowed_head_ref_prefixes": ("codex",)},
        {"allowed_merge_methods": ()},
        {"allowed_merge_methods": ("squash",)},
        {"minimum_approvals": True},
        {"timeout_seconds": True},
        {"max_pages": 101},
        {"max_reviewers": 0},
        {"is_bypass_policy_verified": 1},
    ],
)
def test_config_rejects_wrong_types_and_unbounded_release_coordinates(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _config(**overrides)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"method": "DELETE"},
        {"url": "http://api.github.com/repos/a/b"},
        {"url": "https://evil.example/repos/a/b"},
        {"timeout_seconds": 0},
        {"timeout_seconds": True},
        {"timeout_seconds": float("inf")},
    ],
)
def test_transport_request_is_fixed_host_method_and_finite_timeout(kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {
        "method": "GET",
        "url": "https://api.github.com/repos/a/b",
        "headers": {"authorization": "Bearer secret"},
        "timeout_seconds": 1,
    }
    values.update(kwargs)
    with pytest.raises(ValueError):
        github.GitHubHttpRequest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status_code": True},
        {"status_code": 99},
        {"status_code": 600},
        {"headers": []},
    ],
)
def test_transport_response_requires_valid_http_status_and_header_mapping(kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {"status_code": 200, "headers": {}, "body": {}}
    values.update(kwargs)
    with pytest.raises(ValueError):
        github.GitHubHttpResponse(**values)  # type: ignore[arg-type]


class _Response:
    def __init__(self, value: object) -> None:
        self.value = value

    def read(self, _limit: int) -> object:
        return self.value


@pytest.mark.parametrize(
    "response",
    [
        object(),
        _Response("text"),
        _Response(b"x" * (4 * 1024 * 1024 + 1)),
        _Response(b"\xff"),
        _Response(b"{"),
        _Response(b'{"value":NaN}'),
    ],
)
def test_response_decoder_rejects_unbounded_nonbytes_nonutf8_and_nonstandard_json(response: object) -> None:
    with pytest.raises(github.GitHubTransportFailure) as exc_info:
        github._response_json(response)
    assert exc_info.value.kind == "validation"


def test_response_decoder_and_header_normalizer_accept_only_bounded_provider_data() -> None:
    assert github._response_json(_Response(b"")) == {}
    assert github._response_json(_Response(b'{"ok":true}')) == {"ok": True}
    assert github._normalized_headers(object()) == {}
    assert github._normalized_headers({"X-GitHub-Request-ID": "REQ-1"}) == {"x-github-request-id": "REQ-1"}


def test_urllib_transport_maps_http_timeout_url_and_os_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    request = github.GitHubHttpRequest("GET", "https://api.github.com/repos/a/b", {}, timeout_seconds=1)

    class _Opener:
        def __init__(self, failure: BaseException | None = None) -> None:
            self.failure = failure

        def open(self, *_args: object, **_kwargs: object) -> object:
            if self.failure is not None:
                raise self.failure
            response = BytesIO(b'{"ok":true}')
            response.status = 200  # type: ignore[attr-defined]
            response.headers = {"X-GitHub-Request-ID": "REQ"}  # type: ignore[attr-defined]
            return response

    monkeypatch.setattr(github, "build_opener", lambda *_: _Opener())
    response = github._urllib_transport(request)
    assert response.status_code == 200 and response.body == {"ok": True}

    for failure, kind in (
        (TimeoutError(), "timeout"),
        (github.URLError(TimeoutError()), "timeout"),
        (github.URLError("offline"), "unavailable"),
        (OSError(), "unavailable"),
    ):
        monkeypatch.setattr(github, "build_opener", lambda *_args, failure=failure: _Opener(failure))
        with pytest.raises(github.GitHubTransportFailure) as exc_info:
            github._urllib_transport(request)
        assert exc_info.value.kind == kind


def test_current_reviews_rejects_malformed_security_evidence_and_keeps_latest_decision() -> None:
    latest = github._current_reviews(
        (
            {"id": 1, "state": "APPROVED", "commit_id": _SHA, "user": {"id": 8, "login": "reviewer"}},
            {"id": 2, "state": "CHANGES_REQUESTED", "commit_id": _SHA, "user": {"id": 8, "login": "reviewer"}},
            {"id": 3, "state": "COMMENTED", "commit_id": _SHA, "user": {"id": 9, "login": "ignored"}},
            {"id": 4, "state": "APPROVED", "commit_id": _SHA, "user": {"id": 7, "login": "author"}},
        ),
        author_id=7,
    )
    assert latest == (PullRequestReviewEvidence(8, "reviewer", "CHANGES_REQUESTED", _SHA, None),)

    for row in (
        {"id": 1, "state": "APPROVED", "commit_id": "short", "user": {"id": 8, "login": "reviewer"}},
        {"id": 1, "state": "APPROVED", "commit_id": _SHA, "user": {"id": 8, "login": ""}},
    ):
        with pytest.raises(AdapterError) as exc_info:
            github._current_reviews((row,), 7)
        assert exc_info.value.failure.operation == "decode_response"


def test_provider_number_and_retry_after_helpers_never_accept_bool_nan_or_infinity() -> None:
    assert github._positive_int(True) is None
    assert github._positive_int(1) == 1
    assert github._full_sha(_SHA) == _SHA
    assert github._full_sha(_SHA.upper()) is None
    assert github._retry_after({"retry-after": "2.5"}) == 2.5
    assert github._retry_after({"retry-after": "-1"}) is None
    assert github._retry_after({"retry-after": "inf"}) is None
    assert github._retry_after({"retry-after": "nan"}) is None


@pytest.mark.parametrize("status", [500, 599])
def test_server_failures_are_retryable_unavailable(status: int) -> None:
    assert github._http_failure_kind(status, {}) == ("unavailable", True)


def test_rate_limit_detection_and_unknown_status_are_fail_closed() -> None:
    assert github._http_failure_kind(403, {"retry-after": "1"}) == ("rate_limited", True)
    assert github._http_failure_kind(403, {"x-ratelimit-remaining": "0"}) == ("rate_limited", True)
    assert github._http_failure_kind(418, {}) == ("unknown", False)


def test_candidate_blob_decoder_binds_sha_size_encoding_and_base64() -> None:
    content = b'{"schemaVersion":"v1"}\n'
    blob_sha = hashlib.sha1(f"blob {len(content)}\0".encode() + content, usedforsecurity=False).hexdigest()
    row = {
        "sha": blob_sha,
        "size": len(content),
        "encoding": "base64",
        "content": base64.b64encode(content).decode(),
    }
    assert github._decode_candidate_blob(row, blob_sha, "lookup") == content

    for changed in (
        {**row, "encoding": "utf-8"},
        {**row, "size": True},
        {**row, "content": "%%%"},
        {**row, "size": len(content) + 1},
    ):
        with pytest.raises(AdapterError):
            github._decode_candidate_blob(changed, blob_sha, "lookup")


def test_canonical_fingerprints_reject_nonstandard_nan_payloads() -> None:
    with pytest.raises(AdapterError) as exc_info:
        github._fingerprint({"value": float("nan")})
    assert exc_info.value.failure.operation == "decode_response"
    assert exc_info.value.failure.details["reason"] == "github_json_value_invalid"
    first = github._fingerprint({"b": 2, "a": 1})
    second = github._fingerprint({"a": 1, "b": 2})
    assert first == second


def test_config_copy_still_preserves_typed_merge_methods() -> None:
    assert replace(_config(), minimum_approvals=0).allowed_merge_methods == (SourceControlMergeMethod.SQUASH,)


def test_classic_protection_decodes_check_and_review_policy_shapes_exactly() -> None:
    assert github._classic_required_check_policies({}) == ()
    assert github._classic_required_check_policies(
        {"required_status_checks": {"strict": True, "contexts": ["quality-pr-fast"]}}
    ) == (github._RequiredCheckPolicy("quality-pr-fast", None),)
    assert github._classic_required_check_policies(
        {
            "required_status_checks": {
                "strict": True,
                "checks": [{"context": "codeql", "app_id": 42}],
            }
        }
    ) == (github._RequiredCheckPolicy("codeql", 42),)

    assert github._classic_required_approvals(None) == 0
    assert github._classic_required_approvals({"required_approving_review_count": 2}) == 2
    assert github._classic_bool(None, "dismiss_stale_reviews") is False
    assert github._classic_bool({}, "dismiss_stale_reviews") is False
    assert github._classic_enabled({}, "required_conversation_resolution") is False
    assert (
        github._classic_enabled(
            {"required_conversation_resolution": {"enabled": True}},
            "required_conversation_resolution",
        )
        is True
    )


def test_provider_policy_decoders_fail_closed_on_shape_and_scalar_confusion() -> None:
    invalid_calls = (
        lambda: github._mapping([]),
        lambda: github._sequence({}),
        lambda: github._side_repository_id({"repo": {}}, "head_repository_id"),
        lambda: github._require_bool(1, "strict"),
        lambda: github._require_positive_int(True, "repository_id"),
        lambda: github._require_nonnegative_int(-1, "required_approvals"),
        lambda: github._required_rule_parameters({"type": "pull_request"}, "pull_request"),
        lambda: github._classic_required_check_policies(
            {"required_status_checks": {"strict": True, "checks": "quality-pr-fast"}}
        ),
        lambda: github._classic_required_approvals({"required_approving_review_count": True}),
    )

    for invalid_call in invalid_calls:
        with pytest.raises(AdapterError) as exc_info:
            invalid_call()
        assert exc_info.value.failure.operation == "decode_response"
        assert exc_info.value.failure.kind == "validation"
        assert exc_info.value.failure.is_retryable is False


def test_required_check_evidence_distinguishes_missing_status_and_bound_app() -> None:
    unbound_policy = github._RequiredCheckPolicy("quality-pr-fast", None)
    assert github._checks_for_policy(unbound_policy, _SHA, (), ()) == (
        github.RequiredCheckEvidence(
            "quality-pr-fast",
            _SHA,
            "missing",
            None,
            "missing",
            None,
            False,
        ),
    )
    assert (
        github._matching_commit_status(
            unbound_policy,
            _SHA,
            ({"context": "another-check", "state": "success"},),
        )
        is None
    )

    bound_policy = github._RequiredCheckPolicy("codeql", 42)
    evidence = github._checks_for_policy(
        bound_policy,
        _SHA,
        (),
        ({"context": "codeql", "state": "success"},),
    )
    assert [item.source for item in evidence] == ["missing", "github_commit_status"]
    assert evidence[0].source_app_id == 42 and evidence[0].is_successful is False
    assert evidence[1].is_successful is True


def test_matching_check_runs_require_exact_context_commit_and_app() -> None:
    policy = github._RequiredCheckPolicy("codeql", 42)
    rows = (
        {
            "id": 101,
            "name": "codeql",
            "head_sha": _SHA,
            "status": "completed",
            "conclusion": "success",
            "app": {"id": 7},
        },
        {
            "id": 102,
            "name": "another-check",
            "head_sha": _SHA,
            "status": "completed",
            "conclusion": "success",
            "app": {"id": 42},
        },
        {
            "id": 103,
            "name": "codeql",
            "head_sha": "b" * 40,
            "status": "completed",
            "conclusion": "success",
            "app": {"id": 42},
        },
        {
            "id": 104,
            "name": "codeql",
            "head_sha": _SHA,
            "status": "completed",
            "conclusion": "success",
            "app": {"id": 42},
        },
    )

    evidence = github._matching_check_runs(policy, _SHA, rows)
    assert len(evidence) == 1
    assert evidence[0].source == "github_check_run"
    assert evidence[0].source_app_id == 42
    assert evidence[0].is_successful is True


class _QueuedTransport:
    def __init__(self, responses: list[github.GitHubHttpResponse]) -> None:
        self.responses = responses
        self.requests: list[github.GitHubHttpRequest] = []

    def __call__(self, request: github.GitHubHttpRequest) -> github.GitHubHttpResponse:
        self.requests.append(request)
        assert self.responses, f"unexpected GitHub request: {request.method} {request.url}"
        return self.responses.pop(0)


def test_source_ref_inspection_binds_ref_commit_and_tree_coordinates() -> None:
    tree_sha = "b" * 40
    transport = _QueuedTransport(
        [
            github.GitHubHttpResponse(
                200,
                {},
                {"ref": "refs/heads/main", "object": {"type": "commit", "sha": _SHA}},
            ),
            github.GitHubHttpResponse(
                200,
                {},
                {"sha": _SHA, "tree": {"sha": tree_sha}, "parents": []},
            ),
        ]
    )
    adapter = github.GitHubReleaseAdapter(_config(), _SecretProvider(), transport=transport)

    snapshot = adapter.inspect_source_ref(_repository(), "main")

    assert snapshot.repository == _repository()
    assert snapshot.ref == "main"
    assert snapshot.commit_sha == _SHA
    assert snapshot.tree_sha == tree_sha
    assert len(transport.requests) == 2


def test_source_ref_inspection_is_fail_closed_for_missing_or_changed_identity() -> None:
    missing = github.GitHubReleaseAdapter(
        _config(),
        _SecretProvider(),
        transport=_QueuedTransport([github.GitHubHttpResponse(404, {}, {})]),
    )
    with pytest.raises(AdapterError) as exc_info:
        missing.inspect_source_ref(_repository(), "main")
    assert exc_info.value.failure.kind == "not_found"

    changed = github.GitHubReleaseAdapter(
        _config(),
        _SecretProvider(),
        transport=_QueuedTransport(
            [
                github.GitHubHttpResponse(
                    200,
                    {},
                    {"ref": "refs/heads/other", "object": {"type": "commit", "sha": _SHA}},
                )
            ]
        ),
    )
    with pytest.raises(AdapterError) as exc_info:
        changed.inspect_source_ref(_repository(), "main")
    assert exc_info.value.failure.kind == "conflict"
    assert exc_info.value.failure.details["reason"] == "source_ref_binding_invalid"


def test_candidate_target_filters_unrelated_refs_and_rejects_malformed_identity() -> None:
    adapter = github.GitHubReleaseAdapter(_config(), _SecretProvider(), transport=_QueuedTransport([]))
    search = PullRequestSearch(_repository(), "main", "codex/release-1")
    valid = {
        "number": 17,
        "base": {"ref": "main", "repo": {"id": 42}},
        "head": {"ref": "codex/release-1", "sha": _SHA, "repo": {"id": 42}},
    }

    assert adapter._candidate_target({**valid, "base": {"ref": "develop", "repo": {"id": 42}}}, search) is None
    assert (
        adapter._candidate_target(
            {**valid, "head": {"ref": "codex/other", "sha": _SHA, "repo": {"id": 42}}},
            search,
        )
        is None
    )
    assert adapter._candidate_target({**valid, "number": True}, search) is None
    assert adapter._candidate_target(valid, search) is not None

    assert (
        adapter._publication_candidate_target(
            {**valid, "base": {"ref": "develop", "repo": {"id": 42}}},
            search,
            "lookup",
        )
        is None
    )
    assert (
        adapter._publication_candidate_target(
            {**valid, "head": {"ref": "codex/other", "sha": _SHA, "repo": {"id": 42}}},
            search,
            "lookup",
        )
        is None
    )
    with pytest.raises(AdapterError) as exc_info:
        adapter._publication_candidate_target({**valid, "number": True}, search, "lookup")
    assert exc_info.value.failure.details["reason"] == "candidate_pull_request_identity_invalid"
