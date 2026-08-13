"""Deterministic, tenant-safe binding between one proposal and a Git commit.

The manifest deliberately contains fingerprints rather than Ontology YAML,
Pipeline graphs, titles, descriptions, or actor identities.  A public source
repository can therefore carry the release binding without becoming a second
copy of tenant data.  The GitHub adapter must later read these exact bytes from
the reviewed head commit before that pull request can become merge-ready.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.ports.source_control_release import SourceRepositoryRef
from foundry_lite.application.services.aip.consumer_osdk_compliance import consumer_osdk_compliance_binding
from foundry_lite.application.services.ontology_proposal_payloads import yaml_fingerprint
from foundry_lite.application.services.pipeline_graph_model import pipeline_graph_fingerprint
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

JsonObject = Mapping[str, object]

_MANIFEST_SCHEMA = "foundry-lite-governed-release/v1"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SHA256 = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class GovernedReleaseSourceManifest:
    """Canonical source artifact and the digest expected at the PR head."""

    artifact_path: str
    canonical_bytes: bytes
    manifest_fingerprint: str
    proposal_content_fingerprint: str

    def __post_init__(self) -> None:
        if not self.artifact_path.startswith(".foundry-lite/releases/"):
            raise ValueError("release manifest artifact path is invalid")
        if not self.canonical_bytes.endswith(b"\n"):
            raise ValueError("release manifest canonical bytes require one trailing newline")
        if _SHA256.fullmatch(self.manifest_fingerprint) is None:
            raise ValueError("release manifest fingerprint must be sha256")
        if _SHA256.fullmatch(self.proposal_content_fingerprint) is None:
            raise ValueError("proposal content fingerprint must be sha256")


def build_governed_release_source_manifest(
    ctx: RequestContext,
    release_kind: str,
    proposal: JsonObject,
    repository: SourceRepositoryRef,
    base_ref: str,
    head_ref: str,
    *,
    consumer_osdk_application_id: str | None = None,
    consumer_osdk_compliance: JsonObject | None = None,
    expected_source_commit: str | None = None,
) -> GovernedReleaseSourceManifest:
    """Build the exact, redacted bytes that a source candidate must contain."""

    del ctx  # Authorization is checked by the caller; public bytes contain no tenant identifier.
    proposal_id = _required_safe_id(proposal, "id")
    branch = _required_mapping(proposal, "sourceBranch")
    branch_id = _required_safe_id(branch, "branchId")
    branch_name = _required_text(branch, "branchName")
    _require_source_binding(head_ref, branch_name)
    content_fingerprint = _verified_content_fingerprint(release_kind, proposal)
    payload = _bound_manifest_payload(
        release_kind,
        proposal,
        repository,
        base_ref,
        head_ref,
        branch_id,
        content_fingerprint,
        consumer_osdk_application_id,
        consumer_osdk_compliance,
        expected_source_commit,
    )
    canonical_bytes = _canonical_json(payload) + b"\n"
    return GovernedReleaseSourceManifest(
        artifact_path=f".foundry-lite/releases/{release_kind}/{proposal_id}.json",
        canonical_bytes=canonical_bytes,
        manifest_fingerprint=_fingerprint(canonical_bytes),
        proposal_content_fingerprint=content_fingerprint,
    )


def verify_governed_release_source_manifest(
    expected: GovernedReleaseSourceManifest,
    observed_bytes: bytes,
) -> None:
    """Reject a PR head whose stored artifact is absent, changed, or noncanonical."""

    if observed_bytes != expected.canonical_bytes:
        raise ConflictDetected(
            "source pull request manifest does not match the governed proposal",
            details={
                "artifactPath": expected.artifact_path,
                "expectedFingerprint": expected.manifest_fingerprint,
                "observedFingerprint": _fingerprint(observed_bytes),
            },
        )


def _manifest_payload(
    release_kind: str,
    proposal: JsonObject,
    repository: SourceRepositoryRef,
    base_ref: str,
    head_ref: str,
    branch_id: str,
    content_fingerprint: str,
) -> dict[str, object]:
    proposal_id = _required_safe_id(proposal, "id")
    return {
        "schemaVersion": _MANIFEST_SCHEMA,
        "releaseKind": release_kind,
        "proposalId": proposal_id,
        "proposalContentFingerprint": content_fingerprint,
        "source": {
            "provider": repository.provider,
            "repositoryId": repository.repository_id,
            "baseRef": _required_value(base_ref, "baseRef"),
            "headRef": _required_value(head_ref, "headRef"),
            "internalBranchId": branch_id,
        },
        "reviewEvidence": _review_evidence_fingerprints(release_kind, proposal),
    }


def _bound_manifest_payload(
    release_kind: str,
    proposal: JsonObject,
    repository: SourceRepositoryRef,
    base_ref: str,
    head_ref: str,
    branch_id: str,
    content_fingerprint: str,
    application_id: str | None,
    compliance: JsonObject | None,
    expected_source_commit: str | None,
) -> dict[str, object]:
    payload = _manifest_payload(release_kind, proposal, repository, base_ref, head_ref, branch_id, content_fingerprint)
    _bind_consumer_osdk(payload, application_id, compliance, expected_source_commit)
    return payload


def _bind_consumer_osdk(
    payload: dict[str, object],
    application_id: str | None,
    compliance: JsonObject | None,
    expected_source_commit: str | None,
) -> None:
    if application_id is None and compliance is None:
        return
    if not isinstance(application_id, str) or not application_id.strip():
        raise ValidationFailed("consumerOsdkApplicationId is required with a compliance receipt")
    if compliance is None:
        raise ValidationFailed("consumerOsdkCompliance is required for a strict consumer application release")
    if not isinstance(expected_source_commit, str) or not expected_source_commit:
        raise ValidationFailed("expected source commit is required for consumer OSDK compliance")
    payload["consumerOsdkCompliance"] = consumer_osdk_compliance_binding(
        compliance,
        application_id.strip(),
        expected_source_commit,
    )


def _verified_content_fingerprint(release_kind: str, proposal: JsonObject) -> str:
    if release_kind == "ontology":
        actual = yaml_fingerprint(_required_raw_text(proposal, "yamlText"))
        expected = _required_fingerprint(proposal, "fingerprint")
    elif release_kind == "pipeline":
        graph = _required_mapping(proposal, "graph")
        actual = pipeline_graph_fingerprint(graph)
        expected = _required_fingerprint(proposal, "graphFingerprint")
    else:
        raise ValidationFailed("releaseKind must be ontology or pipeline")
    if _normalized_fingerprint(expected) != _normalized_fingerprint(actual):
        raise ConflictDetected(
            "release proposal content no longer matches its stored fingerprint",
            details={"releaseKind": release_kind, "proposalId": proposal.get("id")},
        )
    return f"sha256:{_normalized_fingerprint(actual)}"


def _review_evidence_fingerprints(release_kind: str, proposal: JsonObject) -> dict[str, object]:
    if release_kind == "ontology":
        ontology_evidence = {
            "validation": _required_mapping(proposal, "validation"),
            "submittedMigrationPlan": _required_mapping(proposal, "submittedMigrationPlan"),
        }
        return {"ontologyValidationFingerprint": _json_fingerprint(ontology_evidence)}
    pipeline_evidence: dict[str, object] = {
        "changeDiff": _required_mapping(proposal, "changeDiff"),
        "diffCompleteness": _required_text(proposal, "diffCompleteness"),
        "testReceipt": _required_mapping(proposal, "testReceipt"),
    }
    return {"pipelineValidationFingerprint": _json_fingerprint(pipeline_evidence)}


def _require_source_binding(head_ref: str, branch_name: str) -> None:
    clean_head = _required_value(head_ref, "headRef")
    clean_branch = _required_value(branch_name, "branchName")
    if clean_head != clean_branch and not clean_head.endswith(f"/{clean_branch}"):
        raise ConflictDetected(
            "source head ref is not derived from the internal release branch",
            details={"headRef": clean_head},
        )


def _json_fingerprint(value: object) -> str:
    return _fingerprint(_canonical_json(value))


def _fingerprint(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _canonical_json(value: object) -> bytes:
    try:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise ValidationFailed("release proposal evidence is not JSON serializable") from exc
    return text.encode("utf-8")


def _required_mapping(payload: JsonObject, key: str) -> Mapping[str, object]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValidationFailed(f"{key} is required for source candidate publication")
    return value


def _required_safe_id(payload: JsonObject, key: str) -> str:
    value = _required_text(payload, key)
    if _SAFE_ID.fullmatch(value) is None:
        raise ValidationFailed(f"{key} cannot be used in a release manifest path")
    return value


def _required_fingerprint(payload: JsonObject, key: str) -> str:
    value = _required_text(payload, key)
    if _SHA256.fullmatch(value) is None:
        raise ValidationFailed(f"{key} must be a full sha256 fingerprint")
    return value


def _required_text(payload: JsonObject, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed(f"{key} is required for source candidate publication")
    return value.strip()


def _required_raw_text(payload: JsonObject, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed(f"{key} is required for source candidate publication")
    return value


def _required_value(value: str, label: str) -> str:
    if not value.strip():
        raise ValidationFailed(f"{label} is required for source candidate publication")
    return value.strip()


def _normalized_fingerprint(value: str) -> str:
    return value.removeprefix("sha256:")


__all__ = [
    "GovernedReleaseSourceManifest",
    "build_governed_release_source_manifest",
    "verify_governed_release_source_manifest",
]
