"""Restore-mode report builders and post-restore validation checks."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import BackupRestoreModeReport, BackupRestorePreflightReport, RuntimeJsonObject
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.backup_restore_mode import (
    RESTORE_MODE_BLOCKED_EVENT,
    RESTORE_MODE_RESUME_APPROVED_EVENT,
    RESTORE_MODE_STARTED_EVENT,
)
from foundry_lite.domain.context import RequestContext


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
    findings: list[RuntimeJsonObject] = []
    if preflight["status"] != "ready":
        findings.append({"check": "metadata_storage_preflight", "status": preflight["status"]})
    if len(preflight["datasetVersions"]) == 0:
        findings.append({"check": "dataset_version_inventory", "status": "missing"})
    if len(preflight["activeIndexPointers"]) == 0:
        findings.append({"check": "object_index_pointer", "status": "missing"})
    findings.extend(_missing_runtime_evidence(preflight["highWatermarks"]))
    return findings


def restore_mode_event_type(report: BackupRestoreModeReport) -> str:
    """Return the audit event type that matches a restore-mode status."""
    if report["status"] == "resume_approved":
        return RESTORE_MODE_RESUME_APPROVED_EVENT
    if report["status"] == "blocked":
        return RESTORE_MODE_BLOCKED_EVENT
    return RESTORE_MODE_STARTED_EVENT


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


def _missing_runtime_evidence(high_watermarks: RuntimeJsonObject) -> list[RuntimeJsonObject]:
    findings: list[RuntimeJsonObject] = []
    for table_name in ("actionRuns", "materializationRuns"):
        if _watermark_count(high_watermarks, table_name) == 0:
            findings.append({"check": table_name, "status": "missing"})
    return findings


def _watermark_count(high_watermarks: RuntimeJsonObject, table_name: str) -> int:
    table = high_watermarks.get(table_name)
    if isinstance(table, Mapping) and isinstance(table.get("count"), int):
        return int(table["count"])
    return 0
