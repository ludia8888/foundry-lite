from __future__ import annotations

import json
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from foundry_lite.application.ports.backup_artifact_store import (
    BackupArtifactConflictError,
    BackupArtifactIntegrityError,
    BackupArtifactNotFoundError,
)
from foundry_lite.application.runtime_repository_backup_restore import BackupRestorePreflightReport
from foundry_lite.infrastructure.adapters.backup_artifact_codec import build_backup_artifact
from foundry_lite.infrastructure.adapters.local_backup_artifact_store import LocalBackupArtifactStore
from foundry_lite.infrastructure.adapters.s3_backup_artifact_store import (
    S3BackupArtifactStore,
    S3BackupArtifactStoreConfig,
)


class _S3Error(Exception):
    def __init__(self, code: str, status: int) -> None:
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}
        super().__init__(code)


class _MemoryS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, **kwargs: Any) -> None:
        target = (str(kwargs["Bucket"]), str(kwargs["Key"]))
        if kwargs.get("IfNoneMatch") == "*" and target in self.objects:
            raise _S3Error("PreconditionFailed", 412)
        self.objects[target] = bytes(kwargs["Body"])

    def get_object(self, **kwargs: Any) -> dict[str, object]:
        target = (str(kwargs["Bucket"]), str(kwargs["Key"]))
        if target not in self.objects:
            raise _S3Error("NoSuchKey", 404)
        body = self.objects[target]
        return {"Body": BytesIO(body), "ContentLength": len(body)}


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


def test_s3_backup_artifact_store_is_immutable_replayable_and_hash_verified() -> None:
    client = _MemoryS3()
    store = S3BackupArtifactStore(
        S3BackupArtifactStoreConfig(bucket="foundry-artifacts", prefix="qa/backups"),
        client=client,
    )
    report = _report("backup-s3", version_id="version-1")

    first = store.write_backup_artifact(report)
    replay = store.write_backup_artifact(report)
    artifact = store.read_backup_artifact(first["artifactRef"], expected_hash=first["artifactHash"])

    assert first["artifactRef"].startswith("s3-backup-artifact://foundry-artifacts/qa/backups/")
    assert replay["isIdempotentReplay"] is True
    assert artifact["preflightReport"]["backupId"] == "backup-s3"
    with pytest.raises(BackupArtifactConflictError):
        store.write_backup_artifact(_report("backup-s3", version_id="version-2"))


def test_backup_artifact_storage_is_compact_and_has_soak_history_headroom() -> None:
    report = _report("backup-large-soak", version_id="version-1")
    template = report["datasetVersions"][0]
    report["datasetVersions"] = [
        {
            **deepcopy(template),
            "versionId": f"dsv_{index:032x}",
            "versionNumber": index,
            "manifestUri": f"s3://foundry/datasets/ds-orders/main/{index}/manifest.json",
            "manifestContentHashes": ["sha256:" + "a" * 64],
        }
        for index in range(31_000)
    ]
    artifact_ref = "s3-backup-artifact://foundry-artifacts/qa/backups/tenant-demo/large.json"
    _receipt, encoded = build_backup_artifact(report, artifact_ref)
    canonical = json.dumps(json.loads(encoded), sort_keys=True, separators=(",", ":")).encode()

    assert encoded == canonical
    assert len(encoded) < 16 * 1024 * 1024
    client = _MemoryS3()
    store = S3BackupArtifactStore(
        S3BackupArtifactStoreConfig(
            bucket="foundry-artifacts",
            prefix="qa/backups",
            max_artifact_bytes=16 * 1024 * 1024,
        ),
        client=client,
    )
    receipt = store.write_backup_artifact(report)
    assert receipt["datasetVersionCount"] == 31_000


def test_s3_backup_artifact_default_capacity_is_bounded_at_64_mib() -> None:
    assert S3BackupArtifactStoreConfig(bucket="foundry-artifacts").max_artifact_bytes == 64 * 1024 * 1024


def test_s3_backup_artifact_store_rejects_missing_and_cross_prefix_refs() -> None:
    client = _MemoryS3()
    store = S3BackupArtifactStore(
        S3BackupArtifactStoreConfig(bucket="foundry-artifacts", prefix="qa/backups", max_artifact_bytes=10_000),
        client=client,
    )

    with pytest.raises(BackupArtifactIntegrityError, match="outside the configured prefix"):
        store.read_backup_artifact("s3-backup-artifact://foundry-artifacts/other/tenant/file.json")
    with pytest.raises(BackupArtifactNotFoundError, match="backup artifact not found"):
        store.read_backup_artifact(f"s3-backup-artifact://foundry-artifacts/qa/backups/tenant-demo/{'a' * 64}.json")


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
