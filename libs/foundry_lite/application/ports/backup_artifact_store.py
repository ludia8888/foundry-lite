"""Application port contract for backup artifact store."""

from __future__ import annotations

from typing import Protocol

from foundry_lite.application.runtime_repository_backup_restore import (
    BackupRestoreArtifact,
    BackupRestoreArtifactReceipt,
    BackupRestorePreflightReport,
)


class BackupArtifactConflictError(Exception):
    """Raised when one backup id already points at a different immutable artifact."""

    def __init__(self, backup_id: str, existing_hash: str, requested_hash: str) -> None:
        self.backup_id = backup_id
        self.existing_hash = existing_hash
        self.requested_hash = requested_hash
        super().__init__(f"backup artifact already exists for {backup_id}")


class BackupArtifactNotFoundError(Exception):
    """Raised when an artifact reference does not exist in the configured store."""

    def __init__(self, artifact_ref: str) -> None:
        self.artifact_ref = artifact_ref
        super().__init__(f"backup artifact not found: {artifact_ref}")


class BackupArtifactIntegrityError(Exception):
    """Raised when an artifact cannot prove its receipt hash or manifest contract."""

    def __init__(self, artifact_ref: str, reason: str) -> None:
        self.artifact_ref = artifact_ref
        self.reason = reason
        super().__init__(f"backup artifact integrity check failed: {reason}")


class BackupArtifactStore(Protocol):
    """Durable storage boundary for backup/restore preflight artifacts."""

    @property
    def profile_name(self) -> str: ...

    def write_backup_artifact(self, report: BackupRestorePreflightReport) -> BackupRestoreArtifactReceipt:
        """Persist a ready preflight report as an immutable restore artifact."""
        ...

    def read_backup_artifact(
        self,
        artifact_ref: str,
        *,
        expected_hash: str | None = None,
    ) -> BackupRestoreArtifact:
        """Read and verify an immutable restore artifact before restore-mode entry."""
        ...
