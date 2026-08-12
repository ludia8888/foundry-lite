"""Application port contract for runtime repository."""
# pyright: reportUnusedImport=false

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from foundry_lite.application.ports.runtime_repository_types import (  # noqa: F401
    AuditEventRecord,
    DeadLetterEventRecord,
    LineageEdgeRecord,
    LineageEdgeRow,
    OutboxEventRecord,
    RuntimeJsonObject,
    RuntimeLookupTable,
    RuntimeRetryMaterializationResult,
    RuntimeRetryPlan,
    RuntimeRetryResult,
    RuntimeRow,
    RuntimeRowsTable,
    RuntimeRunDetail,
    RuntimeRunInvestigation,
    RuntimeRunPageCursor,
    RuntimeRunQueryResult,
    RuntimeRunRelationRecord,
    RuntimeRunRelationRow,
    RuntimeRunSnapshot,
    RuntimeRunType,
)
from foundry_lite.application.ports.transaction_context import StatusTransition, TransactionContext
from foundry_lite.application.ports.workflow_adapter import WorkflowLedgerStatus, WorkflowRunRecord, WorkflowRunRow
from foundry_lite.application.runtime_repository_backup_restore import (  # noqa: F401
    BackupRestoreArtifact,
    BackupRestoreArtifactReceipt,
    BackupRestoreArtifactRestoredVersion,
    BackupRestoreArtifactRestoreReport,
    BackupRestoreArtifactRestoreStatus,
    BackupRestoreArtifactStatus,
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
    ObservabilityStoredReport,
    RuntimeRunLink,
    StoredObservabilityIncident,
    StoredObservabilityIncidentStatus,
)


class RuntimeRepository(Protocol):
    """DB boundary for runtime audit, outbox, lineage, and operational reads."""

    def lineage_for_resource(self, *, tenant_id: str, resource_id: str) -> list[LineageEdgeRow]:
        """Return lineage edges touching a resource for one tenant."""
        ...

    def catalog_lineage_for_resource(self, *, tenant_id: str, resource_id: str) -> list[LineageEdgeRow]:
        """Return lineage edges touching a catalog resource, folded to resource granularity.

        Edges are recorded per dataset version because that is what a build actually consumes
        and produces. A lineage graph is browsed at catalog granularity though -- "which
        datasets feed this one" -- so version edges are collapsed onto the resources that own
        them and deduplicated. Callers that need the per-version provenance behind an edge use
        `lineage_for_resource` instead.
        """
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

    def list_observability_incidents(
        self,
        *,
        tenant_id: str,
        status: StoredObservabilityIncidentStatus | None,
        limit: int,
    ) -> list[StoredObservabilityIncident]:
        """Return durable observability incidents for one tenant."""
        ...

    def observability_incident_by_id(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        incident_id: str,
    ) -> StoredObservabilityIncident | None:
        """Return one durable incident inside the caller transaction."""
        ...

    def upsert_observability_incident(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        incident: ObservabilityIncident,
    ) -> StoredObservabilityIncident:
        """Insert or update one active detector incident by tenant/dedupe key."""
        ...

    def update_observability_incident_status(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        incident_id: str,
        status: StoredObservabilityIncidentStatus,
        actor_user_id: str,
        updated_at: str,
        resolution_reason: str | None,
    ) -> StoredObservabilityIncident | None:
        """Move one durable incident through the acknowledged/resolved lifecycle."""
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
        self,
        *,
        transaction: TransactionContext,
        table: RuntimeRowsTable,
        tenant_id: str,
        event_types: Sequence[str] | None = None,
    ) -> list[RuntimeRow]:
        """Return rows by tenant from an allowlisted runtime table.

        ``event_types`` narrows the scan for tables that record an event type.
        Callers that only consume a few event types must pass it: ``audit_events``
        grows with every write in the tenant, so an unfiltered read is a
        full-table scan that gets slower with age.
        """
        ...

    def audit_event_for_resource(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        event_type: str,
        resource_type: str,
        resource_id: str,
    ) -> RuntimeRow | None:
        """Return the newest exact durable audit event for one tenant resource."""
        ...

    def audit_events_for_resources(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        resource_refs: Sequence[tuple[str, str]],
        event_types: Sequence[str],
        limit: int,
    ) -> list[RuntimeRow]:
        """Return a bounded newest-first audit window for exact tenant resources and event types."""
        ...

    def pending_outbox_events(self, *, transaction: TransactionContext, tenant_id: str, limit: int) -> list[RuntimeRow]:
        """Return bounded pending outbox rows in publish order."""
        ...

    def outbox_event_by_idempotency_key(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        idempotency_key: str,
    ) -> RuntimeRow | None:
        """Return one tenant-scoped durable mutation receipt by exact key."""
        ...

    def mark_outbox_event_publishing(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        event_id: str,
        transition: StatusTransition,
        claimed_at: str,
    ) -> RuntimeRow | None:
        """CAS a pending outbox row into the publishing claim state, recording the claim time."""
        ...

    def reclaim_stale_publishing_events(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        transition: StatusTransition,
        claimed_before: str,
    ) -> int:
        """Requeue outbox rows stuck in publishing whose claim predates the lease cutoff."""
        ...

    def mark_outbox_event_published(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        event_id: str,
        transition: StatusTransition,
        published_at: str,
        claimed_at: str,
    ) -> RuntimeRow | None:
        """CAS a publishing outbox row into the published terminal state.

        ``claimed_at`` fences the transition to the exact claim the caller made:
        if the row was reclaimed and re-claimed by another worker, the stored
        claim differs and this returns None instead of overwriting a live claim.
        """
        ...

    def mark_outbox_event_failed(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        event_id: str,
        transition: StatusTransition,
        claimed_at: str,
    ) -> RuntimeRow | None:
        """CAS a publishing outbox row into the failed terminal state, fenced by ``claimed_at``."""
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

    def claim_workflow_run_lease(
        self, *, transaction: TransactionContext, record: WorkflowRunRecord, lease_owner_id: str, now: str
    ) -> WorkflowRunRow | None: ...

    def release_workflow_run_lease(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        workflow_run_id: str,
        lease_owner_id: str,
        lease_token: str,
        status: WorkflowLedgerStatus,
        output: RuntimeJsonObject,
        error: RuntimeJsonObject | None,
        completed_at: str,
    ) -> WorkflowRunRow | None: ...

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
