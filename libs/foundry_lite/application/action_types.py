"""Application-layer models and helpers for action types."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import NotRequired, TypedDict

from foundry_lite.application.ports.ontology_definitions import OntologyJsonObject


class ActionCatalogItem(TypedDict):
    """One permission-filtered canonical Action catalog entry."""

    apiName: str
    displayName: str
    description: str | None
    target: OntologyJsonObject
    ontologyVersionId: str
    contractVersion: int
    contractFingerprint: str
    parameterSchema: OntologyJsonObject
    contract: OntologyJsonObject
    riskLevel: str
    agentExecutionPolicy: str
    access: OntologyJsonObject
    enabled: bool


class ActionCatalogPage(TypedDict):
    """Cursor page of Action catalog entries."""

    items: list[ActionCatalogItem]
    nextCursor: str | None


class ActionMediaUploadResult(TypedDict):
    """Provisional immutable media reference returned for an Action parameter."""

    actionApiName: str
    parameter: str
    referenceKind: str
    reference: Mapping[str, object]
    mediaTransactionId: str
    uploadState: str
    isRetentionMarked: bool
    isIdempotentReplay: bool
    malwareScan: Mapping[str, object]


class ActionExecutionPlanResponse(TypedDict):
    """Immutable, hash-addressed preview of edits, effects, risk, and authorization."""

    actionApiName: str
    ontologyVersionId: str
    definitionFingerprint: str
    functionVersion: str | None
    target: Mapping[str, object]
    parameters: Mapping[str, object]
    editManifest: Mapping[str, object]
    diffs: list[Mapping[str, object]]
    effectManifest: list[Mapping[str, object]]
    risk: Mapping[str, object]
    authorization: Mapping[str, object]
    approval: Mapping[str, object]
    executionMode: str
    isDryRun: bool
    requestId: str
    createdAt: str
    planHash: str
    branchId: NotRequired[str]
    suppressedEffects: NotRequired[list[Mapping[str, object]]]


class ActionValidationIssue(TypedDict):
    """Machine-readable reason that an Action target or parameter is invalid."""

    code: str
    message: str
    details: NotRequired[Mapping[str, object]]


class ActionValidationParameterResult(TypedDict):
    """Validation result for one canonical Action parameter."""

    result: str
    required: bool
    issues: list[ActionValidationIssue]


class ActionValidationTargetResult(TypedDict):
    """OCC-aware validation result for the requested target object."""

    result: str
    objectType: str
    objectId: str
    expectedObjectVersion: int
    currentObjectVersion: int | None
    issues: list[ActionValidationIssue]


class ActionCriteriaEvaluation(TypedDict):
    """Redacted tree explaining a submission-criteria decision."""

    status: str
    reason: str | None
    tree: Mapping[str, object] | None


class ActionValidationResponse(TypedDict):
    """Complete non-mutating validation response for an Action request."""

    actionApiName: str
    result: str
    target: ActionValidationTargetResult
    parameters: dict[str, ActionValidationParameterResult]
    submissionCriteria: list[ActionValidationIssue]
    submissionCriteriaEvaluation: ActionCriteriaEvaluation | None


class ActionEditSummary(TypedDict):
    """Committed object edit evidence returned to an Action caller."""

    objectType: str
    objectId: str
    objectEditId: NotRequired[str]
    newObjectVersion: NotRequired[int]
    patch: Mapping[str, object]
    changedProperties: list[str]


class ActionCacheRefreshHint(TypedDict):
    """Typed client cache keys invalidated by an Action commit."""

    objectKeys: list[str]
    objectTypeKeys: list[str]
    actionRunKeys: list[str]


class ActionPlanEdit(TypedDict):
    """Compact object-operation row within an Action plan summary."""

    objectType: str
    objectId: str
    operation: str


class ActionPlanSummary(TypedDict):
    """The multi-object/link result of an Action IR v2 (rulesV2) submission."""

    editCount: int
    createdObjectIds: list[str]
    deletedObjectIds: list[str]
    linksCreated: int
    linksDeleted: int
    edits: list[ActionPlanEdit]


class ActionApplyResponse(TypedDict):
    """Synchronous Action commit result with durable run evidence."""

    actionRunId: str
    status: str
    target: Mapping[str, object]
    objectEditId: NotRequired[str]
    newObjectVersion: NotRequired[int]
    patch: NotRequired[Mapping[str, object]]
    validation: NotRequired[ActionValidationResponse]
    edits: NotRequired[ActionEditSummary]
    plan: NotRequired[ActionPlanSummary]
    cacheRefresh: NotRequired[ActionCacheRefreshHint]
    idempotentReplay: NotRequired[bool]


class ActionWritebackReconciliationResult(TypedDict):
    """Outcome of reconciling an ambiguous external writeback."""

    actionRunId: str
    writebackId: str
    status: str
    remoteStatus: str
    remoteResourceId: str
    objectEditId: NotRequired[str]
    newObjectVersion: NotRequired[int]
    alreadyReconciled: NotRequired[bool]


class ActionWritebackRecoveryItem(TypedDict):
    """One operator-visible recovery decision for an external writeback."""

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
    """Aggregate result of a bounded writeback recovery pass."""

    processed: int
    reconciled: int
    skipped: int
    failed: int
    items: list[ActionWritebackRecoveryItem]


class ActionWritebackQueueItem(TypedDict):
    """Durable writeback queue entry exposed for operator investigation."""

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
    """Operator-visible collection of pending writeback entries."""

    items: list[ActionWritebackQueueItem]


class ActionBatchTargetResult(TypedDict):
    """Committed evidence for one target in an atomic batch Action."""

    objectId: str
    objectEditId: str
    newObjectVersion: int
    patch: Mapping[str, object]


class ActionBatchApplyResponse(TypedDict):
    """All-or-nothing multi-target Action result."""

    actionRunId: str
    status: str
    objectType: str
    results: list[ActionBatchTargetResult]
    cacheRefresh: NotRequired[ActionCacheRefreshHint]
    idempotentReplay: NotRequired[bool]


@dataclass(frozen=True)
class ActionBatchTarget:
    """One parsed batch target: which object and which version the caller saw."""

    object_id: str
    expected_object_version: int


@dataclass(frozen=True)
class ActionBatchApplyCommand:
    """Normalized batch apply request; the fingerprint covers targets so replay detects body drift."""

    action_api_name: str
    object_type: str
    targets: tuple[ActionBatchTarget, ...]
    params: dict[str, object]
    idempotency_key: str
    request_fingerprint: str


@dataclass(frozen=True)
class ActionBatchApplyOutcome:
    """Internal success-or-deferred-error carrier for batch application."""

    response: ActionBatchApplyResponse | None = None
    deferred_error: Exception | None = None


@dataclass(frozen=True)
class ActionApplyCommand:
    """Normalized idempotent command consumed by the Action application service."""

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
    """Internal success-or-deferred-error carrier for one Action application."""

    response: ActionApplyResponse | None = None
    deferred_error: Exception | None = None
