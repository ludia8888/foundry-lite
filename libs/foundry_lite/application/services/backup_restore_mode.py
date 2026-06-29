"""Helpers for reconstructing durable backup/restore mode from audit evidence."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from foundry_lite.application.ports import BackupRestoreModeReport, RuntimeRow
from foundry_lite.domain.context import RequestContext

RESTORE_MODE_BLOCKED_EVENT = "backup_restore.restore_mode_blocked"
RESTORE_MODE_STARTED_EVENT = "backup_restore.restore_mode_started"
RESTORE_MODE_RESUME_APPROVED_EVENT = "backup_restore.restore_mode_resume_approved"
POST_RESTORE_VALIDATED_EVENT = "backup_restore.post_restore_validated"
RESTORE_MODE_EVENTS = (
    RESTORE_MODE_BLOCKED_EVENT,
    RESTORE_MODE_STARTED_EVENT,
    RESTORE_MODE_RESUME_APPROVED_EVENT,
)


def inactive_restore_mode_report(ctx: RequestContext, restore_id: str) -> BackupRestoreModeReport:
    """Return the default status when no restore-mode audit event exists."""
    return {
        "generatedAt": "",
        "tenantId": ctx.tenant_id,
        "restoreId": restore_id,
        "backupId": "",
        "status": "inactive",
        "preflightStatus": "not_run",
        "is_write_traffic_paused": False,
        "is_outbox_publisher_paused": False,
        "is_serving_traffic_open": True,
        "is_post_restore_validation_required": False,
        "is_operator_approval_required": False,
        "blockingIssueCount": 0,
        "reason": "no restore mode event exists for this restore id",
        "highWatermarks": {},
        "summary": {"activeRestoreMode": False},
    }


def latest_restore_mode_report(
    audit_events: Sequence[RuntimeRow],
    *,
    restore_id: str | None = None,
) -> BackupRestoreModeReport | None:
    """Return the latest restore-mode report encoded in audit rows."""
    candidates = [
        row
        for row in audit_events
        if row.get("event_type") in RESTORE_MODE_EVENTS and _matches_restore_id(row, restore_id)
    ]
    if not candidates:
        return None
    latest = max(candidates, key=_created_at)
    return _report_from_audit_row(latest)


def active_restore_mode_report(audit_events: Sequence[RuntimeRow]) -> BackupRestoreModeReport | None:
    """Return an active restore-mode lockout report, if one is still blocking."""
    active = [
        report
        for report in _latest_restore_mode_reports_by_id(audit_events).values()
        if _is_active_restore_mode_report(report)
    ]
    if not active:
        return None
    return max(active, key=lambda report: report["generatedAt"])


def _latest_restore_mode_reports_by_id(audit_events: Sequence[RuntimeRow]) -> dict[str, BackupRestoreModeReport]:
    latest_rows: dict[str, RuntimeRow] = {}
    for row in audit_events:
        if row.get("event_type") not in RESTORE_MODE_EVENTS:
            continue
        restore_id = row.get("resource_id")
        if not isinstance(restore_id, str) or restore_id == "":
            continue
        current = latest_rows.get(restore_id)
        if current is None or _created_at(row) > _created_at(current):
            latest_rows[restore_id] = row
    return {restore_id: _report_from_audit_row(row) for restore_id, row in latest_rows.items()}


def _is_active_restore_mode_report(report: BackupRestoreModeReport) -> bool:
    if report["status"] == "resume_approved":
        return False
    return report["is_outbox_publisher_paused"] or not report["is_serving_traffic_open"]


def _matches_restore_id(row: RuntimeRow, restore_id: str | None) -> bool:
    """Check whether an audit row belongs to the requested restore id."""
    if restore_id is None:
        return True
    return row.get("resource_id") == restore_id


def _created_at(row: RuntimeRow) -> str:
    """Read the audit timestamp used for latest-event selection."""
    value = row.get("created_at")
    return value if isinstance(value, str) else ""


def _report_from_audit_row(row: RuntimeRow) -> BackupRestoreModeReport:
    """Decode a restore-mode report from an audit row payload."""
    after_ref = row.get("after_ref")
    if isinstance(after_ref, dict) and isinstance(after_ref.get("restoreId"), str):
        return cast(BackupRestoreModeReport, after_ref)
    raise ValueError("restore mode audit event is missing report payload")
