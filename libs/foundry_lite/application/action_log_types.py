"""Typed records for normalized Action logs and reversible edit evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import NotRequired, TypedDict

ACTION_LOG_PROPERTY_TYPES = {
    "actionRunId": "string",
    "logEntryId": "string",
    "definitionVersion": "string",
    "actorUserId": "string",
    "status": "string",
    "parameters": "struct",
    "result": "struct",
    "branchId": "string",
    "planHash": "string",
    "approvalId": "string",
    "revertAllowed": "boolean",
    "revertStatus": "string",
    "revertedByRunId": "string",
    "effectReceiptCount": "integer",
    "editedObjectCount": "integer",
    "editedObjects": "array",
    "createdAt": "timestamp",
    "completedAt": "timestamp",
}


class ActionLogEntryRow(TypedDict):
    """One immutable Action submission log with mutable revert disposition."""

    id: str
    tenant_id: str
    action_run_id: str
    log_object_type_api_name: str
    log_object_id: str
    action_type_id: str
    action_type_api_name: str
    definition_version: str
    actor_user_id: str
    status: str
    parameters: Mapping[str, object]
    result: Mapping[str, object]
    branch_id: str | None
    plan_hash: str | None
    approval_id: str | None
    revert_allowed: bool
    revert_status: str
    reverted_by_run_id: str | None
    created_at: str
    completed_at: str


class ActionLogObjectRow(TypedDict):
    """One object or link edit connected to an Action log entry."""

    id: str
    tenant_id: str
    action_log_entry_id: str
    object_edit_id: str
    object_type_id: str
    object_type_api_name: str
    object_id: str
    edit_type: str
    ordinal: int


class ActionRevertEligibility(TypedDict):
    """Public explanation of whether an Action run can be atomically reverted."""

    actionRunId: str
    isEligible: bool
    reason: str | None
    editCount: int
    hasPreservedExternalEffects: bool
    compensationAction: NotRequired[str]
    logEntryId: NotRequired[str]


@dataclass(frozen=True, slots=True)
class ActionLogEntryRecord:
    """Insert values for one normalized Action log entry."""

    log_entry_id: str
    tenant_id: str
    action_run_id: str
    log_object_type_api_name: str
    log_object_id: str
    action_type_id: str
    action_type_api_name: str
    definition_version: str
    actor_user_id: str
    status: str
    parameters: Mapping[str, object]
    result: Mapping[str, object]
    branch_id: str | None
    plan_hash: str | None
    approval_id: str | None
    revert_allowed: bool
    created_at: str
    completed_at: str


@dataclass(frozen=True, slots=True)
class ActionLogObjectRecord:
    """Insert values connecting one committed edit to its Action log."""

    log_object_link_id: str
    tenant_id: str
    action_log_entry_id: str
    object_edit_id: str
    object_type_id: str
    object_type_api_name: str
    object_id: str
    edit_type: str
    ordinal: int


@dataclass(frozen=True, slots=True)
class ObjectRestoreWrite:
    """CAS request that restores one soft-deleted object during revert."""

    object_record_id: str
    tenant_id: str
    expected_object_version: int
    updated_at: str
