"""Compatibility entrypoint for the Backup/Restore bounded context."""

from __future__ import annotations

from foundry_lite.application.ports import (
    BackupRestoreArtifactReceipt,
    BackupRestoreArtifactRestoreReport,
    BackupRestoreModeReport,
    BackupRestorePostRestoreValidationReport,
    BackupRestorePreflightReport,
    BackupRestoreRecoveryOverview,
)
from foundry_lite.application.services.backup_restore_artifact_execution import BackupRestoreArtifactExecutionService
from foundry_lite.application.services.backup_restore_artifact_restore import BackupRestoreArtifactRestoreService
from foundry_lite.application.services.backup_restore_artifacts import BackupRestoreArtifactService
from foundry_lite.application.services.backup_restore_mode_service import BackupRestoreModeService
from foundry_lite.application.services.backup_restore_preflight_service import BackupRestorePreflightService
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext


class BackupRestoreService(CoreService):
    """Stable public surface that delegates to focused backup/restore use cases."""

    required_dependencies = ()
    required_collaborators = (
        "backup_restore_artifact_execution_service",
        "backup_restore_artifact_restore_service",
        "backup_restore_artifact_service",
        "backup_restore_mode_service",
        "backup_restore_preflight_service",
    )
    backup_restore_artifact_execution_service: BackupRestoreArtifactExecutionService
    backup_restore_artifact_restore_service: BackupRestoreArtifactRestoreService
    backup_restore_artifact_service: BackupRestoreArtifactService
    backup_restore_mode_service: BackupRestoreModeService
    backup_restore_preflight_service: BackupRestorePreflightService

    def restore_preflight_report(
        self,
        *,
        ctx: RequestContext | None = None,
        backup_id: str | None = None,
    ) -> BackupRestorePreflightReport:
        return self.backup_restore_preflight_service.restore_preflight_report(ctx=ctx, backup_id=backup_id)

    def create_backup_artifact(
        self,
        *,
        ctx: RequestContext | None = None,
        backup_id: str | None = None,
    ) -> BackupRestoreArtifactReceipt:
        return self.backup_restore_artifact_service.create_backup_artifact(ctx=ctx, backup_id=backup_id)

    def restore_from_backup_artifact(
        self,
        *,
        ctx: RequestContext | None = None,
        artifact_ref: str,
        artifact_hash: str | None = None,
        restore_id: str | None = None,
        validation_id: str | None = None,
        should_run_post_restore_validation: bool = True,
    ) -> BackupRestoreArtifactRestoreReport:
        return self.backup_restore_artifact_restore_service.restore_from_backup_artifact(
            ctx=ctx,
            artifact_ref=artifact_ref,
            artifact_hash=artifact_hash,
            restore_id=restore_id,
            validation_id=validation_id,
            should_run_post_restore_validation=should_run_post_restore_validation,
        )

    def execute_backup_artifact_restore(
        self,
        *,
        ctx: RequestContext | None = None,
        artifact_ref: str,
        artifact_hash: str | None = None,
        restore_id: str | None = None,
        validation_id: str | None = None,
        should_run_post_restore_validation: bool = True,
    ) -> BackupRestoreArtifactRestoreReport:
        return self.backup_restore_artifact_execution_service.execute_backup_artifact_restore(
            ctx=ctx,
            artifact_ref=artifact_ref,
            artifact_hash=artifact_hash,
            restore_id=restore_id,
            validation_id=validation_id,
            should_run_post_restore_validation=should_run_post_restore_validation,
        )

    def start_restore_mode(
        self,
        *,
        ctx: RequestContext | None = None,
        backup_id: str | None = None,
        restore_id: str | None = None,
    ) -> BackupRestoreModeReport:
        return self.backup_restore_mode_service.start_restore_mode(
            ctx=ctx,
            backup_id=backup_id,
            restore_id=restore_id,
        )

    def restore_mode_status(
        self,
        restore_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> BackupRestoreModeReport:
        return self.backup_restore_mode_service.restore_mode_status(restore_id, ctx=ctx)

    def approve_restore_resume(
        self,
        restore_id: str,
        *,
        ctx: RequestContext | None = None,
        validation_id: str | None = None,
    ) -> BackupRestoreModeReport:
        return self.backup_restore_mode_service.approve_restore_resume(
            restore_id,
            ctx=ctx,
            validation_id=validation_id,
        )

    def run_post_restore_validation(
        self,
        restore_id: str,
        *,
        ctx: RequestContext | None = None,
        validation_id: str | None = None,
    ) -> BackupRestorePostRestoreValidationReport:
        return self.backup_restore_mode_service.run_post_restore_validation(
            restore_id,
            ctx=ctx,
            validation_id=validation_id,
        )

    def recovery_overview(self, *, ctx: RequestContext | None = None) -> BackupRestoreRecoveryOverview:
        return self.backup_restore_mode_service.recovery_overview(ctx=ctx)
