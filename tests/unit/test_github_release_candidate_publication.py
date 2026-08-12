from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from urllib.parse import urlsplit

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailureContract
from foundry_lite.application.ports.secret_provider import SecretValue
from foundry_lite.application.ports.source_control_candidate import (
    SourceCandidateManifest,
    SourceCandidatePublicationRequest,
    SourceCandidatePublicationStatus,
    SourceRepositoryRef,
)
from foundry_lite.infrastructure.adapters.github_release import (
    GitHubHttpRequest,
    GitHubHttpResponse,
    GitHubReleaseAdapter,
    GitHubReleaseConfig,
    GitHubTransportFailure,
)

_BASE_SHA = "a" * 40
_HEAD_SHA = "b" * 40
_HEAD_TREE_SHA = "c" * 40
_BASE_TREE_SHA = "d" * 40
_MANIFEST_PATH = ".foundry-lite/releases/pipeline/pipeprop_123.json"
_MANIFEST_BYTES = b'{"proposalId":"pipeprop_123","schemaVersion":"foundry-lite-governed-release/v1"}\n'


class _SecretProvider:
    profile_name = "test-secret"

    def get_secret(self, name: str, *, version: str | None = None) -> SecretValue:
        return SecretValue(name=name, version=version or "v1", value="github-token")

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())


class _CandidateGitHubTransport:
    def __init__(self) -> None:
        self.requests: list[GitHubHttpRequest] = []
        self.is_branch_published = False
        self.is_pull_published = False
        self.is_parent_divergent = False
        self.has_extra_file = False
        self.is_tree_truncated = False
        self.pull_count = 1
        self.lose_ref_response = False
        self.lose_pull_response = False
        self.manifest_bytes = _MANIFEST_BYTES

    def __call__(self, request: GitHubHttpRequest) -> GitHubHttpResponse:
        self.requests.append(request)
        path = urlsplit(request.url).path
        if request.method == "GET":
            return self._get(path)
        if path.endswith("/git/blobs"):
            return self._post_blob(request)
        if path.endswith("/git/trees"):
            return _response(201, {"sha": _HEAD_TREE_SHA}, "TREE")
        if path.endswith("/git/commits"):
            return _response(201, {"sha": _HEAD_SHA}, "COMMIT")
        if path.endswith("/git/refs"):
            self.is_branch_published = True
            if self.lose_ref_response:
                self.lose_ref_response = False
                raise GitHubTransportFailure("timeout")
            return _response(201, {"ref": "refs/heads/codex/orders", "object": {"sha": _HEAD_SHA}}, "REF")
        if path.endswith("/pulls"):
            self.is_pull_published = True
            if self.lose_pull_response:
                self.lose_pull_response = False
                raise GitHubTransportFailure("timeout")
            return _response(201, self._pull(), "PULL-CREATE")
        raise AssertionError(f"unexpected request: {request.method} {path}")

    def _get(self, path: str) -> GitHubHttpResponse:
        if path.endswith("/git/ref/heads/main"):
            return _response(200, _ref("main", _BASE_SHA), "BASE-REF")
        if path.endswith("/git/ref/heads/codex%2Forders"):
            if not self.is_branch_published:
                return _response(404, {}, "HEAD-ABSENT")
            return _response(200, _ref("codex/orders", _HEAD_SHA), "HEAD-REF")
        if path.endswith(f"/git/commits/{_BASE_SHA}"):
            return _response(200, _commit(_BASE_SHA, _BASE_TREE_SHA, ()), "BASE-COMMIT")
        if path.endswith(f"/git/commits/{_HEAD_SHA}"):
            parent = "e" * 40 if self.is_parent_divergent else _BASE_SHA
            return _response(200, _commit(_HEAD_SHA, _HEAD_TREE_SHA, (parent,)), "HEAD-COMMIT")
        if path.endswith(f"/git/trees/{_BASE_TREE_SHA}"):
            return _response(200, _tree(_BASE_TREE_SHA, [], self.is_tree_truncated), "BASE-TREE")
        if path.endswith(f"/git/trees/{_HEAD_TREE_SHA}"):
            entry = {
                "path": _MANIFEST_PATH,
                "mode": "100644",
                "type": "blob",
                "sha": _git_blob_sha(self.manifest_bytes),
                "size": len(self.manifest_bytes),
            }
            return _response(200, _tree(_HEAD_TREE_SHA, [entry], self.is_tree_truncated), "HEAD-TREE")
        if "/git/blobs/" in path:
            return _response(200, _blob(self.manifest_bytes), "BLOB")
        if "/compare/" in path:
            return _response(200, self._compare(), "COMPARE")
        if path.endswith("/pulls"):
            pulls = [self._pull() for _ in range(self.pull_count)] if self.is_pull_published else []
            return _response(200, pulls, "PULL-SEARCH")
        if path.endswith("/pulls/17"):
            return _response(200, self._pull(), "PULL")
        if "/rules/branches/" in path:
            return _response(200, [], "RULES")
        if path.endswith("/branches/main/protection"):
            return _response(404, {}, "NO-PROTECTION")
        if path.endswith("/pulls/17/reviews"):
            return _response(200, [], "REVIEWS")
        raise AssertionError(f"unexpected GET: {path}")

    def _post_blob(self, request: GitHubHttpRequest) -> GitHubHttpResponse:
        assert request.body is not None
        content = request.body["content"]
        assert isinstance(content, str)
        self.manifest_bytes = base64.b64decode(content)
        return _response(201, {"sha": _git_blob_sha(self.manifest_bytes)}, "BLOB-CREATE")

    def _compare(self) -> Mapping[str, object]:
        files = [{"filename": _MANIFEST_PATH, "status": "added", "sha": _git_blob_sha(self.manifest_bytes)}]
        if self.has_extra_file:
            files.append({"filename": "README.md", "status": "modified", "sha": "f" * 40})
        return {
            "status": "ahead",
            "ahead_by": 1,
            "behind_by": 0,
            "total_commits": 1,
            "base_commit": {"sha": _BASE_SHA},
            "merge_base_commit": {"sha": _BASE_SHA},
            "commits": [{"sha": _HEAD_SHA}],
            "files": files,
        }

    def _pull(self) -> dict[str, object]:
        return {
            "number": 17,
            "state": "open",
            "draft": False,
            "merged": False,
            "mergeable": True,
            "mergeable_state": "clean",
            "base": {"ref": "main", "sha": _BASE_SHA, "repo": {"id": 42}},
            "head": {"ref": "codex/orders", "sha": _HEAD_SHA, "repo": {"id": 42}},
            "user": {"id": 7, "login": "release-bot"},
            "merge_commit_sha": None,
            "merged_at": None,
        }


def _repository() -> SourceRepositoryRef:
    return SourceRepositoryRef("github", 42, "example", "foundry-lite")


def _manifest(content: bytes = _MANIFEST_BYTES) -> SourceCandidateManifest:
    return SourceCandidateManifest(_MANIFEST_PATH, content, f"sha256:{hashlib.sha256(content).hexdigest()}")


def _request() -> SourceCandidatePublicationRequest:
    return SourceCandidatePublicationRequest(
        _repository(),
        "pipeline",
        "pipeprop_123",
        "main",
        "codex/orders",
        _BASE_SHA,
        _manifest(),
        "publish-pipeprop-123",
    )


def _adapter(transport: _CandidateGitHubTransport) -> GitHubReleaseAdapter:
    config = GitHubReleaseConfig(
        _repository(),
        "github_installation_token",
        minimum_approvals=0,
        is_bypass_policy_verified=True,
    )
    return GitHubReleaseAdapter(config, _SecretProvider(), transport=transport)


def _response(status: int, body: object, request_id: str) -> GitHubHttpResponse:
    return GitHubHttpResponse(status, {"x-github-request-id": request_id}, body)


def _ref(name: str, sha: str) -> dict[str, object]:
    return {"ref": f"refs/heads/{name}", "object": {"type": "commit", "sha": sha}}


def _commit(sha: str, tree_sha: str, parents: tuple[str, ...]) -> dict[str, object]:
    return {"sha": sha, "tree": {"sha": tree_sha}, "parents": [{"sha": parent} for parent in parents]}


def _tree(sha: str, entries: list[object], is_truncated: bool) -> dict[str, object]:
    return {"sha": sha, "truncated": is_truncated, "tree": entries}


def _blob(content: bytes) -> dict[str, object]:
    return {
        "sha": _git_blob_sha(content),
        "size": len(content),
        "encoding": "base64",
        "content": base64.b64encode(content).decode("ascii"),
    }


def _git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


def _post_paths(transport: _CandidateGitHubTransport) -> list[str]:
    return [urlsplit(request.url).path for request in transport.requests if request.method == "POST"]


def test_candidate_lookup_reports_absent_as_not_published_without_mutation() -> None:
    transport = _CandidateGitHubTransport()

    receipt = _adapter(transport).lookup_pull_request_candidate(_request())

    assert receipt.status is SourceCandidatePublicationStatus.ABSENT
    assert receipt.evidence["reason"] == "not_published"
    assert _post_paths(transport) == []


def test_candidate_publish_creates_exact_git_objects_ref_and_pull_then_replays_read_only() -> None:
    transport = _CandidateGitHubTransport()
    adapter = _adapter(transport)

    receipt = adapter.publish_pull_request_candidate(_request())
    first_posts = _post_paths(transport)
    replay = adapter.publish_pull_request_candidate(_request())

    assert receipt.status is SourceCandidatePublicationStatus.PUBLISHED
    assert receipt.provider_request_id == "PULL-CREATE"
    assert receipt.to_pull_request_target().candidate_binding is not None
    assert first_posts[-5:] == [
        "/repos/example/foundry-lite/git/blobs",
        "/repos/example/foundry-lite/git/trees",
        "/repos/example/foundry-lite/git/commits",
        "/repos/example/foundry-lite/git/refs",
        "/repos/example/foundry-lite/pulls",
    ]
    assert replay.to_pull_request_target() == receipt.to_pull_request_target()
    assert replay.manifest_fingerprint == receipt.manifest_fingerprint
    assert _post_paths(transport) == first_posts
    assert _MANIFEST_BYTES.decode() not in repr(transport.requests)


def test_exact_branch_without_pull_recovers_by_creating_only_pull_request() -> None:
    transport = _CandidateGitHubTransport()
    transport.is_branch_published = True

    receipt = _adapter(transport).publish_pull_request_candidate(_request())

    assert receipt.status is SourceCandidatePublicationStatus.PUBLISHED
    assert _post_paths(transport) == ["/repos/example/foundry-lite/pulls"]


@pytest.mark.parametrize("lost_mutation", ["ref", "pull"])
def test_candidate_publication_reconciles_lost_mutation_response(lost_mutation: str) -> None:
    transport = _CandidateGitHubTransport()
    transport.lose_ref_response = lost_mutation == "ref"
    transport.lose_pull_response = lost_mutation == "pull"

    receipt = _adapter(transport).publish_pull_request_candidate(_request())

    assert receipt.status is SourceCandidatePublicationStatus.PUBLISHED
    assert transport.is_branch_published is True
    assert transport.is_pull_published is True


def test_candidate_lookup_rejects_divergent_branch_and_duplicate_pull_requests() -> None:
    transport = _CandidateGitHubTransport()
    transport.is_branch_published = True
    transport.is_parent_divergent = True

    with pytest.raises(AdapterError) as divergent:
        _adapter(transport).lookup_pull_request_candidate(_request())

    assert divergent.value.failure.kind == "conflict"
    assert _post_paths(transport) == []

    transport = _CandidateGitHubTransport()
    transport.is_branch_published = True
    transport.is_pull_published = True
    transport.pull_count = 2
    with pytest.raises(AdapterError, match="multiple_candidate_pull_requests"):
        _adapter(transport).lookup_pull_request_candidate(_request())


def test_merge_time_inspection_rejects_extra_file_or_changed_manifest_bytes() -> None:
    transport = _CandidateGitHubTransport()
    adapter = _adapter(transport)
    published = adapter.publish_pull_request_candidate(_request())
    target = published.to_pull_request_target()

    assert adapter.inspect_pull_request(target).is_ready_to_merge is True

    transport.has_extra_file = True
    with pytest.raises(AdapterError, match="candidate_commit_not_manifest_only"):
        adapter.inspect_pull_request(target)

    transport.has_extra_file = False
    transport.manifest_bytes = b"{}\n"
    with pytest.raises(AdapterError, match="candidate_manifest_bytes_changed"):
        adapter.inspect_pull_request(target)


def test_merge_readback_reverifies_exact_candidate_commit_after_head_ref_deletion() -> None:
    transport = _CandidateGitHubTransport()
    adapter = _adapter(transport)
    target = adapter.publish_pull_request_candidate(_request()).to_pull_request_target()
    transport.is_branch_published = False

    receipt = adapter.lookup_merge(target)

    assert receipt.status.value == "absent"
    assert not any("/git/ref/heads/codex%2Forders" in request.url for request in transport.requests[-5:])
    transport.manifest_bytes = b"{}\n"
    with pytest.raises(AdapterError, match="candidate_manifest_bytes_changed"):
        adapter.lookup_merge(target)


def test_candidate_tree_truncation_fails_closed_without_publication() -> None:
    transport = _CandidateGitHubTransport()
    transport.is_tree_truncated = True

    with pytest.raises(AdapterError) as excinfo:
        _adapter(transport).publish_pull_request_candidate(_request())

    assert excinfo.value.failure.kind == "unsupported"
    assert _post_paths(transport) == []
