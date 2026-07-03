"""Application port contract for object index repository."""

from __future__ import annotations

from typing import Protocol, TypedDict

from foundry_lite.application.ports.object_index_records import (
    IndexRunCursor,
    IndexRunError,
    IndexRunRecord,
    IndexRunSourceRef,
    ObjectConflictRecord,
    ObjectLinkInsert,
    ObjectLinkSourceDeletion,
    ObjectPropertyMap,
    ObjectPropertyVersions,
    ObjectRecordCdcUpdate,
    ObjectRecordInsert,
    ObjectRecordSourceDeletion,
    ObjectRecordSourceUpdate,
)
from foundry_lite.application.ports.object_read_repository import ObjectRecordRow
from foundry_lite.application.ports.ontology_repository import LinkTypeRow
from foundry_lite.application.ports.transaction_context import TransactionContext

__all__ = [
    "IndexRunCursor",
    "IndexRunError",
    "IndexRunRecord",
    "IndexRunSourceRef",
    "IndexRunUsageRow",
    "IndexRunRow",
    "ObjectConflictRecord",
    "ObjectIndexCdcResult",
    "ObjectIndexLinkRow",
    "ObjectIndexRebuildResult",
    "ObjectIndexRepository",
    "ObjectIndexShadowRebuildResult",
    "ObjectIndexValidationResult",
    "ObjectLinkInsert",
    "ObjectLinkSourceDeletion",
    "ObjectPropertyMap",
    "ObjectPropertyVersions",
    "ObjectRecordCdcUpdate",
    "ObjectRecordInsert",
    "ObjectRecordSourceDeletion",
    "ObjectRecordSourceUpdate",
    "OntologyObjectReindexResult",
]


class ObjectIndexLinkRow(TypedDict):
    """Persisted object link row."""

    id: str
    tenant_id: str
    link_type_id: str
    link_type_api_name: str
    index_version: str
    is_active: bool
    from_object_type_id: str
    from_api_name: str
    from_object_id: str
    to_object_type_id: str
    to_api_name: str
    to_object_id: str
    properties: ObjectPropertyMap
    source_dataset_version_id: str
    link_version: int
    deleted: bool
    deletion_reason: str | None
    updated_at: str


class IndexRunUsageRow(TypedDict):
    """Aggregated index-run activity for one object type and usage window.

    Usage read models need counts and recency, not row payloads, so the
    aggregation lives at the port where each adapter can push it into SQL.
    """

    status_counts: dict[str, int]
    total_runs: int
    last_run_at: str | None
    last_succeeded_at: str | None


class IndexRunRow(TypedDict):
    """Persisted index run row used by operations replay workflows."""

    id: str
    tenant_id: str
    object_type_id: str
    object_type_api_name: str
    trigger_type: str
    source_ref: IndexRunSourceRef
    status: str
    cursor: IndexRunCursor
    rows_read: int
    objects_upserted: int
    objects_deleted: int
    links_upserted: int
    error: IndexRunError | None
    started_at: str | None
    completed_at: str | None
    created_at: str


class ObjectIndexRebuildResult(TypedDict):
    """Public result returned after rebuilding an object index."""

    index_run_id: str
    object_type: str
    rows_read: int
    objects_upserted: int
    objects_deleted: int
    links_upserted: int


class ObjectIndexValidationResult(TypedDict):
    """Count/hash validation proof for a shadow object index."""

    expectedCount: int
    actualCount: int
    expectedHash: str
    actualHash: str


class ObjectIndexShadowRebuildResult(TypedDict):
    """Public result returned after building and promoting a shadow index."""

    index_run_id: str
    object_type: str
    indexVersion: str
    previousIndexVersion: str
    rows_read: int
    objects_upserted: int
    objects_deleted: int
    links_upserted: int
    is_switched: bool
    validation: ObjectIndexValidationResult


class ObjectIndexCdcResult(TypedDict):
    """Public result returned after applying CDC events to object records."""

    index_run_id: str
    object_type: str
    rows_read: int
    objects_upserted: int
    objects_deleted: int
    events_skipped: int


class OntologyObjectReindexResult(TypedDict):
    """Public result for executing an ontology object-reindex plan."""

    index_run_id: str
    object_type: str
    rows_read: int
    objects_upserted: int
    objects_deleted: int
    links_upserted: int
    ontologyReindexKey: str
    servingContractStatus: str
    isIdempotentReplay: bool
    sourceOntologyVersionId: str | None
    changedFields: list[str]


class ObjectIndexRepository(Protocol):
    """DB write boundary for object indexing runs, records, conflicts, and links."""

    def index_run_by_id(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
    ) -> IndexRunRow | None:
        """Return one index run row for operations replay, or None."""
        ...

    def create_index_run(self, *, transaction: TransactionContext, record: IndexRunRecord) -> None:
        """Persist a running index run row."""
        ...

    def index_run_usage(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_api_name: str,
        since: str,
    ) -> IndexRunUsageRow:
        """Aggregate index runs created at or after ``since`` for one object type.

        Feeds the ontology object-type usage read model: run counts by status
        plus last-run/last-success recency without loading run payloads.
        """
        ...

    def active_index_version(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_id: str,
    ) -> str:
        """Return the currently serving object index version for one object type."""
        ...

    def mark_index_run_succeeded(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
        rows_read: int,
        objects_upserted: int,
        objects_deleted: int,
        links_upserted: int,
        cursor: IndexRunCursor,
        completed_at: str,
    ) -> bool:
        """CAS a running index run into succeeded."""
        ...

    def mark_index_run_failed(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
        error: IndexRunError,
        completed_at: str,
    ) -> bool:
        """CAS a running index run into failed."""
        ...

    def insert_object_record(self, *, transaction: TransactionContext, record: ObjectRecordInsert) -> None:
        """Insert a new object record."""
        ...

    def object_record_in_index(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_id: str,
        object_type_api_name: str,
        object_id: str,
        index_version: str,
    ) -> ObjectRecordRow | None:
        """Return one object record from a specific index version."""
        ...

    def object_records_for_index_version(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_id: str,
        index_version: str,
    ) -> list[ObjectRecordRow]:
        """Return all object rows for count/hash validation of one index version."""
        ...

    def update_object_record_from_source(
        self,
        *,
        transaction: TransactionContext,
        record: ObjectRecordSourceUpdate,
    ) -> None:
        """Update source-owned object fields after a dataset re-index."""
        ...

    def mark_object_record_deleted_from_source(
        self,
        *,
        transaction: TransactionContext,
        record: ObjectRecordSourceDeletion,
    ) -> None:
        """Tombstone an object that disappeared from a full source snapshot."""
        ...

    def update_object_record_from_cdc(
        self,
        *,
        transaction: TransactionContext,
        record: ObjectRecordCdcUpdate,
    ) -> bool:
        """CAS-apply one ordered CDC source patch or tombstone to an object record."""
        ...

    def insert_object_conflict(self, *, transaction: TransactionContext, record: ObjectConflictRecord) -> None:
        """Persist a source-vs-edit conflict detected during re-index."""
        ...

    def link_types_for_object_type(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        ontology_version_id: str,
        from_object_type_id: str,
    ) -> list[LinkTypeRow]:
        """Return link types emitted by one source object type in an active ontology version."""
        ...

    def object_link(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        link_type_id: str,
        from_object_id: str,
        to_object_id: str,
        index_version: str,
    ) -> ObjectIndexLinkRow | None:
        """Return one object link by identity."""
        ...

    def refresh_object_link(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        link_id: str,
        link_version: int,
        source_dataset_version_id: str,
        updated_at: str,
    ) -> None:
        """Refresh an existing object link and undelete it."""
        ...

    def insert_object_link(self, *, transaction: TransactionContext, record: ObjectLinkInsert) -> None:
        """Insert a new object link."""
        ...

    def object_links_for_index_version(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        from_object_type_id: str,
        index_version: str,
    ) -> list[ObjectIndexLinkRow]:
        """Return all links emitted from one object type and index version."""
        ...

    def mark_object_link_deleted_from_source(
        self,
        *,
        transaction: TransactionContext,
        record: ObjectLinkSourceDeletion,
    ) -> None:
        """Tombstone a link that disappeared from a full source snapshot."""
        ...

    def switch_active_index_version(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_id: str,
        index_version: str,
        updated_at: str,
        expected_previous_index_version: str | None = None,
    ) -> bool:
        """Atomically switch one object type to a validated shadow index version.

        When ``expected_previous_index_version`` is provided, the switch behaves
        like a compare-and-swap promotion: it succeeds only if the object type's
        active index pointer still matches the version that was validated.
        """
        ...

    def delete_inactive_index_version(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_id: str,
        index_version: str,
    ) -> None:
        """Delete one non-serving index version after its retention period."""
        ...
