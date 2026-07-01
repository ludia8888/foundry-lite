"""Application service helpers for backup restore overview workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports import (
    BackupRestoreModeReport,
    BackupRestorePostRestoreValidationSummary,
    BackupRestorePreflightSummary,
    BackupRestoreRecoveryOverview,
    RuntimeJsonObject,
    RuntimeRow,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.backup_restore_mode import POST_RESTORE_VALIDATED_EVENT
from foundry_lite.domain.context import RequestContext


def recovery_overview_from_audit(
    ctx: RequestContext,
    *,
    active_mode: BackupRestoreModeReport | None,
    latest_mode: BackupRestoreModeReport | None,
    audit_events: Sequence[RuntimeRow],
) -> BackupRestoreRecoveryOverview:
    latest_preflight = _latest_preflight_summary(audit_events)
    latest_validation = _latest_validation_summary(audit_events, active_mode)
    actions = _required_operator_actions(active_mode, latest_preflight, latest_validation)
    return {
        "generatedAt": _now(),
        "tenantId": ctx.tenant_id,
        "activeRestoreMode": active_mode,
        "latestRestoreMode": latest_mode,
        "latestPreflight": latest_preflight,
        "latestPostRestoreValidation": latest_validation,
        "restoreTrafficGate": _overview_traffic_gate(active_mode),
        "requiredOperatorActions": actions,
        "summary": {
            "activeRestoreMode": active_mode is not None,
            "latestRestoreStatus": latest_mode["status"] if latest_mode is not None else "none",
            "preflightStatus": latest_preflight["status"] if latest_preflight is not None else "not_run",
            "postRestoreValidationStatus": latest_validation["status"] if latest_validation is not None else "not_run",
            "blockingIssueCount": active_mode["blockingIssueCount"] if active_mode is not None else 0,
            "requiredOperatorActionCount": len(actions),
        },
    }


def _latest_preflight_summary(audit_events: Sequence[RuntimeRow]) -> BackupRestorePreflightSummary | None:
    preflight_rows = [
        row
        for row in audit_events
        if row.get("event_type") == "backup_restore.preflight_reported" and isinstance(row.get("after_ref"), Mapping)
    ]
    if not preflight_rows:
        return None
    latest = max(preflight_rows, key=_created_at)
    after_ref = cast(Mapping[str, object], latest["after_ref"])
    return {
        "generatedAt": _created_at(latest),
        "backupId": _string_or_empty(latest.get("resource_id")),
        "status": _string_or_empty(after_ref.get("status")),
        "datasetVersionCount": _int_or_zero(after_ref.get("datasetVersionCount")),
        "issueCount": _int_or_zero(after_ref.get("issueCount")),
        "activeIndexPointerCount": _int_or_zero(after_ref.get("activeIndexPointerCount")),
    }


def _latest_validation_summary(
    audit_events: Sequence[RuntimeRow],
    active_mode: BackupRestoreModeReport | None,
) -> BackupRestorePostRestoreValidationSummary | None:
    restore_id = active_mode["restoreId"] if active_mode is not None else None
    validation_rows = [
        row
        for row in audit_events
        if row.get("event_type") == POST_RESTORE_VALIDATED_EVENT
        and isinstance(row.get("after_ref"), Mapping)
        and (restore_id is None or row.get("resource_id") == restore_id)
    ]
    if not validation_rows:
        return None
    latest = max(validation_rows, key=_created_at)
    after_ref = cast(Mapping[str, object], latest["after_ref"])
    findings = after_ref.get("findings")
    return {
        "generatedAt": _created_at(latest),
        "restoreId": _string_or_empty(after_ref.get("restoreId")),
        "backupId": _string_or_empty(after_ref.get("backupId")),
        "validationId": _string_or_empty(after_ref.get("validationId")),
        "status": _string_or_empty(after_ref.get("status")),
        "findingCount": len(findings) if isinstance(findings, list) else 0,
    }


def _overview_traffic_gate(active_mode: BackupRestoreModeReport | None) -> RuntimeJsonObject:
    if active_mode is None:
        return {
            "isWriteTrafficPaused": False,
            "isOutboxPublisherPaused": False,
            "isServingTrafficOpen": True,
            "activeRestoreId": None,
            "reason": "no active restore mode",
        }
    return {
        "isWriteTrafficPaused": active_mode["is_write_traffic_paused"],
        "isOutboxPublisherPaused": active_mode["is_outbox_publisher_paused"],
        "isServingTrafficOpen": active_mode["is_serving_traffic_open"],
        "activeRestoreId": active_mode["restoreId"],
        "reason": active_mode["reason"],
    }


def _required_operator_actions(
    active_mode: BackupRestoreModeReport | None,
    latest_preflight: BackupRestorePreflightSummary | None,
    latest_validation: BackupRestorePostRestoreValidationSummary | None,
) -> list[str]:
    actions: list[str] = []
    if latest_preflight is None:
        actions.append("run_restore_preflight")
    elif latest_preflight["issueCount"] > 0:
        actions.append("resolve_restore_blocking_issues")
    if active_mode is None:
        return actions
    if active_mode["is_post_restore_validation_required"] and _validation_not_passed(latest_validation):
        actions.append("run_post_restore_closed_loop_validation")
    if active_mode["is_operator_approval_required"]:
        actions.append("approve_restore_resume")
    return actions


def _validation_not_passed(validation: BackupRestorePostRestoreValidationSummary | None) -> bool:
    return validation is None or validation["status"] != "passed"


def _created_at(row: RuntimeRow) -> str:
    value = row.get("created_at")
    return value if isinstance(value, str) else ""


def _string_or_empty(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int_or_zero(value: object) -> int:
    return value if isinstance(value, int) else 0
