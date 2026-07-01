from __future__ import annotations

import json
from pathlib import Path

import pytest
from foundry_lite.application.ports.backup_artifact_store import (
    BackupArtifactConflictError,
    BackupArtifactIntegrityError,
)
from foundry_lite.application.runtime_repository_backup_restore import BackupRestorePreflightReport
from foundry_lite.infrastructure.adapters.local_backup_artifact_store import LocalBackupArtifactStore


def test_local_backup_artifact_store_writes_immutable_receipt(tmp_path: Path) -> None:
    store = LocalBackupArtifactStore(tmp_path)
    report = _report("backup-1", version_id="version-1")

    receipt = store.write_backup_artifact(report)
    artifact = json.loads(Path(receipt["artifactRef"]).read_text(encoding="utf-8"))

    assert receipt["status"] == "created"
    assert receipt["preflightStatus"] == "ready"
    assert receipt["datasetVersionCount"] == 1
    assert artifact["manifestFormat"] == "foundry-lite.backup-artifact.v1"
    assert artifact["preflightReport"]["backupId"] == "backup-1"
    assert artifact["receipt"]["artifactHash"] == receipt["artifactHash"]


def test_local_backup_artifact_store_replays_same_artifact_without_overwrite(tmp_path: Path) -> None:
    store = LocalBackupArtifactStore(tmp_path)
    report = _report("backup-replay", version_id="version-1")

    first = store.write_backup_artifact(report)
    second = store.write_backup_artifact(report)

    assert second["status"] == "existing"
    assert second["isIdempotentReplay"] is True
    assert second["artifactHash"] == first["artifactHash"]
    assert second["artifactRef"] == first["artifactRef"]


def test_local_backup_artifact_store_reads_and_verifies_artifact(tmp_path: Path) -> None:
    store = LocalBackupArtifactStore(tmp_path)
    report = _report("backup-read", version_id="version-1")
    receipt = store.write_backup_artifact(report)

    artifact = store.read_backup_artifact(receipt["artifactRef"], expected_hash=receipt["artifactHash"])

    assert artifact["receipt"]["artifactHash"] == receipt["artifactHash"]
    assert artifact["preflightReport"]["backupId"] == "backup-read"
    assert artifact["manifestFormat"] == "foundry-lite.backup-artifact.v1"


def test_local_backup_artifact_store_rejects_wrong_expected_hash(tmp_path: Path) -> None:
    store = LocalBackupArtifactStore(tmp_path)
    receipt = store.write_backup_artifact(_report("backup-wrong-hash", version_id="version-1"))

    with pytest.raises(BackupArtifactIntegrityError, match="artifact hash mismatch"):
        store.read_backup_artifact(receipt["artifactRef"], expected_hash="sha256:not-the-artifact")


def test_local_backup_artifact_store_rejects_tampered_payload(tmp_path: Path) -> None:
    store = LocalBackupArtifactStore(tmp_path)
    receipt = store.write_backup_artifact(_report("backup-tamper", version_id="version-1"))
    artifact_path = Path(receipt["artifactRef"])
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact["preflightReport"]["datasetVersions"][0]["rowCount"] = 999
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(BackupArtifactIntegrityError, match="artifact hash mismatch"):
        store.read_backup_artifact(receipt["artifactRef"], expected_hash=receipt["artifactHash"])


def test_local_backup_artifact_store_rejects_same_backup_id_with_different_payload(tmp_path: Path) -> None:
    store = LocalBackupArtifactStore(tmp_path)
    first = _report("backup-conflict", version_id="version-1")
    changed = _report("backup-conflict", version_id="version-2")
    store.write_backup_artifact(first)

    with pytest.raises(BackupArtifactConflictError):
        store.write_backup_artifact(changed)


def _report(backup_id: str, *, version_id: str) -> BackupRestorePreflightReport:
    return {
        "generatedAt": "2026-07-01T00:00:00+09:00",
        "tenantId": "tenant-demo",
        "backupId": backup_id,
        "status": "ready",
        "datasetVersions": [
            {
                "datasetRef": "raw.orders",
                "datasetId": "ds-orders",
                "branch": "main",
                "versionId": version_id,
                "versionNumber": 1,
                "manifestUri": "/tmp/manifest.json",
                "rowCount": 1,
                "byteSize": 10,
                "storageProfile": "local",
                "manifestFileCount": 1,
                "manifestRowCount": 1,
                "manifestByteSize": 10,
                "manifestContentHashes": ["sha256:abc"],
                "status": "valid",
                "issue": None,
            }
        ],
        "issues": [],
        "activeIndexPointers": [],
        "highWatermarks": {},
        "temporalStrategy": {"workflowProfile": "local-workflow"},
        "searchRebuild": {"rebuildRequiredAfterRestore": True},
        "restoreTrafficGate": {"writeTrafficMustBePausedBeforeRestore": True},
        "summary": {"issueCount": 0},
    }
