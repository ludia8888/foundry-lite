from __future__ import annotations

import hashlib
from typing import cast

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.source_control_candidate import (
    PullRequestTarget,
    SourceCandidateCommitBinding,
    SourceCandidateManifest,
    SourceCandidatePublicationReceipt,
    SourceCandidatePublicationRequest,
    SourceCandidatePublicationStatus,
    SourceRefSnapshot,
    SourceRepositoryRef,
    freeze_source_control_evidence,
)
from foundry_lite.application.ports.source_control_release import (
    BranchRuleEvidence,
    PullRequestReviewEvidence,
    PullRequestSearch,
    PullRequestSnapshot,
    RequiredCheckEvidence,
    SourceControlMergeMethod,
    SourceControlMergeReceipt,
    SourceControlMergeRequest,
    SourceControlMergeStatus,
    SourceControlReviewDecision,
    UnavailableSourceControlReleasePort,
)

_BASE = "a" * 40
_HEAD = "b" * 40
_TREE = "c" * 40
_MERGE = "d" * 40
_RULES = f"sha256:{'e' * 64}"
_CHECKS = f"sha256:{'f' * 64}"


def _repository() -> SourceRepositoryRef:
    return SourceRepositoryRef("github", 42, "acme", "foundry-lite")


def _manifest(proposal: str = "proposal-1", kind: str = "pipeline") -> SourceCandidateManifest:
    content = b'{"schemaVersion":"candidate/v1"}\n'
    return SourceCandidateManifest(
        f".foundry-lite/releases/{kind}/{proposal}.json",
        content,
        f"sha256:{hashlib.sha256(content).hexdigest()}",
    )


def _binding() -> SourceCandidateCommitBinding:
    return SourceCandidateCommitBinding(_BASE, _TREE, "codex/proposal-1", _manifest())


def _target() -> PullRequestTarget:
    return PullRequestTarget(_repository(), 17, "main", _HEAD)


def _approval(**changes: object) -> PullRequestReviewEvidence:
    values: dict[str, object] = {
        "reviewer_id": 8,
        "reviewer_login": "reviewer",
        "state": "APPROVED",
        "commit_sha": _HEAD,
        "submitted_at": None,
    }
    values.update(changes)
    return PullRequestReviewEvidence(**values)  # type: ignore[arg-type]


def _check(**changes: object) -> RequiredCheckEvidence:
    values: dict[str, object] = {
        "context": "quality-gate",
        "commit_sha": _HEAD,
        "status": "completed",
        "conclusion": "success",
        "source": "github_check_run",
        "source_app_id": 15368,
        "is_successful": True,
    }
    values.update(changes)
    return RequiredCheckEvidence(**values)  # type: ignore[arg-type]


def _snapshot(**changes: object) -> PullRequestSnapshot:
    values: dict[str, object] = {
        "target": _target(),
        "state": "open",
        "is_draft": False,
        "is_merged": False,
        "base_sha": _BASE,
        "head_ref": "codex/proposal-1",
        "author_id": 7,
        "author_login": "author",
        "mergeable_state": "clean",
        "test_merge_commit_sha": None,
        "checks_commit_sha": _HEAD,
        "review_decision": SourceControlReviewDecision.APPROVED,
        "required_approval_count": 1,
        "approvals": (_approval(),),
        "active_rules": (),
        "required_checks": (_check(),),
        "is_merge_queue_required": False,
        "rules_fingerprint": _RULES,
        "checks_fingerprint": _CHECKS,
        "blocking_reasons": (),
        "is_ready_to_merge": True,
    }
    values.update(changes)
    return PullRequestSnapshot(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("provider", "repository_id", "owner", "name"),
    [
        ("", 42, "acme", "repo"),
        ("github", 0, "acme", "repo"),
        ("github", True, "acme", "repo"),
        ("github", 42, "", "repo"),
        ("github", 42, "acme", ""),
    ],
)
def test_repository_identity_rejects_blank_or_boolean_coordinates(
    provider: str, repository_id: int, owner: str, name: str
) -> None:
    with pytest.raises(ValueError):
        SourceRepositoryRef(provider, repository_id, owner, name)


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("candidate.json", b"{}"),
        (".foundry-lite/releases/pipeline/p.json", b""),
        (".foundry-lite/releases/pipeline/p.json", b"x" * (64 * 1024 + 1)),
    ],
)
def test_candidate_manifest_rejects_invalid_path_or_unbounded_bytes(path: str, content: bytes) -> None:
    with pytest.raises(ValueError):
        SourceCandidateManifest(path, content, f"sha256:{hashlib.sha256(content).hexdigest()}")


def test_candidate_manifest_requires_immutable_bytes_and_exact_digest() -> None:
    with pytest.raises(ValueError, match="immutable bytes"):
        SourceCandidateManifest(
            ".foundry-lite/releases/pipeline/p.json",
            cast(bytes, bytearray(b"{}")),
            f"sha256:{'0' * 64}",
        )
    with pytest.raises(ValueError, match="fingerprint"):
        SourceCandidateManifest(".foundry-lite/releases/pipeline/p.json", b"{}", f"sha256:{'0' * 64}")


@pytest.mark.parametrize(
    ("base", "tree", "head"),
    [
        ("short", _TREE, "codex/p"),
        (_BASE, "short", "codex/p"),
        (_BASE, _TREE, "bad..ref"),
        (_BASE, _TREE, "codex/p.lock"),
    ],
)
def test_candidate_commit_binding_requires_full_shas_and_safe_head(base: str, tree: str, head: str) -> None:
    with pytest.raises(ValueError):
        SourceCandidateCommitBinding(base, tree, head, _manifest())


@pytest.mark.parametrize(
    ("pull", "base", "head"),
    [(0, "main", _HEAD), (True, "main", _HEAD), (17, " ", _HEAD), (17, "main", "short")],
)
def test_pull_request_target_rejects_ambiguous_or_unsealed_identity(pull: int, base: str, head: str) -> None:
    with pytest.raises(ValueError):
        PullRequestTarget(_repository(), pull, base, head)


@pytest.mark.parametrize(
    ("ref", "commit", "tree"),
    [("bad..ref", _HEAD, _TREE), ("main", "short", _TREE), ("main", _HEAD, "short")],
)
def test_source_ref_snapshot_rejects_unsafe_or_unsealed_identity(ref: str, commit: str, tree: str) -> None:
    with pytest.raises(ValueError):
        SourceRefSnapshot(_repository(), ref, commit, tree)


def _publication_request(**changes: object) -> SourceCandidatePublicationRequest:
    values: dict[str, object] = {
        "repository": _repository(),
        "release_kind": "pipeline",
        "proposal_id": "proposal-1",
        "expected_base_ref": "main",
        "expected_head_ref": "codex/proposal-1",
        "expected_base_sha": _BASE,
        "manifest": _manifest(),
        "idempotency_key": "publish-1",
    }
    values.update(changes)
    return SourceCandidatePublicationRequest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"release_kind": "model"},
        {"proposal_id": "bad proposal"},
        {"expected_base_ref": "bad..ref"},
        {"expected_head_ref": "codex/p.lock"},
        {"expected_head_ref": "main"},
        {"expected_base_sha": "short"},
        {"idempotency_key": " "},
        {"manifest": _manifest("other")},
        {"manifest": _manifest("proposal-1", "ontology")},
    ],
)
def test_candidate_publication_rejects_redirected_or_unbound_coordinates(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _publication_request(**changes)


def _receipt(**changes: object) -> SourceCandidatePublicationReceipt:
    values: dict[str, object] = {
        "status": SourceCandidatePublicationStatus.PUBLISHED,
        "repository": _repository(),
        "expected_base_ref": "main",
        "expected_head_ref": "codex/proposal-1",
        "expected_base_sha": _BASE,
        "manifest_artifact_path": _manifest().artifact_path,
        "manifest_fingerprint": _manifest().manifest_fingerprint,
        "idempotency_key": "publish-1",
        "head_sha": _HEAD,
        "pull_number": 17,
        "commit_binding": _binding(),
    }
    values.update(changes)
    return SourceCandidatePublicationReceipt(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "published"},
        {"expected_base_ref": "bad..ref"},
        {"expected_head_ref": "bad..ref"},
        {"expected_base_sha": "short"},
        {"manifest_fingerprint": "sha256:short"},
        {"idempotency_key": " "},
        {"head_sha": "short"},
        {"pull_number": True},
        {"pull_number": 0},
        {"head_sha": None},
        {"pull_number": None},
        {"commit_binding": None},
    ],
)
def test_published_candidate_receipt_rejects_invalid_or_incomplete_state(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _receipt(**changes)


def test_candidate_receipt_rejects_partial_absent_and_ambiguous_shape_confusion() -> None:
    with pytest.raises(ValueError, match="partial"):
        _receipt(status=SourceCandidatePublicationStatus.PARTIAL, pull_number=17)
    with pytest.raises(ValueError, match="absent"):
        _receipt(status=SourceCandidatePublicationStatus.ABSENT)
    ambiguous = _receipt(status=SourceCandidatePublicationStatus.AMBIGUOUS)
    assert ambiguous.head_sha == _HEAD


@pytest.mark.parametrize(
    "binding",
    [
        SourceCandidateCommitBinding("f" * 40, _TREE, "codex/proposal-1", _manifest()),
        SourceCandidateCommitBinding(_BASE, _TREE, "codex/other", _manifest()),
        SourceCandidateCommitBinding(_BASE, _TREE, "codex/proposal-1", _manifest("other")),
    ],
)
def test_candidate_receipt_rejects_commit_binding_drift(binding: SourceCandidateCommitBinding) -> None:
    with pytest.raises(ValueError, match="binding"):
        _receipt(commit_binding=binding)


def test_source_control_evidence_is_recursively_frozen_and_json_only() -> None:
    frozen = freeze_source_control_evidence({"nested": {"rows": [1, {"ok": True}]}})
    assert frozen["nested"]["rows"] == (1, {"ok": True})  # type: ignore[index]
    with pytest.raises(ValueError, match="keys"):
        freeze_source_control_evidence(cast(dict[str, object], {1: "bad"}))
    with pytest.raises(ValueError, match="JSON-compatible"):
        freeze_source_control_evidence({"bad": {1, 2}})


@pytest.mark.parametrize(("base", "head"), [("", "codex/p"), ("main", "")])
def test_pull_request_search_requires_two_explicit_refs(base: str, head: str) -> None:
    with pytest.raises(ValueError):
        PullRequestSearch(_repository(), base, head)


@pytest.mark.parametrize(
    "changes",
    [
        {"rule_type": ""},
        {"source_type": ""},
        {"source": ""},
        {"ruleset_id": 0},
        {"ruleset_id": True},
        {"parameters_fingerprint": "sha256:short"},
    ],
)
def test_branch_rule_evidence_rejects_incomplete_provider_identity(changes: dict[str, object]) -> None:
    values = {
        "rule_type": "pull_request",
        "ruleset_id": 42,
        "source_type": "Repository",
        "source": "acme/foundry-lite",
        "parameters_fingerprint": _RULES,
    }
    values.update(changes)
    with pytest.raises(ValueError):
        BranchRuleEvidence(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [{"context": ""}, {"source": ""}, {"commit_sha": "short"}, {"source_app_id": 0}, {"source_app_id": True}],
)
def test_required_check_evidence_rejects_incomplete_provider_identity(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _check(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"reviewer_id": 0},
        {"reviewer_id": True},
        {"reviewer_login": ""},
        {"state": "COMMENTED"},
        {"commit_sha": "short"},
    ],
)
def test_review_evidence_rejects_untrusted_identity_or_state(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _approval(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"base_sha": "short"},
        {"author_id": 0},
        {"author_id": True},
        {"author_login": ""},
        {"test_merge_commit_sha": "short"},
        {"checks_commit_sha": "short"},
        {"checks_commit_sha": _MERGE},
        {"required_approval_count": True},
        {"required_approval_count": -1},
        {"rules_fingerprint": "sha256:short"},
        {"checks_fingerprint": "sha256:short"},
        {"blocking_reasons": ("blocked",)},
    ],
)
def test_pull_request_snapshot_rejects_invalid_identity_or_derived_readiness(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _snapshot(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "closed"},
        {"is_draft": True},
        {"is_merged": True},
        {"is_merge_queue_required": True},
        {"mergeable_state": "blocked"},
        {"review_decision": SourceControlReviewDecision.CHANGES_REQUESTED},
        {"required_approval_count": 2},
        {"approvals": (_approval(commit_sha=_BASE),)},
        {"approvals": (_approval(state="DISMISSED"),)},
        {"required_checks": (_check(is_successful=False),)},
        {"required_checks": (_check(source="missing"),)},
        {"required_checks": (_check(status="queued"),)},
        {"required_checks": (_check(conclusion="failure"),)},
        {"required_checks": (_check(commit_sha=_BASE),)},
    ],
)
def test_ready_snapshot_requires_open_current_independent_success_evidence(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="missing approval or policy evidence"):
        _snapshot(**changes)


def test_test_merge_commit_can_be_the_authoritative_checks_commit() -> None:
    snapshot = _snapshot(
        test_merge_commit_sha=_MERGE,
        checks_commit_sha=_MERGE,
        required_checks=(_check(commit_sha=_MERGE, conclusion="neutral"),),
    )
    assert snapshot.is_ready_to_merge is True


@pytest.mark.parametrize(
    "changes",
    [
        {"idempotency_key": " "},
        {"merge_method": "squash"},
        {"expected_base_sha": "short"},
        {"expected_rules_fingerprint": "sha256:short"},
        {"expected_checks_fingerprint": "sha256:short"},
    ],
)
def test_merge_request_rejects_untyped_or_unsealed_coordinates(changes: dict[str, object]) -> None:
    values: dict[str, object] = {
        "target": _target(),
        "merge_method": SourceControlMergeMethod.SQUASH,
        "idempotency_key": "merge-1",
        "expected_base_sha": _BASE,
        "expected_rules_fingerprint": _RULES,
        "expected_checks_fingerprint": _CHECKS,
    }
    values.update(changes)
    with pytest.raises(ValueError):
        SourceControlMergeRequest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "landed"},
        {"repository_id": 0},
        {"repository_id": True},
        {"pull_number": 0},
        {"pull_number": True},
        {"head_sha": "short"},
        {"idempotency_key": " "},
    ],
)
def test_merge_receipt_rejects_untyped_or_unsealed_identity(changes: dict[str, object]) -> None:
    values: dict[str, object] = {
        "status": SourceControlMergeStatus.LANDED,
        "repository_id": 42,
        "pull_number": 17,
        "head_sha": _HEAD,
        "merge_commit_sha": _MERGE,
        "idempotency_key": "merge-1",
    }
    values.update(changes)
    with pytest.raises(ValueError):
        SourceControlMergeReceipt(**values)  # type: ignore[arg-type]


def test_merge_receipt_rejects_outcome_field_contradictions() -> None:
    with pytest.raises(ValueError, match="landed receipt requires"):
        SourceControlMergeReceipt(SourceControlMergeStatus.LANDED, 42, 17, _HEAD, merge_commit_sha="short")
    with pytest.raises(ValueError, match="non-landed receipt cannot carry merge_commit_sha"):
        SourceControlMergeReceipt(SourceControlMergeStatus.ABSENT, 42, 17, _HEAD, merge_commit_sha=_MERGE)
    with pytest.raises(ValueError, match="non-landed receipt cannot carry merged_at"):
        SourceControlMergeReceipt(SourceControlMergeStatus.AMBIGUOUS, 42, 17, _HEAD, merged_at="now")


@pytest.mark.parametrize(
    "method_name",
    [
        "inspect_source_ref",
        "publish_pull_request_candidate",
        "lookup_pull_request_candidate",
        "find_pull_request",
        "inspect_pull_request",
        "merge_pull_request",
        "lookup_merge",
    ],
)
def test_unavailable_source_control_port_fails_every_operation_closed(method_name: str) -> None:
    port = UnavailableSourceControlReleasePort()
    arguments: dict[str, tuple[object, ...]] = {
        "inspect_source_ref": (_repository(), "main"),
        "publish_pull_request_candidate": (_publication_request(),),
        "lookup_pull_request_candidate": (_publication_request(),),
        "find_pull_request": (PullRequestSearch(_repository(), "main", "codex/p"),),
        "inspect_pull_request": (_target(),),
        "merge_pull_request": (
            SourceControlMergeRequest(
                _target(),
                SourceControlMergeMethod.SQUASH,
                "merge-1",
                _BASE,
                _RULES,
                _CHECKS,
            ),
        ),
        "lookup_merge": (_target(),),
    }
    with pytest.raises(AdapterError) as caught:
        getattr(port, method_name)(*arguments[method_name])
    assert caught.value.failure.kind == "unsupported"
    assert caught.value.failure.operation == method_name
