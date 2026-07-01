"""Thin facade entrypoints for operations console backup workflows."""

from __future__ import annotations

from foundry_lite.application.facades.operations_console_protocols import BackupRestoreReporter
from foundry_lite.application.ports import (
    BackupRestoreArtifactReceipt,
    BackupRestoreArtifactRestoreReport,
    BackupRestoreModeReport,
    BackupRestorePostRestoreValidationReport,
    BackupRestorePreflightReport,
    BackupRestoreRecoveryOverview,
)
from foundry_lite.domain.context import RequestContext


class OperationsConsoleBackupMixin:
    _backup_restore: BackupRestoreReporter

    def restore_preflight_report(
        self,
        *,
        ctx: RequestContext | None = None,
        backup_id: str | None = None,
    ) -> BackupRestorePreflightReport:
        return self._backup_restore.restore_preflight_report(ctx=ctx, backup_id=backup_id)

    def create_backup_artifact(
        self,
        *,
        ctx: RequestContext | None = None,
        backup_id: str | None = None,
    ) -> BackupRestoreArtifactReceipt:
        return self._backup_restore.create_backup_artifact(ctx=ctx, backup_id=backup_id)

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
        return self._backup_restore.restore_from_backup_artifact(
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
        return self._backup_restore.execute_backup_artifact_restore(
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
        return self._backup_restore.start_restore_mode(ctx=ctx, backup_id=backup_id, restore_id=restore_id)

    def restore_mode_status(
        self,
        restore_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> BackupRestoreModeReport:
        return self._backup_restore.restore_mode_status(restore_id, ctx=ctx)

    def approve_restore_resume(
        self,
        restore_id: str,
        *,
        ctx: RequestContext | None = None,
        validation_id: str | None = None,
    ) -> BackupRestoreModeReport:
        return self._backup_restore.approve_restore_resume(restore_id, ctx=ctx, validation_id=validation_id)

    def run_post_restore_validation(
        self,
        restore_id: str,
        *,
        ctx: RequestContext | None = None,
        validation_id: str | None = None,
    ) -> BackupRestorePostRestoreValidationReport:
        return self._backup_restore.run_post_restore_validation(restore_id, ctx=ctx, validation_id=validation_id)

    def recovery_overview(self, *, ctx: RequestContext | None = None) -> BackupRestoreRecoveryOverview:
        return self._backup_restore.recovery_overview(ctx=ctx)
