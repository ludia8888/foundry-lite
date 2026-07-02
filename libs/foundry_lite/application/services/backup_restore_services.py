"""Service group for the Backup/Restore bounded context."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.backup_restore_artifact_execution import BackupRestoreArtifactExecutionService
from foundry_lite.application.services.backup_restore_artifact_restore import BackupRestoreArtifactRestoreService
from foundry_lite.application.services.backup_restore_artifacts import BackupRestoreArtifactService
from foundry_lite.application.services.backup_restore_mode_service import BackupRestoreModeService
from foundry_lite.application.services.backup_restore_preflight_service import BackupRestorePreflightService
from foundry_lite.application.services.backup_restore_service import BackupRestoreService
from foundry_lite.application.services.backup_restore_validation import BackupRestoreValidationService
from foundry_lite.application.services.base import CoreService, build_service


@dataclass(frozen=True)
class BackupRestoreServices:
    """Backup/restore use-case services plus the stable compatibility entrypoint."""

    artifact: BackupRestoreArtifactService
    artifact_execution: BackupRestoreArtifactExecutionService
    artifact_restore: BackupRestoreArtifactRestoreService
    entrypoint: BackupRestoreService
    mode: BackupRestoreModeService
    preflight: BackupRestorePreflightService
    validation: BackupRestoreValidationService

    @classmethod
    def create(cls, dependencies: CoreDependencies) -> BackupRestoreServices:
        return cls(
            artifact=build_service(BackupRestoreArtifactService, dependencies),
            artifact_execution=build_service(BackupRestoreArtifactExecutionService, dependencies),
            artifact_restore=build_service(BackupRestoreArtifactRestoreService, dependencies),
            entrypoint=build_service(BackupRestoreService, dependencies),
            mode=build_service(BackupRestoreModeService, dependencies),
            preflight=build_service(BackupRestorePreflightService, dependencies),
            validation=build_service(BackupRestoreValidationService, dependencies),
        )

    def items(self) -> tuple[CoreService, ...]:
        return (
            self.artifact,
            self.artifact_execution,
            self.artifact_restore,
            self.entrypoint,
            self.mode,
            self.preflight,
            self.validation,
        )
