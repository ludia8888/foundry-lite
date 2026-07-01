"""Application-layer models and helpers for action types."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import NotRequired, TypedDict


class ActionValidationIssue(TypedDict):
    code: str
    message: str
    details: NotRequired[Mapping[str, object]]


class ActionValidationParameterResult(TypedDict):
    result: str
    required: bool
    issues: list[ActionValidationIssue]


class ActionValidationTargetResult(TypedDict):
    result: str
    objectType: str
    objectId: str
    expectedObjectVersion: int
    currentObjectVersion: int | None
    issues: list[ActionValidationIssue]


class ActionValidationResponse(TypedDict):
    actionApiName: str
    result: str
    target: ActionValidationTargetResult
    parameters: dict[str, ActionValidationParameterResult]
    submissionCriteria: list[ActionValidationIssue]


class ActionEditSummary(TypedDict):
    objectType: str
    objectId: str
    objectEditId: NotRequired[str]
    newObjectVersion: NotRequired[int]
    patch: Mapping[str, object]
    changedProperties: list[str]


class ActionCacheRefreshHint(TypedDict):
    objectKeys: list[str]
    objectTypeKeys: list[str]
    actionRunKeys: list[str]


class ActionApplyResponse(TypedDict):
    actionRunId: str
    status: str
    target: Mapping[str, object]
    objectEditId: NotRequired[str]
    newObjectVersion: NotRequired[int]
    patch: NotRequired[Mapping[str, object]]
    validation: NotRequired[ActionValidationResponse]
    edits: NotRequired[ActionEditSummary]
    cacheRefresh: NotRequired[ActionCacheRefreshHint]
    idempotentReplay: NotRequired[bool]


class ActionWritebackReconciliationResult(TypedDict):
    actionRunId: str
    writebackId: str
    status: str
    remoteStatus: str
    remoteResourceId: str
    objectEditId: NotRequired[str]
    newObjectVersion: NotRequired[int]
    alreadyReconciled: NotRequired[bool]


class ActionWritebackRecoveryItem(TypedDict):
    writebackId: str
    actionRunId: str
    status: str
    decision: str
    reason: NotRequired[str]
    approvalId: NotRequired[str]
    approvalRequired: NotRequired[bool]
    approvalReason: NotRequired[str]
    remoteStatus: NotRequired[str]
    remoteResourceId: NotRequired[str]


class ActionWritebackRecoveryResult(TypedDict):
    processed: int
    reconciled: int
    skipped: int
    failed: int
    items: list[ActionWritebackRecoveryItem]


class ActionWritebackQueueItem(TypedDict):
    writebackId: str
    actionRunId: str
    status: str
    mode: str
    connectorId: str
    idempotencyKey: str
    attempts: int
    createdAt: str
    completedAt: str | None
    reconciliationDeadline: object
    remoteResourceId: object
    lastObservedStatus: object
    compensationActionType: object
    request: Mapping[str, object]
    response: Mapping[str, object] | None


class ActionWritebackQueueResult(TypedDict):
    items: list[ActionWritebackQueueItem]


@dataclass(frozen=True)
class ActionApplyCommand:
    action_api_name: str
    object_type: str
    object_id: str
    expected_object_version: int
    params: dict[str, object]
    idempotency_key: str
    request_fingerprint: str
    simulate_writeback_failure: bool
    simulate_writeback_retryable: bool
    simulate_writeback_outcome_unknown: bool
    simulate_writeback_compensation_required: bool
    external_writeback_uri: str | None = None


@dataclass(frozen=True)
class ActionApplyOutcome:
    response: ActionApplyResponse | None = None
    deferred_error: Exception | None = None
