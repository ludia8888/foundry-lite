"""Application service helpers for action reconciliation helpers workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.action_types import (
    ActionApplyResponse,
    ActionWritebackQueueItem,
    ActionWritebackReconciliationResult,
    ActionWritebackRecoveryItem,
    ActionWritebackRecoveryResult,
)
from foundry_lite.application.ports.action_repository import ActionRunRow, ActionWritebackRecord
from foundry_lite.domain.errors import FoundryLiteError, ValidationFailed

RECONCILABLE_WRITEBACK_STATUSES = ("outcome_unknown", "compensation_required")
UNRESOLVED_WRITEBACK_STATUSES = (*RECONCILABLE_WRITEBACK_STATUSES, "retryable")


def validate_remote_success(remote_status: str, remote_resource_id: str) -> None:
    if remote_status != "succeeded":
        raise ValidationFailed(
            "only remote success reconciliation is supported",
            details={"remote_status": remote_status},
        )
    if not remote_resource_id:
        raise ValidationFailed("remote resource id is required")


def is_resolvable_writeback(writeback: ActionWritebackRecord, action_run: ActionRunRow) -> bool:
    return (
        writeback.status in RECONCILABLE_WRITEBACK_STATUSES and action_run["status"] in RECONCILABLE_WRITEBACK_STATUSES
    )


def queue_statuses(status: str | None) -> Sequence[str]:
    if status is None:
        return UNRESOLVED_WRITEBACK_STATUSES
    if status not in UNRESOLVED_WRITEBACK_STATUSES:
        raise ValidationFailed(
            "reconciliation queue status must be unresolved",
            details={"status": status, "allowed": list(UNRESOLVED_WRITEBACK_STATUSES)},
        )
    return (status,)


def validate_queue_limit(limit: int) -> None:
    if limit < 1 or limit > 100:
        raise ValidationFailed("reconciliation queue limit must be between 1 and 100", details={"limit": limit})


def queue_item(writeback: ActionWritebackRecord, sensitive: set[str]) -> ActionWritebackQueueItem:
    response = redact_mapping(writeback.response, sensitive)
    evidence = dict(response or {})
    return {
        "writebackId": writeback.writeback_id,
        "actionRunId": writeback.action_run_id,
        "status": writeback.status,
        "mode": writeback.mode,
        "connectorId": writeback.connector_id,
        "idempotencyKey": writeback.idempotency_key,
        "attempts": writeback.attempts,
        "createdAt": writeback.created_at,
        "completedAt": writeback.completed_at,
        "reconciliationDeadline": evidence.get("reconciliation_deadline"),
        "remoteResourceId": evidence.get("remote_resource_id"),
        "lastObservedStatus": evidence.get("last_observed_status"),
        "compensationActionType": evidence.get("compensation_action_type"),
        "request": redact_mapping(writeback.request, sensitive) or {},
        "response": response,
    }


def external_writeback_uri(writeback: ActionWritebackRecord) -> str | None:
    value = writeback.request.get("externalWritebackUri")
    return value if isinstance(value, str) and value else None


def recovery_result(items: list[ActionWritebackRecoveryItem]) -> ActionWritebackRecoveryResult:
    return {
        "processed": len(items),
        "reconciled": _decision_count(items, "reconciled"),
        "skipped": _decision_count(items, "skipped"),
        "failed": _decision_count(items, "failed"),
        "items": items,
    }


def skipped_recovery_item(writeback: ActionWritebackRecord, reason: str) -> ActionWritebackRecoveryItem:
    return _base_recovery_item(writeback, decision="skipped", reason=reason)


def approval_required_recovery_item(writeback: ActionWritebackRecord, reason: str) -> ActionWritebackRecoveryItem:
    item = skipped_recovery_item(writeback, "operator_approval_required")
    item["approvalRequired"] = True
    item["approvalReason"] = reason
    return item


def failed_recovery_item(writeback: ActionWritebackRecord, exc: Exception) -> ActionWritebackRecoveryItem:
    return _base_recovery_item(writeback, decision="failed", reason=_failure_reason(exc))


def reconciled_recovery_item(
    writeback: ActionWritebackRecord,
    result: ActionWritebackReconciliationResult,
) -> ActionWritebackRecoveryItem:
    item = _base_recovery_item(writeback, decision="reconciled")
    item["remoteStatus"] = result["remoteStatus"]
    item["remoteResourceId"] = result["remoteResourceId"]
    return item


def reconciled_writeback_response(
    writeback: ActionWritebackRecord,
    remote_status: str,
    remote_resource_id: str,
    reconciled_at: str,
) -> dict[str, object]:
    response = dict(writeback.response or {})
    return {
        **response,
        "status_code": 200,
        "outcome_unknown": False,
        "reconciled": True,
        "remote_resource_id": remote_resource_id,
        "last_observed_status": remote_status,
        "reconciled_at": reconciled_at,
    }


def reconciled_result(
    action_run_id: str,
    writeback_id: str,
    remote_status: str,
    remote_resource_id: str,
    mutation: ActionApplyResponse,
) -> ActionWritebackReconciliationResult:
    result: ActionWritebackReconciliationResult = {
        "actionRunId": action_run_id,
        "writebackId": writeback_id,
        "status": "reconciled",
        "remoteStatus": remote_status,
        "remoteResourceId": remote_resource_id,
    }
    if "objectEditId" in mutation:
        result["objectEditId"] = mutation["objectEditId"]
    if "newObjectVersion" in mutation:
        result["newObjectVersion"] = mutation["newObjectVersion"]
    return result


def already_reconciled_result(
    writeback: ActionWritebackRecord,
    remote_status: str,
    remote_resource_id: str,
) -> ActionWritebackReconciliationResult:
    response = dict(writeback.response or {})
    return {
        "actionRunId": writeback.action_run_id,
        "writebackId": writeback.writeback_id,
        "status": "reconciled",
        "remoteStatus": str(response.get("last_observed_status") or remote_status),
        "remoteResourceId": str(response.get("remote_resource_id") or remote_resource_id),
        "alreadyReconciled": True,
    }


def action_run_has_sensitive_parameters(action_run: ActionRunRow, sensitive: set[str]) -> bool:
    return any(name in sensitive for name in action_run["parameters"])


def redact_mapping(value: Mapping[str, object] | None, sensitive: set[str]) -> Mapping[str, object] | None:
    if value is None:
        return None
    return {key: "***MASKED***" if key in sensitive else _redact_value(item, sensitive) for key, item in value.items()}


def _decision_count(items: list[ActionWritebackRecoveryItem], decision: str) -> int:
    return len([item for item in items if item["decision"] == decision])


def _base_recovery_item(
    writeback: ActionWritebackRecord,
    *,
    decision: str,
    reason: str | None = None,
) -> ActionWritebackRecoveryItem:
    item: ActionWritebackRecoveryItem = {
        "writebackId": writeback.writeback_id,
        "actionRunId": writeback.action_run_id,
        "status": writeback.status,
        "decision": decision,
    }
    if reason is not None:
        item["reason"] = reason
    return item


def _failure_reason(exc: Exception) -> str:
    if isinstance(exc, FoundryLiteError):
        return exc.code
    return type(exc).__name__


def _redact_value(value: object, sensitive: set[str]) -> object:
    if isinstance(value, Mapping):
        return redact_mapping(value, sensitive) or {}
    if isinstance(value, list):
        return [_redact_value(item, sensitive) for item in value]
    return value
