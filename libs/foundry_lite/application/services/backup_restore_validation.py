"""Restore-mode report builders and post-restore validation checks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports import (
    BackupRestoreModeReport,
    BackupRestorePostRestoreValidationReport,
    BackupRestorePreflightReport,
    RuntimeJsonObject,
    RuntimeRepository,
    RuntimeRow,
    TransactionContext,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.backup_restore_mode import (
    POST_RESTORE_VALIDATED_EVENT,
    RESTORE_MODE_BLOCKED_EVENT,
    RESTORE_MODE_RESUME_APPROVED_EVENT,
    RESTORE_MODE_STARTED_EVENT,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.protocols import DatasetRuntimeBoundary
from foundry_lite.domain.backup_restore.restore_mode import (
    post_restore_validation_findings as _domain_post_restore_validation_findings,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound


class BackupRestoreValidationService(CoreService):
    """Post-restore validation and restore-mode audit boundary."""

    required_dependencies = ("runtime_repository",)
    required_collaborators = ("runtime_service",)
    runtime_repository: RuntimeRepository
    runtime_service: DatasetRuntimeBoundary

    def existing_restore_mode_report(self, ctx: RequestContext, restore_id: str) -> BackupRestoreModeReport | None:
        snapshot = self.runtime_repository.list_runs(tenant_id=ctx.tenant_id)
        return latest_restore_mode_report(snapshot["auditEvents"], restore_id)

    def approval_candidate(self, ctx: RequestContext, restore_id: str) -> BackupRestoreModeReport:
        current = self.existing_restore_mode_report(ctx, restore_id)
        return self._require_approval_candidate(current, restore_id)

    def post_restore_validation_candidate(
        self,
        ctx: RequestContext,
        restore_id: str,
        validation_id: str | None,
    ) -> tuple[BackupRestoreModeReport, BackupRestorePostRestoreValidationReport | None]:
        snapshot = self.runtime_repository.list_runs(tenant_id=ctx.tenant_id)
        audit_events = snapshot["auditEvents"]
        current = self._require_approval_candidate(latest_restore_mode_report(audit_events, restore_id), restore_id)
        existing = latest_post_restore_validation_report(audit_events, restore_id)
        if validation_id is None or existing is None or existing["validationId"] != validation_id:
            return current, None
        return current, existing

    @staticmethod
    def _require_approval_candidate(
        current: BackupRestoreModeReport | None,
        restore_id: str,
    ) -> BackupRestoreModeReport:
        if current is None:
            raise NotFound("restore mode not found", details={"restore_id": restore_id})
        if current["status"] == "blocked":
            raise ConflictDetected(
                "blocked restore mode cannot approve publisher resume",
                details={"restore_id": restore_id, "blockingIssueCount": current["blockingIssueCount"]},
            )
        return current

    def required_passed_post_restore_validation(
        self,
        ctx: RequestContext,
        restore_id: str,
        validation_id: str | None,
    ) -> BackupRestorePostRestoreValidationReport:
        snapshot = self.runtime_repository.list_runs(tenant_id=ctx.tenant_id)
        report = latest_post_restore_validation_report(snapshot["auditEvents"], restore_id)
        if report is None or report["status"] != "passed":
            raise ConflictDetected(
                "post-restore validation must pass before publisher resume",
                details={"restore_id": restore_id, "validation_id": validation_id},
            )
        if validation_id is not None and report["validationId"] != validation_id:
            raise ConflictDetected(
                "post-restore validation id does not match latest passed evidence",
                details={"restore_id": restore_id, "validation_id": validation_id},
            )
        return report

    def audit_restore_mode(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        report: BackupRestoreModeReport,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type=restore_mode_event_type(report),
            resource_type="backup_restore",
            resource_id=report["restoreId"],
            action="operations:retry",
            after_ref=report,
            correlation_id=ctx.request_id,
        )

    def audit_post_restore_validation(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        report: BackupRestorePostRestoreValidationReport,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type=post_restore_validation_event_type(),
            resource_type="backup_restore",
            resource_id=report["restoreId"],
            action="operations:retry",
            after_ref=report,
            correlation_id=ctx.request_id,
        )


def restore_mode_report(
    ctx: RequestContext,
    restore_id: str,
    preflight: BackupRestorePreflightReport,
) -> BackupRestoreModeReport:
    """Build the paused or blocked restore-mode status from preflight evidence."""
    is_blocked = preflight["status"] == "blocked"
    blocking_issue_count = len(preflight["issues"])
    return {
        "generatedAt": _now(),
        "tenantId": ctx.tenant_id,
        "restoreId": restore_id,
        "backupId": preflight["backupId"],
        "status": "blocked" if is_blocked else "paused",
        "preflightStatus": preflight["status"],
        "is_write_traffic_paused": True,
        "is_outbox_publisher_paused": True,
        "is_serving_traffic_open": False,
        "is_post_restore_validation_required": True,
        "is_operator_approval_required": True,
        "blockingIssueCount": blocking_issue_count,
        "reason": _restore_mode_reason(is_blocked, blocking_issue_count),
        "highWatermarks": preflight["highWatermarks"],
        "summary": _restore_mode_summary(preflight, is_blocked),
    }


def post_restore_validation_report(
    ctx: RequestContext,
    current: BackupRestoreModeReport,
    preflight: BackupRestorePreflightReport,
    validation_id: str | None,
) -> BackupRestorePostRestoreValidationReport:
    """Build durable post-restore validation evidence for operator approval."""
    findings = post_restore_validation_findings(preflight)
    resolved_validation_id = validation_id or f"post-restore:{current['restoreId']}:{preflight['backupId']}"
    return {
        "generatedAt": _now(),
        "tenantId": ctx.tenant_id,
        "restoreId": current["restoreId"],
        "backupId": current["backupId"] or preflight["backupId"],
        "validationId": resolved_validation_id,
        "status": "blocked" if findings else "passed",
        "preflightStatus": preflight["status"],
        "findings": findings,
        "highWatermarks": preflight["highWatermarks"],
        "summary": _post_restore_validation_summary(preflight, resolved_validation_id, findings),
    }


def resume_approved_report(
    ctx: RequestContext,
    current: BackupRestoreModeReport,
    preflight: BackupRestorePreflightReport,
    validation_id: str | None,
) -> BackupRestoreModeReport:
    """Build the approved status that reopens current retry/reprocess entrypoints."""
    return {
        "generatedAt": _now(),
        "tenantId": ctx.tenant_id,
        "restoreId": current["restoreId"],
        "backupId": current["backupId"],
        "status": "resume_approved",
        "preflightStatus": preflight["status"],
        "is_write_traffic_paused": False,
        "is_outbox_publisher_paused": False,
        "is_serving_traffic_open": True,
        "is_post_restore_validation_required": False,
        "is_operator_approval_required": False,
        "blockingIssueCount": 0,
        "reason": "post-restore closed-loop validation passed and operator approved publisher resume",
        "highWatermarks": preflight["highWatermarks"],
        "summary": _resume_approved_summary(preflight, validation_id),
    }


def post_restore_validation_findings(preflight: BackupRestorePreflightReport) -> list[RuntimeJsonObject]:
    """Return blocking validation findings that prevent restore resume approval."""
    return [dict(finding) for finding in _domain_post_restore_validation_findings(preflight)]


def restore_mode_event_type(report: BackupRestoreModeReport) -> str:
    """Return the audit event type that matches a restore-mode status."""
    if report["status"] == "resume_approved":
        return RESTORE_MODE_RESUME_APPROVED_EVENT
    if report["status"] == "blocked":
        return RESTORE_MODE_BLOCKED_EVENT
    return RESTORE_MODE_STARTED_EVENT


def latest_post_restore_validation_report(
    audit_events: Sequence[RuntimeRow],
    restore_id: str,
) -> BackupRestorePostRestoreValidationReport | None:
    """Return the latest validation report for a restore id from audit evidence."""
    rows = [
        row
        for row in audit_events
        if row.get("event_type") == POST_RESTORE_VALIDATED_EVENT and row.get("resource_id") == restore_id
    ]
    if not rows:
        return None
    latest = max(rows, key=_created_at)
    after_ref = latest.get("after_ref")
    if isinstance(after_ref, dict) and isinstance(after_ref.get("validationId"), str):
        return cast(BackupRestorePostRestoreValidationReport, after_ref)
    raise ValueError("post-restore validation audit event is missing report payload")


def latest_restore_mode_report(
    audit_events: Sequence[RuntimeRow],
    restore_id: str,
) -> BackupRestoreModeReport | None:
    """Return the latest restore mode report for one restore id."""
    rows = [
        row
        for row in audit_events
        if row.get("event_type")
        in {RESTORE_MODE_BLOCKED_EVENT, RESTORE_MODE_RESUME_APPROVED_EVENT, RESTORE_MODE_STARTED_EVENT}
        and row.get("resource_id") == restore_id
    ]
    if not rows:
        return None
    latest = max(rows, key=_created_at)
    after_ref = latest.get("after_ref")
    if isinstance(after_ref, dict) and isinstance(after_ref.get("restoreId"), str):
        return cast(BackupRestoreModeReport, after_ref)
    raise ValueError("restore-mode audit event is missing report payload")


def post_restore_validation_event_type() -> str:
    """Return the audit event type used for post-restore validation evidence."""
    return POST_RESTORE_VALIDATED_EVENT


def _restore_mode_reason(is_blocked: bool, issue_count: int) -> str:
    if is_blocked:
        return f"restore mode blocked by {issue_count} DB/storage preflight issue(s)"
    return "restore mode started with write traffic and outbox publisher paused"


def _restore_mode_summary(preflight: BackupRestorePreflightReport, is_blocked: bool) -> RuntimeJsonObject:
    return {
        "activeRestoreMode": True,
        "preflightIssueCount": len(preflight["issues"]),
        "datasetVersionCount": len(preflight["datasetVersions"]),
        "outboxResumeRequires": "post_restore_validation_and_operator_approval",
        "restoreFailureKeepsServingTrafficClosed": is_blocked,
    }


def _resume_approved_summary(
    preflight: BackupRestorePreflightReport,
    validation_id: str | None,
) -> RuntimeJsonObject:
    return {
        "activeRestoreMode": False,
        "validationId": validation_id or f"post-restore:{preflight['backupId']}",
        "postRestoreValidation": "dataset_object_action_materialization_closed_loop",
        "datasetVersionCount": len(preflight["datasetVersions"]),
        "activeIndexPointerCount": len(preflight["activeIndexPointers"]),
        "actionRunCount": _watermark_count(preflight["highWatermarks"], "actionRuns"),
        "materializationRunCount": _watermark_count(preflight["highWatermarks"], "materializationRuns"),
        "outboxResumeApproved": True,
    }


def _post_restore_validation_summary(
    preflight: BackupRestorePreflightReport,
    validation_id: str,
    findings: list[RuntimeJsonObject],
) -> RuntimeJsonObject:
    return {
        "validationId": validation_id,
        "postRestoreValidation": "dataset_object_action_materialization_closed_loop",
        "datasetVersionCount": len(preflight["datasetVersions"]),
        "activeIndexPointerCount": len(preflight["activeIndexPointers"]),
        "actionRunCount": _watermark_count(preflight["highWatermarks"], "actionRuns"),
        "materializationRunCount": _watermark_count(preflight["highWatermarks"], "materializationRuns"),
        "findingCount": len(findings),
        "outboxResumeEligible": len(findings) == 0,
    }


def _watermark_count(high_watermarks: RuntimeJsonObject, table_name: str) -> int:
    table = high_watermarks.get(table_name)
    if isinstance(table, Mapping) and isinstance(table.get("count"), int):
        return int(table["count"])
    return 0


def _created_at(row: RuntimeRow) -> str:
    value = row.get("created_at")
    return value if isinstance(value, str) else ""
