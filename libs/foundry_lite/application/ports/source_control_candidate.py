"""Provider-neutral contract for publishing one exact governed source candidate.

The caller supplies immutable manifest bytes and the exact base commit.  A
provider adapter may create Git objects and a pull request, but a published
receipt is valid only after the adapter reads the candidate back and proves the
head is a one-commit, manifest-only child of that base.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import cast

_FULL_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_GIT_REF = re.compile(r"^(?!/)(?!.*(?:\.\.|//|@\{|\\))[A-Za-z0-9._/-]+(?<![/.])$")
_SAFE_PROPOSAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_MANIFEST_PATH = re.compile(
    r"^\.foundry-lite/releases/(?P<kind>ontology|pipeline)/(?P<proposal>[A-Za-z0-9][A-Za-z0-9_-]{0,127})\.json$"
)
_MAX_MANIFEST_BYTES = 64 * 1024


class SourceCandidatePublicationStatus(StrEnum):
    """Authoritative read-back state for one exact candidate identity."""

    PUBLISHED = "published"
    PARTIAL = "partial"
    ABSENT = "absent"
    AMBIGUOUS = "ambiguous"


_PUBLICATION_RECEIPT_SHAPES: Mapping[
    SourceCandidatePublicationStatus,
    tuple[frozenset[str], str],
] = {
    SourceCandidatePublicationStatus.PUBLISHED: (
        frozenset({"head", "pull", "binding"}),
        "published source candidate receipt requires head, pull request, and binding",
    ),
    SourceCandidatePublicationStatus.PARTIAL: (
        frozenset({"head", "binding"}),
        "partial source candidate receipt requires an exact branch without a pull request",
    ),
    SourceCandidatePublicationStatus.ABSENT: (
        frozenset(),
        "absent source candidate receipt cannot carry remote identities",
    ),
}


@dataclass(frozen=True)
class SourceRepositoryRef:
    """Immutable source-provider repository identity."""

    provider: str
    repository_id: int
    owner: str
    name: str

    def __post_init__(self) -> None:
        if not self.provider.strip() or not _is_positive_int(self.repository_id):
            raise ValueError("source repository provider and positive repository_id are required")
        if not self.owner.strip() or not self.name.strip():
            raise ValueError("source repository owner and name are required")


@dataclass(frozen=True)
class SourceCandidateManifest:
    """Exact redacted bytes that must exist at the reviewed Git head."""

    artifact_path: str
    canonical_bytes: bytes = field(repr=False)
    manifest_fingerprint: str

    def __post_init__(self) -> None:
        canonical_bytes = _require_manifest_bytes(self.canonical_bytes)
        if _MANIFEST_PATH.fullmatch(self.artifact_path) is None:
            raise ValueError("source candidate manifest path is invalid")
        if not canonical_bytes or len(canonical_bytes) > _MAX_MANIFEST_BYTES:
            raise ValueError("source candidate manifest bytes must be nonempty and bounded")
        if self.manifest_fingerprint != _fingerprint(canonical_bytes):
            raise ValueError("source candidate manifest fingerprint does not match its bytes")


@dataclass(frozen=True)
class SourceCandidateCommitBinding:
    """Expected Git identity and exact manifest for later merge-time read-back."""

    expected_base_sha: str
    expected_tree_sha: str
    expected_head_ref: str
    manifest: SourceCandidateManifest

    def __post_init__(self) -> None:
        if not _is_full_sha(self.expected_base_sha) or not _is_full_sha(self.expected_tree_sha):
            raise ValueError("candidate commit binding requires full base and tree SHAs")
        if not _is_safe_git_ref(self.expected_head_ref):
            raise ValueError("candidate commit binding requires a safe head ref")


@dataclass(frozen=True)
class PullRequestTarget:
    """A reviewed PR target sealed to its base, head, and optional candidate bytes."""

    repository: SourceRepositoryRef
    pull_number: int
    expected_base_ref: str
    expected_head_sha: str
    candidate_binding: SourceCandidateCommitBinding | None = None

    def __post_init__(self) -> None:
        if not _is_positive_int(self.pull_number):
            raise ValueError("pull_number must be positive")
        if not self.expected_base_ref.strip():
            raise ValueError("expected_base_ref is required")
        if not _is_full_sha(self.expected_head_sha):
            raise ValueError("expected_head_sha must be a full lowercase 40-character Git SHA")


@dataclass(frozen=True)
class SourceRefSnapshot:
    """Fresh commit and tree identity for one provider ref."""

    repository: SourceRepositoryRef
    ref: str
    commit_sha: str
    tree_sha: str

    def __post_init__(self) -> None:
        if not _is_safe_git_ref(self.ref):
            raise ValueError("source ref snapshot requires a safe ref")
        if not _is_full_sha(self.commit_sha) or not _is_full_sha(self.tree_sha):
            raise ValueError("source ref snapshot requires full commit and tree SHAs")


@dataclass(frozen=True)
class SourceCandidatePublicationRequest:
    """Idempotent request for a manifest-only branch and exact pull request."""

    repository: SourceRepositoryRef
    release_kind: str
    proposal_id: str
    expected_base_ref: str
    expected_head_ref: str
    expected_base_sha: str
    manifest: SourceCandidateManifest
    idempotency_key: str

    def __post_init__(self) -> None:
        _validate_publication_coordinates(self)
        _validate_publication_manifest(self)


def _validate_publication_coordinates(request: SourceCandidatePublicationRequest) -> None:
    if request.release_kind not in {"ontology", "pipeline"}:
        raise ValueError("source candidate release_kind must be ontology or pipeline")
    if _SAFE_PROPOSAL_ID.fullmatch(request.proposal_id) is None:
        raise ValueError("source candidate proposal_id is invalid")
    if not _is_safe_git_ref(request.expected_base_ref) or not _is_safe_git_ref(request.expected_head_ref):
        raise ValueError("source candidate requires safe base and head refs")
    if request.expected_base_ref == request.expected_head_ref:
        raise ValueError("source candidate base and head refs must differ")
    if not _is_full_sha(request.expected_base_sha) or not request.idempotency_key.strip():
        raise ValueError("source candidate requires a full base SHA and idempotency key")


def _validate_publication_manifest(request: SourceCandidatePublicationRequest) -> None:
    match = _MANIFEST_PATH.fullmatch(request.manifest.artifact_path)
    if match is None:
        raise ValueError("source candidate manifest path is invalid")
    if match.group("kind") != request.release_kind or match.group("proposal") != request.proposal_id:
        raise ValueError("source candidate manifest path does not match the proposal identity")


def _empty_candidate_evidence() -> Mapping[str, object]:
    return {}


@dataclass(frozen=True)
class SourceCandidatePublicationReceipt:
    """Read-back result that distinguishes not-published from provider failure."""

    status: SourceCandidatePublicationStatus
    repository: SourceRepositoryRef
    expected_base_ref: str
    expected_head_ref: str
    expected_base_sha: str
    manifest_artifact_path: str
    manifest_fingerprint: str
    idempotency_key: str
    head_sha: str | None = None
    pull_number: int | None = None
    commit_binding: SourceCandidateCommitBinding | None = None
    provider_request_id: str | None = None
    evidence: Mapping[str, object] = field(default_factory=_empty_candidate_evidence)

    def __post_init__(self) -> None:
        _validate_publication_receipt_identity(self)
        _validate_publication_receipt_state(self)
        object.__setattr__(self, "evidence", freeze_source_control_evidence(self.evidence))

    def to_pull_request_target(self) -> PullRequestTarget:
        """Return the sealed merge target only after exact publication proof."""

        if self.status is not SourceCandidatePublicationStatus.PUBLISHED:
            raise ValueError("only a published source candidate has a pull request target")
        if self.head_sha is None or self.pull_number is None or self.commit_binding is None:
            raise ValueError("published source candidate receipt is incomplete")
        return PullRequestTarget(
            self.repository,
            self.pull_number,
            self.expected_base_ref,
            self.head_sha,
            candidate_binding=self.commit_binding,
        )


def _validate_publication_receipt_identity(receipt: SourceCandidatePublicationReceipt) -> None:
    _require_publication_status(receipt.status)
    if not _is_safe_git_ref(receipt.expected_base_ref) or not _is_safe_git_ref(receipt.expected_head_ref):
        raise ValueError("source candidate receipt refs are invalid")
    if _MANIFEST_PATH.fullmatch(receipt.manifest_artifact_path) is None:
        raise ValueError("source candidate receipt manifest path is invalid")
    if not _is_full_sha(receipt.expected_base_sha) or not _is_sha256(receipt.manifest_fingerprint):
        raise ValueError("source candidate receipt fingerprints are invalid")
    if not receipt.idempotency_key.strip():
        raise ValueError("source candidate receipt idempotency key is required")


def _validate_publication_receipt_state(receipt: SourceCandidatePublicationReceipt) -> None:
    _validate_optional_receipt_remote_ids(receipt)
    shape = _PUBLICATION_RECEIPT_SHAPES.get(receipt.status)
    if shape is not None and _receipt_remote_fields(receipt) != shape[0]:
        raise ValueError(shape[1])
    _validate_receipt_binding(receipt)


def _validate_optional_receipt_remote_ids(receipt: SourceCandidatePublicationReceipt) -> None:
    if receipt.head_sha is not None and not _is_full_sha(receipt.head_sha):
        raise ValueError("source candidate receipt head SHA is invalid")
    if receipt.pull_number is not None and not _is_positive_int(receipt.pull_number):
        raise ValueError("source candidate receipt pull number is invalid")


def _receipt_remote_fields(receipt: SourceCandidatePublicationReceipt) -> frozenset[str]:
    values = {
        "head": receipt.head_sha,
        "pull": receipt.pull_number,
        "binding": receipt.commit_binding,
    }
    return frozenset(name for name, value in values.items() if value is not None)


def _validate_receipt_binding(receipt: SourceCandidatePublicationReceipt) -> None:
    binding = receipt.commit_binding
    if binding is None:
        return
    if binding.expected_base_sha != receipt.expected_base_sha:
        raise ValueError("source candidate receipt base binding changed")
    if binding.expected_head_ref != receipt.expected_head_ref:
        raise ValueError("source candidate receipt head binding changed")
    if binding.manifest.artifact_path != receipt.manifest_artifact_path:
        raise ValueError("source candidate receipt manifest path binding changed")
    if binding.manifest.manifest_fingerprint != receipt.manifest_fingerprint:
        raise ValueError("source candidate receipt manifest binding changed")


def _fingerprint(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _require_manifest_bytes(value: object) -> bytes:
    if not isinstance(value, bytes):
        raise ValueError("source candidate manifest must use immutable bytes")
    return value


def _require_publication_status(value: object) -> None:
    if not isinstance(value, SourceCandidatePublicationStatus):
        raise ValueError("source candidate receipt status is invalid")


def _is_full_sha(value: object) -> bool:
    return isinstance(value, str) and _FULL_GIT_SHA.fullmatch(value) is not None


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_FINGERPRINT.fullmatch(value) is not None


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_safe_git_ref(value: object) -> bool:
    return isinstance(value, str) and _SAFE_GIT_REF.fullmatch(value) is not None and not value.endswith(".lock")


def freeze_source_control_evidence(value: Mapping[str, object]) -> Mapping[str, object]:
    """Defensively freeze JSON-compatible adapter evidence."""

    mapping = cast(Mapping[object, object], value)
    if not all(isinstance(key, str) for key in mapping):
        raise ValueError("source candidate evidence keys must be strings")
    return MappingProxyType({cast(str, key): _freeze_evidence(item) for key, item in mapping.items()})


def _freeze_evidence(value: object) -> object:
    if isinstance(value, Mapping):
        return freeze_source_control_evidence(cast(Mapping[str, object], value))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_evidence(item) for item in cast(list[object] | tuple[object, ...], value))
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("source candidate evidence must contain immutable JSON-compatible values")


__all__ = [
    "PullRequestTarget",
    "SourceCandidateCommitBinding",
    "SourceCandidateManifest",
    "SourceCandidatePublicationReceipt",
    "SourceCandidatePublicationRequest",
    "SourceCandidatePublicationStatus",
    "SourceRefSnapshot",
    "SourceRepositoryRef",
    "freeze_source_control_evidence",
]
