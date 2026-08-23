"""Canonical backup artifact encoding shared by local and S3 stores."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import cast

from foundry_lite.application.ports.backup_artifact_store import (
    BackupArtifactConflictError,
    BackupArtifactIntegrityError,
)
from foundry_lite.application.primitives import _json_ready
from foundry_lite.application.runtime_repository_backup_restore import (
    BackupRestoreArtifact,
    BackupRestoreArtifactReceipt,
    BackupRestorePreflightReport,
)

MANIFEST_FORMAT = "foundry-lite.backup-artifact.v1"


def backup_artifact_payload(report: BackupRestorePreflightReport) -> dict[str, object]:
    """Build the source-of-truth payload whose hash is the restore boundary."""

    return {
        "manifestFormat": MANIFEST_FORMAT,
        "sourceOfTruth": {
            "datasetVersionTruth": "metadata_db_committed_dataset_version",
            "storageTruth": "committed_manifest_and_data_file_hashes",
            "searchProjectionTruth": "rebuildable_projection_not_backup_truth",
        },
        "preflightReport": report,
    }


def backup_artifact_hash(payload: dict[str, object]) -> str:
    """Hash only the immutable payload, never presentation whitespace or receipt state."""

    return "sha256:" + sha256(canonical_backup_payload_bytes(payload)).hexdigest()


def canonical_backup_payload_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(_json_ready(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_backup_artifact(
    report: BackupRestorePreflightReport,
    artifact_ref: str,
) -> tuple[BackupRestoreArtifactReceipt, bytes]:
    """Create a receipt and serialized immutable artifact for one storage location."""

    payload = backup_artifact_payload(report)
    artifact_hash = backup_artifact_hash(payload)
    receipt = _receipt(report, artifact_ref, artifact_hash, len(canonical_backup_payload_bytes(payload)))
    document = {"receipt": receipt, **payload}
    # Backup artifacts can contain tens of thousands of immutable dataset
    # versions. Presentation whitespace previously added roughly 40% to the
    # object and made valid soak backups cross the S3 adapter's size boundary.
    # The payload hash was already defined over canonical compact JSON, so use
    # the same bounded representation for the stored envelope as well.
    encoded = canonical_backup_payload_bytes(document)
    return receipt, encoded


def replay_backup_artifact_receipt(
    encoded: bytes,
    artifact_ref: str,
    *,
    requested_hash: str,
    backup_id: str,
) -> BackupRestoreArtifactReceipt:
    """Return an idempotent replay receipt or reject an immutable-key collision."""

    artifact = verify_backup_artifact(encoded, artifact_ref)
    existing_hash = str(artifact["receipt"]["artifactHash"])
    if existing_hash != requested_hash:
        raise BackupArtifactConflictError(backup_id, existing_hash, requested_hash)
    receipt = dict(artifact["receipt"])
    receipt["status"] = "existing"
    receipt["isIdempotentReplay"] = True
    return cast(BackupRestoreArtifactReceipt, receipt)


def verify_backup_artifact(
    encoded: bytes,
    artifact_ref: str,
    expected_hash: str | None = None,
) -> BackupRestoreArtifact:
    """Parse and verify receipt, manifest, hash, and location binding."""

    raw = _load_document(encoded, artifact_ref)
    receipt = _mapping(raw, "receipt", artifact_ref)
    payload = {key: value for key, value in raw.items() if key != "receipt"}
    actual_hash = backup_artifact_hash(payload)
    _verify_receipt(receipt, payload, artifact_ref, actual_hash, expected_hash)
    return cast(BackupRestoreArtifact, {"receipt": receipt, **payload})


def _receipt(
    report: BackupRestorePreflightReport,
    artifact_ref: str,
    artifact_hash: str,
    payload_byte_size: int,
) -> BackupRestoreArtifactReceipt:
    return {
        "generatedAt": report["generatedAt"],
        "tenantId": report["tenantId"],
        "backupId": report["backupId"],
        "backupArtifactId": f"backup-artifact-{artifact_hash[:24]}",
        "status": "created",
        "artifactRef": artifact_ref,
        "artifactHash": artifact_hash,
        "payloadByteSize": payload_byte_size,
        "manifestFormat": MANIFEST_FORMAT,
        "preflightStatus": report["status"],
        "datasetVersionCount": len(report["datasetVersions"]),
        "issueCount": len(report["issues"]),
        "isIdempotentReplay": False,
        "restoreModeStartPath": "/api/operations/backup-restore/restore-mode/start",
        "summary": {
            "datasetVersionCount": len(report["datasetVersions"]),
            "issueCount": len(report["issues"]),
            "canStartRestoreMode": report["status"] == "ready",
        },
    }


def _load_document(encoded: bytes, artifact_ref: str) -> dict[str, object]:
    try:
        raw = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupArtifactIntegrityError(artifact_ref, str(exc)) from exc
    if not isinstance(raw, dict):
        raise BackupArtifactIntegrityError(artifact_ref, "artifact root must be an object")
    return cast(dict[str, object], raw)


def _mapping(raw: dict[str, object], key: str, artifact_ref: str) -> dict[str, object]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise BackupArtifactIntegrityError(artifact_ref, f"{key} must be an object")
    return cast(dict[str, object], value)


def _verify_receipt(
    receipt: dict[str, object],
    payload: dict[str, object],
    artifact_ref: str,
    actual_hash: str,
    expected_hash: str | None,
) -> None:
    report = _mapping(payload, "preflightReport", artifact_ref)
    if payload.get("manifestFormat") != MANIFEST_FORMAT:
        raise BackupArtifactIntegrityError(artifact_ref, "unsupported manifest format")
    if receipt.get("artifactHash") != actual_hash or expected_hash not in (None, actual_hash):
        raise BackupArtifactIntegrityError(artifact_ref, "artifact hash mismatch")
    if receipt.get("artifactRef") != artifact_ref:
        raise BackupArtifactIntegrityError(artifact_ref, "artifact reference mismatch")
    if receipt.get("tenantId") != report.get("tenantId") or receipt.get("backupId") != report.get("backupId"):
        raise BackupArtifactIntegrityError(artifact_ref, "receipt and preflight report disagree")
