# pyright: reportUnusedImport=false

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, NotRequired, Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import StatusTransition, TransactionContext
from foundry_lite.application.ports.workflow_adapter import WorkflowRunRecord, WorkflowRunRow
from foundry_lite.application.runtime_repository_backup_restore import (  # noqa: F401
    BackupRestoreDatasetVersion,
    BackupRestoreIndexPointer,
    BackupRestoreManifestIssue,
    BackupRestoreModeReport,
    BackupRestoreModeStatus,
    BackupRestorePostRestoreValidationReport,
    BackupRestorePostRestoreValidationSummary,
    BackupRestorePreflightReport,
    BackupRestorePreflightStatus,
    BackupRestorePreflightSummary,
    BackupRestoreRecoveryOverview,
    BackupRestoreVersionStatus,
)
from foundry_lite.application.runtime_repository_observability import (  # noqa: F401
    ObservabilityDetectorConfig,
    ObservabilityDetectorType,
    ObservabilityIncident,
    ObservabilityIncidentStatus,
    ObservabilityReport,
    ObservabilitySeverity,
    RuntimeRunLink,
)

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


RuntimeRow = Mapping[str, object]


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
    lateData: NotRequired[RuntimeJsonObject | None]
    materialization: NotRequired[RuntimeJsonObject | None]
    downstreamImpact: NotRequired[RuntimeJsonObject | None]
    quality: NotRequired[RuntimeJsonObject | None]
    ai: NotRequired[RuntimeJsonObject | None]


class RuntimeRepository(Protocol):
    """DB boundary for runtime audit, outbox, lineage, and operational reads."""

    def lineage_for_resource(self, *, tenant_id: str, resource_id: str) -> list[LineageEdgeRow]:
        """Return lineage edges touching a resource for one tenant."""
        ...

    def list_runs(self, *, tenant_id: str, limit: int | None = None) -> RuntimeRunSnapshot:
        """Return operational run, audit, and outbox rows for one tenant.

        When ``limit`` is set, each table is bounded to its most recent ``limit``
        rows (a recency window); ``None`` returns the full per-tenant history.
        """
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

    def runs_for_source_chain(
        self,
        *,
        tenant_id: str,
        object_type_api_name: str,
        resource_ids: Sequence[str],
        run_ids: Sequence[str],
        limit: int,
    ) -> RuntimeRunSnapshot:
        """Return a bounded snapshot scoped to one source's index/producer/lineage runs.

        Index runs are scoped by object type, producer runs (sync/transform/
        materialization) by the resources they produce, and any run referenced by
        ``run_ids`` is included so lineage edges resolve. Other tables are empty.
        """
        ...

    def run_row(self, *, tenant_id: str, run_type: RuntimeRunType, run_id: str) -> RuntimeRow | None:
        """Return one tenant-scoped run row by type and id without loading the snapshot."""
        ...

    def run_row_any_type(self, *, tenant_id: str, run_id: str) -> tuple[RuntimeRunType, RuntimeRow] | None:
        """Resolve a run id to its type and row across lineage-producing run tables."""
        ...

    def related_evidence_rows(
        self, *, tenant_id: str, table: RuntimeRowsTable, relation_ids: Sequence[str], limit: int
    ) -> list[RuntimeRow]:
        """Return bounded evidence rows whose relation columns match any correlation id."""
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

    def pending_outbox_events(self, *, transaction: TransactionContext, tenant_id: str, limit: int) -> list[RuntimeRow]:
        """Return bounded pending outbox rows in publish order."""
        ...

    def mark_outbox_event_publishing(
        self, *, transaction: TransactionContext, tenant_id: str, event_id: str, transition: StatusTransition
    ) -> RuntimeRow | None:
        """CAS a pending outbox row into the publishing claim state."""
        ...

    def mark_outbox_event_published(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        event_id: str,
        transition: StatusTransition,
        published_at: str,
    ) -> RuntimeRow | None:
        """CAS a publishing outbox row into the published terminal state."""
        ...

    def mark_outbox_event_failed(
        self, *, transaction: TransactionContext, tenant_id: str, event_id: str, transition: StatusTransition
    ) -> RuntimeRow | None:
        """CAS a publishing outbox row into the failed terminal state."""
        ...

    def update_outbox_event_for_retry(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        event_id: str,
        transition: StatusTransition,
    ) -> RuntimeRow | None:
        """CAS an outbox event back to pending for retry."""
        ...

    def workflow_run_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, workflow_run_id: str
    ) -> WorkflowRunRow | None:
        """Return one tenant-scoped workflow run ledger row."""
        ...

    def workflow_run_by_idempotency(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        workflow_name: str,
        idempotency_key: str,
    ) -> WorkflowRunRow | None:
        """Return the tenant/workflow/idempotency winner row."""
        ...

    def insert_workflow_run_or_get_existing(
        self, *, transaction: TransactionContext, record: WorkflowRunRecord
    ) -> WorkflowRunRow | None:
        """Insert a workflow intent, returning the existing winner on conflict."""
        ...

    def update_workflow_run_status(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        workflow_run_id: str,
        transition: StatusTransition,
        output: RuntimeJsonObject,
        error: RuntimeJsonObject | None,
        started_at: str | None,
        completed_at: str | None,
    ) -> WorkflowRunRow | None:
        """CAS a workflow run status and return the updated row."""
        ...

    def link_workflow_audit_event(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        workflow_run_id: str,
        audit_event_id: str,
    ) -> WorkflowRunRow | None:
        """Attach a workflow start audit event to the workflow ledger row."""
        ...

    def delete_dead_letter_event(self, *, transaction: TransactionContext, tenant_id: str, event_id: str) -> bool:
        """Delete a tenant-scoped dead-letter event after it is requeued."""
        ...

    def delete_object_record(self, *, transaction: TransactionContext, tenant_id: str, record_id: str) -> bool:
        """Tombstone a tenant-scoped object record for erasure; idempotent no-op on replay."""
        ...

    def insert_audit_event(self, *, transaction: TransactionContext, record: AuditEventRecord) -> None:
        """Persist an audit event inside the caller transaction."""
        ...

    def insert_outbox_event(self, *, transaction: TransactionContext, record: OutboxEventRecord) -> bool:
        """Persist an outbox event, returning False for idempotent duplicates."""
        ...

    def insert_dead_letter_event(self, *, transaction: TransactionContext, record: DeadLetterEventRecord) -> None:
        """Persist a failed outbox publish event for operator retry."""
        ...

    def insert_lineage_edge(self, *, transaction: TransactionContext, record: LineageEdgeRecord) -> None:
        """Persist a lineage edge inside the caller transaction."""
        ...

    def insert_run_relation(self, *, transaction: TransactionContext, record: RuntimeRunRelationRecord) -> bool:
        """Persist one durable Operations run relation, returning False for idempotent duplicates."""
        ...

    def run_relations_for_run(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_type: RuntimeRunType,
        run_id: str,
    ) -> list[RuntimeRunRelationRow]: ...
