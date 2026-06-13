from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, NotRequired, Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import TransactionContext

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
    "audit",
]
RuntimeJsonObject = Mapping[str, object]


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


RuntimeRow = Mapping[str, object]


class RuntimeRunLink(TypedDict):
    """Navigable link from evidence to an Operations run detail."""

    runType: RuntimeRunType
    runId: str
    relation: str
    resourceType: str
    resourceId: str
    status: str | None
    operationPath: str


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
    """Operational runtime rows grouped for the run-list screen/API."""

    syncRuns: list[RuntimeRow]
    transformRuns: list[RuntimeRow]
    indexRuns: list[RuntimeRow]
    actionRuns: list[RuntimeRow]
    actionWritebacks: list[RuntimeRow]
    materializationRuns: list[RuntimeRow]
    outboxEvents: list[RuntimeRow]
    deadLetterEvents: list[RuntimeRow]
    auditEvents: list[RuntimeRow]
    objectEdits: list[RuntimeRow]


class RuntimeRunPageCursor(TypedDict):
    """Decoded keyset cursor for one Operations run group."""

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
    lineageEdges: list[LineageEdgeRow]


class RuntimeRepository(Protocol):
    """DB boundary for runtime audit, outbox, lineage, and operational reads."""

    def lineage_for_resource(self, *, tenant_id: str, resource_id: str) -> list[LineageEdgeRow]:
        """Return lineage edges touching a resource for one tenant."""
        ...

    def list_runs(self, *, tenant_id: str) -> RuntimeRunSnapshot:
        """Return operational run, audit, and outbox rows for one tenant."""
        ...

    def query_run_rows(
        self,
        *,
        tenant_id: str,
        run_type: RuntimeRunType,
        status: str | None,
        since: str | None,
        until: str | None,
        cursor: RuntimeRunPageCursor | None,
        limit: int,
    ) -> list[RuntimeRow]:
        """Return one bounded, ordered Operations run page for one tenant."""
        ...

    def row_by_id(
        self, *, transaction: TransactionContext, table: RuntimeLookupTable, row_id: str
    ) -> RuntimeRow | None:
        """Return a row by id from a small allowlisted runtime lookup table."""
        ...

    def dead_letter_event_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, event_id: str
    ) -> RuntimeRow | None:
        """Return one tenant-scoped dead-letter event by id."""
        ...

    def rows_for_tenant(
        self, *, transaction: TransactionContext, table: RuntimeRowsTable, tenant_id: str
    ) -> list[RuntimeRow]:
        """Return rows by tenant from an allowlisted runtime table."""
        ...

    def update_outbox_event_for_retry(
        self, *, transaction: TransactionContext, tenant_id: str, event_id: str
    ) -> RuntimeRow | None:
        """Move an existing outbox event back to pending for retry."""
        ...

    def delete_dead_letter_event(self, *, transaction: TransactionContext, tenant_id: str, event_id: str) -> bool:
        """Delete a tenant-scoped dead-letter event after it is requeued."""
        ...

    def insert_audit_event(self, *, transaction: TransactionContext, record: AuditEventRecord) -> None:
        """Persist an audit event inside the caller transaction."""
        ...

    def insert_outbox_event(self, *, transaction: TransactionContext, record: OutboxEventRecord) -> bool:
        """Persist an outbox event, returning False for idempotent duplicates."""
        ...

    def insert_lineage_edge(self, *, transaction: TransactionContext, record: LineageEdgeRecord) -> None:
        """Persist a lineage edge inside the caller transaction."""
        ...
