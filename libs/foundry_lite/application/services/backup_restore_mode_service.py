"""Backup/restore mode and resume approval use-case service."""

from __future__ import annotations

from foundry_lite.application.ports import (
    BackupRestoreModeReport,
    BackupRestorePostRestoreValidationReport,
    BackupRestoreRecoveryOverview,
)
from foundry_lite.application.services.backup_restore_mode import (
    active_restore_mode_report,
    inactive_restore_mode_report,
    latest_restore_mode_report,
)
from foundry_lite.application.services.backup_restore_overview import recovery_overview_from_audit
from foundry_lite.application.services.backup_restore_preflight_service import BackupRestorePreflightService
from foundry_lite.application.services.backup_restore_validation import (
    BackupRestoreValidationService,
    post_restore_validation_findings,
    post_restore_validation_report,
    restore_mode_report,
    resume_approved_report,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.protocols import DatasetRuntimeBoundary
from foundry_lite.domain.backup_restore.restore_mode import require_restore_resume_ready
from foundry_lite.domain.context import RequestContext


class BackupRestoreModeService(CoreService):
    """Start restore lockout, validate closed-loop evidence, and approve resume."""

    required_dependencies = ("engine", "runtime_repository")
    required_collaborators = (
        "backup_restore_preflight_service",
        "backup_restore_validation_service",
        "runtime_service",
    )
    backup_restore_preflight_service: BackupRestorePreflightService
    backup_restore_validation_service: BackupRestoreValidationService
    runtime_service: DatasetRuntimeBoundary

    def start_restore_mode(
        self,
        *,
        ctx: RequestContext | None = None,
        backup_id: str | None = None,
        restore_id: str | None = None,
    ) -> BackupRestoreModeReport:
        ctx = ctx or RequestContext()
        resolved_restore_id = restore_id or backup_id or f"restore-{ctx.request_id}"
        self.runtime_service._require_or_audit(ctx, "operations:retry", "backup_restore", resolved_restore_id)
        existing = self._existing_restore_mode_report(ctx, resolved_restore_id)
        if existing is not None:
            return existing
        preflight = self.backup_restore_preflight_service.restore_preflight_report(
            ctx=ctx,
            backup_id=backup_id or resolved_restore_id,
        )
        report = restore_mode_report(ctx, resolved_restore_id, preflight)
        with self.engine.begin() as conn:
            self.backup_restore_validation_service.audit_restore_mode(conn, ctx, report)
        return report

    def restore_mode_status(
        self,
        restore_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> BackupRestoreModeReport:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "operations:read:detail", "backup_restore", restore_id)
        existing = self._existing_restore_mode_report(ctx, restore_id)
        return existing or inactive_restore_mode_report(ctx, restore_id)

    def approve_restore_resume(
        self,
        restore_id: str,
        *,
        ctx: RequestContext | None = None,
        validation_id: str | None = None,
    ) -> BackupRestoreModeReport:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "operations:retry", "backup_restore", restore_id)
        current = self.backup_restore_validation_service.approval_candidate(ctx, restore_id)
        if current["status"] == "resume_approved":
            return current
        validation = self.backup_restore_validation_service.required_passed_post_restore_validation(
            ctx,
            restore_id,
            validation_id,
        )
        preflight = self.backup_restore_preflight_service.restore_preflight_report(
            ctx=ctx,
            backup_id=current["backupId"] or restore_id,
        )
        findings = post_restore_validation_findings(preflight)
        require_restore_resume_ready(restore_id, findings)
        report = resume_approved_report(ctx, current, preflight, validation["validationId"])
        with self.engine.begin() as conn:
            self.backup_restore_validation_service.audit_restore_mode(conn, ctx, report)
        return report

    def run_post_restore_validation(
        self,
        restore_id: str,
        *,
        ctx: RequestContext | None = None,
        validation_id: str | None = None,
    ) -> BackupRestorePostRestoreValidationReport:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "operations:retry", "backup_restore", restore_id)
        existing = self.backup_restore_validation_service.existing_post_restore_validation(
            ctx,
            restore_id,
            validation_id,
        )
        if existing is not None:
            return existing
        current = self.backup_restore_validation_service.approval_candidate(ctx, restore_id)
        preflight = self.backup_restore_preflight_service.restore_preflight_report(
            ctx=ctx,
            backup_id=current["backupId"] or restore_id,
        )
        report = post_restore_validation_report(ctx, current, preflight, validation_id)
        with self.engine.begin() as conn:
            self.backup_restore_validation_service.audit_post_restore_validation(conn, ctx, report)
        return report

    def recovery_overview(self, *, ctx: RequestContext | None = None) -> BackupRestoreRecoveryOverview:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "operations:read:summary", "backup_restore", "recovery")
        snapshot = self.runtime_repository.list_runs(tenant_id=ctx.tenant_id)
        audit_events = snapshot["auditEvents"]
        active_mode = active_restore_mode_report(audit_events)
        latest_mode = latest_restore_mode_report(audit_events)
        return recovery_overview_from_audit(
            ctx,
            active_mode=active_mode,
            latest_mode=latest_mode,
            audit_events=audit_events,
        )

    def _existing_restore_mode_report(self, ctx: RequestContext, restore_id: str) -> BackupRestoreModeReport | None:
        return self.backup_restore_validation_service.existing_restore_mode_report(ctx, restore_id)
