"""Contract tests for exact source-candidate publication identities."""

from __future__ import annotations

import hashlib

import pytest
from foundry_lite.application.ports.source_control_candidate import (
    SourceCandidateCommitBinding,
    SourceCandidateManifest,
    SourceCandidatePublicationReceipt,
    SourceCandidatePublicationRequest,
    SourceCandidatePublicationStatus,
    SourceRepositoryRef,
)

_BASE_SHA = "a" * 40
_HEAD_SHA = "b" * 40
_TREE_SHA = "c" * 40


def _repository() -> SourceRepositoryRef:
    return SourceRepositoryRef("github", 42, "example", "foundry-lite")


def _manifest() -> SourceCandidateManifest:
    content = b'{"schemaVersion":"foundry-lite-governed-release/v1"}\n'
    fingerprint = f"sha256:{hashlib.sha256(content).hexdigest()}"
    return SourceCandidateManifest(
        ".foundry-lite/releases/pipeline/pipeprop_123.json",
        content,
        fingerprint,
    )


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


def test_candidate_manifest_binds_exact_bounded_bytes_without_repr_exposure() -> None:
    manifest = _manifest()

    assert manifest.manifest_fingerprint.startswith("sha256:")
    assert manifest.canonical_bytes not in repr(manifest).encode()

    with pytest.raises(ValueError, match="fingerprint"):
        SourceCandidateManifest(manifest.artifact_path, manifest.canonical_bytes, f"sha256:{'0' * 64}")


def test_candidate_request_binds_manifest_path_to_release_and_proposal() -> None:
    request = _request()

    assert request.manifest.artifact_path.endswith("/pipeline/pipeprop_123.json")

    with pytest.raises(ValueError, match="proposal identity"):
        SourceCandidatePublicationRequest(
            request.repository,
            "ontology",
            request.proposal_id,
            request.expected_base_ref,
            request.expected_head_ref,
            request.expected_base_sha,
            request.manifest,
            request.idempotency_key,
        )


def test_absent_receipt_is_typed_not_published_state_without_remote_identity() -> None:
    request = _request()
    receipt = SourceCandidatePublicationReceipt(
        SourceCandidatePublicationStatus.ABSENT,
        request.repository,
        request.expected_base_ref,
        request.expected_head_ref,
        request.expected_base_sha,
        request.manifest.manifest_fingerprint,
        request.idempotency_key,
        evidence={"reason": "not_published"},
    )

    assert receipt.status is SourceCandidatePublicationStatus.ABSENT
    assert receipt.evidence["reason"] == "not_published"
    with pytest.raises(ValueError, match="only a published"):
        receipt.to_pull_request_target()


def test_published_receipt_builds_merge_target_with_exact_commit_binding() -> None:
    request = _request()
    binding = SourceCandidateCommitBinding(_BASE_SHA, _TREE_SHA, request.expected_head_ref, request.manifest)
    receipt = SourceCandidatePublicationReceipt(
        SourceCandidatePublicationStatus.PUBLISHED,
        request.repository,
        request.expected_base_ref,
        request.expected_head_ref,
        request.expected_base_sha,
        request.manifest.manifest_fingerprint,
        request.idempotency_key,
        head_sha=_HEAD_SHA,
        pull_number=17,
        commit_binding=binding,
    )

    target = receipt.to_pull_request_target()

    assert target.expected_head_sha == _HEAD_SHA
    assert target.candidate_binding == binding


def test_receipt_rejects_impossible_partial_and_absent_states() -> None:
    request = _request()

    with pytest.raises(ValueError, match="partial"):
        SourceCandidatePublicationReceipt(
            SourceCandidatePublicationStatus.PARTIAL,
            request.repository,
            request.expected_base_ref,
            request.expected_head_ref,
            request.expected_base_sha,
            request.manifest.manifest_fingerprint,
            request.idempotency_key,
        )

    with pytest.raises(ValueError, match="absent"):
        SourceCandidatePublicationReceipt(
            SourceCandidatePublicationStatus.ABSENT,
            request.repository,
            request.expected_base_ref,
            request.expected_head_ref,
            request.expected_base_sha,
            request.manifest.manifest_fingerprint,
            request.idempotency_key,
            head_sha=_HEAD_SHA,
        )
