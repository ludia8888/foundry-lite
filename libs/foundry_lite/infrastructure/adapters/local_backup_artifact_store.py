"""Infrastructure adapter implementation for local backup artifact store."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast

from foundry_lite.application.ports.backup_artifact_store import (
    BackupArtifactConflictError,
    BackupArtifactIntegrityError,
    BackupArtifactNotFoundError,
)
from foundry_lite.application.primitives import _json_ready
from foundry_lite.application.runtime_repository_backup_restore import (
    BackupRestoreArtifact,
    BackupRestoreArtifactReceipt,
    BackupRestorePreflightReport,
)

_MANIFEST_FORMAT = "foundry-lite.backup-artifact.v1"


@dataclass(frozen=True)
class LocalBackupArtifactStore:
    """Local JSON backup artifact store for recovery drills and CI evidence."""

    root: Path
    profile_name: str = "local-backup-artifact-store"

    def write_backup_artifact(self, report: BackupRestorePreflightReport) -> BackupRestoreArtifactReceipt:
        artifact_path = self._artifact_path(report)
        payload = _artifact_payload(report)
        artifact_hash = _hash_payload(payload)
        if artifact_path.exists():
            return self._existing_receipt(artifact_path, report, artifact_hash)
        receipt = _receipt(report, artifact_path, artifact_hash, len(_canonical_bytes(payload)), is_replay=False)
        artifact = {"receipt": receipt, **payload}
        self._write_artifact(artifact_path, artifact)
        return receipt

    def read_backup_artifact(
        self,
        artifact_ref: str,
        *,
        expected_hash: str | None = None,
    ) -> BackupRestoreArtifact:
        artifact_path = Path(artifact_ref)
        if not artifact_path.exists():
            raise BackupArtifactNotFoundError(artifact_ref)
        raw = _load_artifact(artifact_path)
        artifact = _verified_artifact(raw, artifact_path, expected_hash)
        return cast(BackupRestoreArtifact, artifact)

    def _existing_receipt(
        self,
        artifact_path: Path,
        report: BackupRestorePreflightReport,
        requested_hash: str,
    ) -> BackupRestoreArtifactReceipt:
        raw = json.loads(artifact_path.read_text(encoding="utf-8"))
        existing_hash = str(raw.get("receipt", {}).get("artifactHash"))
        if existing_hash != requested_hash:
            raise BackupArtifactConflictError(report["backupId"], existing_hash, requested_hash)
        receipt = dict(raw["receipt"])
        receipt["status"] = "existing"
        receipt["isIdempotentReplay"] = True
        return cast(BackupRestoreArtifactReceipt, receipt)

    def _artifact_path(self, report: BackupRestorePreflightReport) -> Path:
        token = sha256(f"{report['tenantId']}:{report['backupId']}".encode()).hexdigest()
        return self.root / report["tenantId"] / f"{token}.json"

    def _write_artifact(self, path: Path, artifact: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(_json_ready(artifact), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp_path.replace(path)


def _artifact_payload(report: BackupRestorePreflightReport) -> dict[str, object]:
    return {
        "manifestFormat": _MANIFEST_FORMAT,
        "sourceOfTruth": {
            "datasetVersionTruth": "metadata_db_committed_dataset_version",
            "storageTruth": "committed_manifest_and_data_file_hashes",
            "searchProjectionTruth": "rebuildable_projection_not_backup_truth",
        },
        "preflightReport": report,
    }


def _receipt(
    report: BackupRestorePreflightReport,
    artifact_path: Path,
    artifact_hash: str,
    payload_byte_size: int,
    *,
    is_replay: bool,
) -> BackupRestoreArtifactReceipt:
    return {
        "generatedAt": report["generatedAt"],
        "tenantId": report["tenantId"],
        "backupId": report["backupId"],
        "backupArtifactId": f"backup-artifact-{artifact_hash[:24]}",
        "status": "existing" if is_replay else "created",
        "artifactRef": str(artifact_path),
        "artifactHash": artifact_hash,
        "payloadByteSize": payload_byte_size,
        "manifestFormat": _MANIFEST_FORMAT,
        "preflightStatus": report["status"],
        "datasetVersionCount": len(report["datasetVersions"]),
        "issueCount": len(report["issues"]),
        "isIdempotentReplay": is_replay,
        "restoreModeStartPath": "/api/operations/backup-restore/restore-mode/start",
        "summary": {
            "datasetVersionCount": len(report["datasetVersions"]),
            "issueCount": len(report["issues"]),
            "canStartRestoreMode": report["status"] == "ready",
        },
    }


def _hash_payload(payload: dict[str, object]) -> str:
    return "sha256:" + sha256(_canonical_bytes(payload)).hexdigest()


def _canonical_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(_json_ready(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load_artifact(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupArtifactIntegrityError(str(path), str(exc)) from exc
    if not isinstance(raw, dict):
        raise BackupArtifactIntegrityError(str(path), "artifact root must be an object")
    return cast(dict[str, object], raw)


def _verified_artifact(
    raw: dict[str, object],
    path: Path,
    expected_hash: str | None,
) -> dict[str, object]:
    receipt = _artifact_mapping(raw, "receipt", path)
    payload = {key: value for key, value in raw.items() if key != "receipt"}
    actual_hash = _hash_payload(payload)
    _verify_receipt(receipt, payload, path, actual_hash, expected_hash)
    return {"receipt": receipt, **payload}


def _artifact_mapping(raw: dict[str, object], key: str, path: Path) -> dict[str, object]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise BackupArtifactIntegrityError(str(path), f"{key} must be an object")
    return cast(dict[str, object], value)


def _verify_receipt(
    receipt: dict[str, object],
    payload: dict[str, object],
    path: Path,
    actual_hash: str,
    expected_hash: str | None,
) -> None:
    report = _artifact_mapping(payload, "preflightReport", path)
    if payload.get("manifestFormat") != _MANIFEST_FORMAT:
        raise BackupArtifactIntegrityError(str(path), "unsupported manifest format")
    if receipt.get("artifactHash") != actual_hash or expected_hash not in (None, actual_hash):
        raise BackupArtifactIntegrityError(str(path), "artifact hash mismatch")
    if receipt.get("artifactRef") != str(path):
        raise BackupArtifactIntegrityError(str(path), "artifact reference mismatch")
    if receipt.get("tenantId") != report.get("tenantId") or receipt.get("backupId") != report.get("backupId"):
        raise BackupArtifactIntegrityError(str(path), "receipt and preflight report disagree")
