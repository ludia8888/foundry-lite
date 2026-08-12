"""Pure bindings and projections for durable source-candidate publication."""

from __future__ import annotations

import binascii
from base64 import b64decode, b64encode
from collections.abc import Mapping

from foundry_lite.application.ports.governed_release_delivery_config import GovernedReleaseDeliveryConfig
from foundry_lite.application.ports.release_delivery_repository import ReleaseDeliveryRecord
from foundry_lite.application.ports.source_control_candidate import (
    PullRequestTarget,
    SourceCandidateCommitBinding,
    SourceCandidateManifest,
    SourceCandidatePublicationReceipt,
    SourceCandidatePublicationRequest,
)
from foundry_lite.application.ports.source_control_release import SourceRepositoryRef
from foundry_lite.application.services.aip.external_release_delivery_support import mutation_identity
from foundry_lite.application.services.aip.governed_release_source_manifest import (
    GovernedReleaseSourceManifest,
    build_governed_release_source_manifest,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

JsonObject = Mapping[str, object]


def source_publication_manifest(
    ctx: RequestContext,
    release_kind: str,
    proposal: JsonObject,
    config: GovernedReleaseDeliveryConfig,
) -> tuple[GovernedReleaseSourceManifest, SourceCandidateManifest]:
    """Build the exact public-safe artifact from server-read proposal state."""

    repository = _repository(config)
    head_ref = config.source_head_ref(_branch_name(proposal))
    governed = build_governed_release_source_manifest(
        ctx,
        release_kind,
        proposal,
        repository,
        config.source_base_ref,
        head_ref,
    )
    return governed, SourceCandidateManifest(
        governed.artifact_path,
        governed.canonical_bytes,
        governed.manifest_fingerprint,
    )


def source_publication_request_for_proposal(
    release_kind: str,
    proposal: JsonObject,
    config: GovernedReleaseDeliveryConfig,
    manifest: SourceCandidateManifest,
    expected_base_sha: str,
    idempotency_key: str,
) -> SourceCandidatePublicationRequest:
    """Build a request using the proposal's server-resolved branch name."""

    return SourceCandidatePublicationRequest(
        repository=_repository(config),
        release_kind=release_kind,
        proposal_id=_text(proposal, "id"),
        expected_base_ref=config.source_base_ref,
        expected_head_ref=config.source_head_ref(_branch_name(proposal)),
        expected_base_sha=expected_base_sha,
        manifest=manifest,
        idempotency_key=idempotency_key,
    )


def source_publication_target_ref(request: SourceCandidatePublicationRequest) -> dict[str, object]:
    repository = request.repository
    return {
        "repositoryId": repository.repository_id,
        "repositoryOwner": repository.owner,
        "repositoryName": repository.name,
        "baseRef": request.expected_base_ref,
        "headRef": request.expected_head_ref,
        "baseSha": request.expected_base_sha,
    }


def source_publication_candidate_ref(
    request: SourceCandidatePublicationRequest,
    governed: GovernedReleaseSourceManifest,
) -> dict[str, object]:
    return {
        "releaseKind": request.release_kind,
        "proposalId": request.proposal_id,
        "artifactPath": request.manifest.artifact_path,
        "manifestFingerprint": request.manifest.manifest_fingerprint,
        "manifestCanonicalBytesBase64": b64encode(request.manifest.canonical_bytes).decode("ascii"),
        "proposalContentFingerprint": governed.proposal_content_fingerprint,
    }


def source_publication_request_from_record(
    row: ReleaseDeliveryRecord,
    config: GovernedReleaseDeliveryConfig,
) -> SourceCandidatePublicationRequest:
    """Rehydrate exact immutable publication input without reading proposal state."""

    request = SourceCandidatePublicationRequest(
        repository=_repository(config),
        release_kind=row.release_kind,
        proposal_id=row.proposal_id,
        expected_base_ref=config.source_base_ref,
        expected_head_ref=_text(row.target_ref, "headRef"),
        expected_base_sha=_text(row.target_ref, "baseSha"),
        manifest=_stored_manifest(row),
        idempotency_key=row.idempotency_key,
    )
    require_publication_candidate_binding(row, request)
    return request


def source_publication_target_from_record(
    row: ReleaseDeliveryRecord,
    request: SourceCandidatePublicationRequest,
) -> PullRequestTarget:
    """Restore the exact PR and Git-object binding from a landed receipt."""

    result = row.result_ref
    if row.status != "landed" or result is None or result.get("status") != "published":
        raise ConflictDetected("source publication has no landed pull request receipt")
    _require_result_binding(row, request)
    binding = SourceCandidateCommitBinding(
        expected_base_sha=request.expected_base_sha,
        expected_tree_sha=_text(result, "treeSha"),
        expected_head_ref=request.expected_head_ref,
        manifest=request.manifest,
    )
    return PullRequestTarget(
        repository=request.repository,
        pull_number=_positive_integer(result, "pullNumber"),
        expected_base_ref=request.expected_base_ref,
        expected_head_sha=_text(result, "headSha"),
        candidate_binding=binding,
    )


def source_publication_receipt_ref(receipt: SourceCandidatePublicationReceipt) -> dict[str, object]:
    binding = receipt.commit_binding
    return {
        "status": receipt.status.value,
        "repositoryId": receipt.repository.repository_id,
        "baseRef": receipt.expected_base_ref,
        "headRef": receipt.expected_head_ref,
        "baseSha": receipt.expected_base_sha,
        "manifestFingerprint": receipt.manifest_fingerprint,
        "headSha": receipt.head_sha,
        "pullNumber": receipt.pull_number,
        "treeSha": binding.expected_tree_sha if binding is not None else None,
        "providerRequestId": receipt.provider_request_id,
        "evidence": dict(receipt.evidence),
    }


def publication_receipt_matches(
    receipt: SourceCandidatePublicationReceipt,
    row: ReleaseDeliveryRecord,
) -> bool:
    return (
        receipt.repository.repository_id == row.target_ref.get("repositoryId")
        and receipt.expected_base_ref == row.target_ref.get("baseRef")
        and receipt.expected_head_ref == row.target_ref.get("headRef")
        and receipt.expected_base_sha == row.target_ref.get("baseSha")
        and receipt.manifest_fingerprint == _candidate_value(row, "manifestFingerprint")
        and receipt.idempotency_key == row.idempotency_key
    )


def require_publication_replay_binding(
    row: ReleaseDeliveryRecord,
    ctx: RequestContext,
    request: SourceCandidatePublicationRequest,
) -> None:
    """Reject an exact-key replay if any actor, run, target, or artifact changed."""

    run_id, binding_hash = mutation_identity(ctx)
    identity_matches = (
        row.operation == "source_publish"
        and row.proposal_id == request.proposal_id
        and row.provider == request.repository.provider
        and row.environment == request.expected_base_ref
        and row.ai_run_id == run_id
        and row.binding_hash == binding_hash
        and row.created_by == ctx.actor_user_id
    )
    if not identity_matches:
        raise ConflictDetected("source publication replay does not match the governed proposal")
    require_publication_candidate_binding(row, request)


def require_publication_candidate_binding(
    row: ReleaseDeliveryRecord,
    request: SourceCandidatePublicationRequest,
) -> None:
    """Bind read and merge paths to the exact durable publication artifact."""

    if row.operation != "source_publish" or row.proposal_id != request.proposal_id:
        raise ConflictDetected("stored source publication does not match the governed proposal")
    if row.release_kind != request.release_kind:
        raise ConflictDetected("stored source publication release kind no longer matches its workflow")
    for key, expected in source_publication_target_ref(request).items():
        _require_value(row.target_ref, key, expected)
    _require_value(row.candidate_ref, "releaseKind", request.release_kind)
    _require_value(row.candidate_ref, "proposalId", request.proposal_id)
    _require_value(row.candidate_ref, "artifactPath", request.manifest.artifact_path)
    _require_value(row.candidate_ref, "manifestFingerprint", request.manifest.manifest_fingerprint)
    encoded = b64encode(request.manifest.canonical_bytes).decode("ascii")
    _require_value(row.candidate_ref, "manifestCanonicalBytesBase64", encoded)


def _stored_manifest(row: ReleaseDeliveryRecord) -> SourceCandidateManifest:
    candidate = row.candidate_ref
    if candidate is None:
        raise ConflictDetected("stored source publication manifest is missing")
    try:
        canonical_bytes = b64decode(_text(candidate, "manifestCanonicalBytesBase64"), validate=True)
        return SourceCandidateManifest(
            _text(candidate, "artifactPath"),
            canonical_bytes,
            _text(candidate, "manifestFingerprint"),
        )
    except (ValueError, binascii.Error) as exc:
        raise ConflictDetected("stored source publication manifest is invalid") from exc


def _require_result_binding(
    row: ReleaseDeliveryRecord,
    request: SourceCandidatePublicationRequest,
) -> None:
    result = row.result_ref
    if result is None:
        raise ConflictDetected("source publication receipt is missing")
    expected = {
        "repositoryId": request.repository.repository_id,
        "baseRef": request.expected_base_ref,
        "headRef": request.expected_head_ref,
        "baseSha": request.expected_base_sha,
        "manifestFingerprint": request.manifest.manifest_fingerprint,
    }
    for key, value in expected.items():
        _require_value(result, key, value)


def _repository(config: GovernedReleaseDeliveryConfig) -> SourceRepositoryRef:
    repository = config.source_repository
    if repository is None:
        raise ValidationFailed("source-control repository is not configured")
    return repository


def _branch_name(proposal: JsonObject) -> str:
    branch = proposal.get("sourceBranch")
    if not isinstance(branch, Mapping):
        raise ValidationFailed("release proposal has no server-resolved source branch")
    return _text(branch, "branchName")


def _candidate_value(row: ReleaseDeliveryRecord, key: str) -> object:
    return row.candidate_ref.get(key) if row.candidate_ref is not None else None


def _require_value(payload: JsonObject | None, key: str, expected: object) -> None:
    if payload is None or payload.get(key) != expected:
        raise ConflictDetected(
            "stored source publication no longer matches the server binding",
            details={"field": key, "expected": expected},
        )


def _text(payload: JsonObject, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed(f"{key} is required for source candidate publication")
    return value.strip()


def _positive_integer(payload: JsonObject, key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConflictDetected(f"stored source publication {key} is invalid")
    return value


__all__ = [
    "publication_receipt_matches",
    "require_publication_candidate_binding",
    "require_publication_replay_binding",
    "source_publication_candidate_ref",
    "source_publication_manifest",
    "source_publication_receipt_ref",
    "source_publication_request_for_proposal",
    "source_publication_request_from_record",
    "source_publication_target_from_record",
    "source_publication_target_ref",
]
