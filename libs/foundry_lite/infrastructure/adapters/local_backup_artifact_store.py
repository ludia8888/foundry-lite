"""Infrastructure adapter implementation for local backup artifact store."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from foundry_lite.application.ports.backup_artifact_store import (
    BackupArtifactNotFoundError,
)
from foundry_lite.application.runtime_repository_backup_restore import (
    BackupRestoreArtifact,
    BackupRestoreArtifactReceipt,
    BackupRestorePreflightReport,
)
from foundry_lite.infrastructure.adapters.backup_artifact_codec import (
    backup_artifact_hash,
    backup_artifact_payload,
    build_backup_artifact,
    replay_backup_artifact_receipt,
    verify_backup_artifact,
)


@dataclass(frozen=True)
class LocalBackupArtifactStore:
    """Local JSON backup artifact store for recovery drills and CI evidence."""

    root: Path
    profile_name: str = "local-backup-artifact-store"

    def write_backup_artifact(self, report: BackupRestorePreflightReport) -> BackupRestoreArtifactReceipt:
        artifact_path = self._artifact_path(report)
        artifact_ref = str(artifact_path)
        payload = backup_artifact_payload(report)
        artifact_hash = backup_artifact_hash(payload)
        if artifact_path.exists():
            return replay_backup_artifact_receipt(
                self._read_bytes(artifact_path),
                artifact_ref,
                requested_hash=artifact_hash,
                backup_id=report["backupId"],
            )
        receipt, encoded = build_backup_artifact(report, artifact_ref)
        self._write_artifact(artifact_path, encoded)
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
        return verify_backup_artifact(self._read_bytes(artifact_path), artifact_ref, expected_hash)

    def _artifact_path(self, report: BackupRestorePreflightReport) -> Path:
        token = sha256(f"{report['tenantId']}:{report['backupId']}".encode()).hexdigest()
        return self.root / report["tenantId"] / f"{token}.json"

    def _write_artifact(self, path: Path, encoded: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_bytes(encoded)
        tmp_path.replace(path)

    @staticmethod
    def _read_bytes(path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise BackupArtifactNotFoundError(str(path)) from exc
