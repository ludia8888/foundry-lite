"""Application port contract for runtime repository types."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict

RuntimeLookupTable = Literal["transforms", "materializations"]
RuntimeRowsTable = Literal[
    "sync_runs",
    "transform_runs",
    "index_runs",
    "action_runs",
    "action_writebacks",
    "materialization_runs",
    "outbox_events",
    "dead_letter_events",
    "workflow_runs",
    "ai_execution_runs",
    "audit_events",
    "object_edits",
    "object_records",
]
RuntimeRunType = Literal[
    "sync",
    "transform",
    "index",
    "action",
    "action_writeback",
    "materialization",
    "outbox",
    "dead_letter",
    "workflow",
    "insight_review",
    "ai",
    "audit",
]
RuntimeJsonObject = Mapping[str, object]
RuntimeRow = Mapping[str, object]


@dataclass(frozen=True)
class AuditEventRecord:
    event_id: str
    tenant_id: str
    actor_user_id: str
    event_type: str
    resource_type: str
    resource_id: str | None
    action: str
    decision: str
    policy_decision: RuntimeJsonObject
    before_ref: RuntimeJsonObject
    after_ref: RuntimeJsonObject
    correlation_id: str
    request_id: str
    metadata: RuntimeJsonObject
    created_at: str


@dataclass(frozen=True)
class OutboxEventRecord:
    event_id: str
    tenant_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: RuntimeJsonObject
    status: str
    attempts: int
    idempotency_key: str
    correlation_id: str
    created_at: str
    published_at: str | None


@dataclass(frozen=True)
class DeadLetterEventRecord:
    event_id: str
    tenant_id: str
    source_event_id: str | None
    event_type: str
    payload: RuntimeJsonObject
    error: RuntimeJsonObject
    failed_at: str
    retry_after: str | None


@dataclass(frozen=True)
class LineageEdgeRecord:
    edge_id: str
    tenant_id: str
    from_resource_type: str
    from_resource_id: str
    to_resource_type: str
    to_resource_id: str
    relation: str
    created_by_run_id: str
    created_at: str


class LineageEdgeRow(TypedDict):
    """Persisted lineage edge row returned by runtime read APIs."""

    id: str
    tenant_id: str
    from_resource_type: str
    from_resource_id: str
    to_resource_type: str
    to_resource_id: str
    relation: str
    created_by_run_id: str
    created_at: str


@dataclass(frozen=True)
class RuntimeRunRelationRecord:
    relation_id: str
    tenant_id: str
    source_run_type: RuntimeRunType
    source_run_id: str
    target_run_type: RuntimeRunType
    target_run_id: str
    relation: str
    resource_type: str
    resource_id: str
    metadata: RuntimeJsonObject
    created_at: str


class RuntimeRunRelationRow(TypedDict):
    """Durable operator relation between two Operations rows."""

    id: str
    tenant_id: str
    source_run_type: RuntimeRunType
    source_run_id: str
    target_run_type: RuntimeRunType
    target_run_id: str
    relation: str
    resource_type: str
    resource_id: str
    metadata: RuntimeJsonObject
    created_at: str


class RuntimeRelatedCounts(TypedDict):
    """Operator-facing counts for evidence attached to one runtime row."""

    outboxEvents: int
    auditEvents: int
    objectEdits: int
    actionWritebacks: int
    lineageEdges: int


class RuntimeRunInvestigation(TypedDict):
    """Human-readable investigation summary for one runtime row."""

    summary: str
    status: str | None
    errorMessage: str | None
    correlationId: str | None
    references: RuntimeJsonObject
    relatedCounts: RuntimeRelatedCounts
    suggestedActions: list[str]


class RuntimeRetryMaterializationResult(TypedDict):
    """Materialization output created while reprocessing a retryable DLQ event."""

    kind: Literal["materialization"]
    apiName: str
    datasetId: str
    datasetRef: str
    transactionId: str
    versionId: str
    versionNumber: int
    rowCount: int


class RuntimeRunSnapshot(TypedDict):
    syncRuns: list[RuntimeRow]
    transformRuns: list[RuntimeRow]
    indexRuns: list[RuntimeRow]
    actionRuns: list[RuntimeRow]
    actionWritebacks: list[RuntimeRow]
    materializationRuns: list[RuntimeRow]
    outboxEvents: list[RuntimeRow]
    deadLetterEvents: list[RuntimeRow]
    workflowRuns: list[RuntimeRow]
    aiRuns: list[RuntimeRow]
    auditEvents: list[RuntimeRow]
    objectEdits: list[RuntimeRow]


class RuntimeRunPageCursor(TypedDict):
    timestamp: str
    run_id: str


class RuntimeRunQueryResult(RuntimeRunSnapshot):
    """Paged operational runtime rows grouped for the run-list screen/API."""

    nextCursor: NotRequired[str | None]
    nextCursors: NotRequired[dict[RuntimeRunType, str]]


class RuntimeRetryPlan(TypedDict):
    """Validated DLQ retry data read before changing runtime state."""

    deadLetterEventId: str
    outboxEventId: str
    eventType: str
    payload: RuntimeJsonObject


class RuntimeRetryResult(RuntimeRetryPlan):
    """Result returned when an operator requeues a dead-lettered event."""

    status: str
    materializationResult: NotRequired[RuntimeRetryMaterializationResult]


class RuntimeRunDetail(TypedDict):
    """Operator-facing detail payload for one runtime row."""

    runType: RuntimeRunType
    runId: str
    row: RuntimeRow
    status: str | None
    errorMessage: str | None
    error: object | None
    correlationId: str | None
    references: RuntimeJsonObject
    investigation: RuntimeRunInvestigation
    relatedOutboxEvents: list[RuntimeRow]
    relatedAuditEvents: list[RuntimeRow]
    relatedObjectEdits: list[RuntimeRow]
    relatedActionWritebacks: list[RuntimeRow]
    runRelations: list[RuntimeRunRelationRow]
    lineageEdges: list[LineageEdgeRow]
    datasetTransaction: NotRequired[RuntimeRow | None]
    sourceEvidence: NotRequired[RuntimeJsonObject | None]
    lateData: NotRequired[RuntimeJsonObject | None]
    materialization: NotRequired[RuntimeJsonObject | None]
    downstreamImpact: NotRequired[RuntimeJsonObject | None]
    quality: NotRequired[RuntimeJsonObject | None]
    ai: NotRequired[RuntimeJsonObject | None]
