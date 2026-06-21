from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, TypedDict

from foundry_lite.application.ports.object_read_repository import ObjectRecordRow
from foundry_lite.application.ports.ontology_repository import LinkTypeRow
from foundry_lite.application.ports.transaction_context import TransactionContext

ObjectPropertyMap = Mapping[str, object]
ObjectPropertyVersions = dict[str, object]


class IndexRunSourceRef(TypedDict, total=False):
    """Source position captured when an object index run starts."""

    dataset_version_id: str
    replay_of_run_id: str
    cdc_dataset: str
    event_count: int
    mode: str
    index_version: str
    baseline_count: int
    baseline_hash: str
    adapter_profile: str
    object_id: str
    request_id: str
    resource_id: str


class IndexRunCursor(TypedDict, total=False):
    """Progress cursor captured when an object index run finishes."""

    last_row: int
    last_event_id: str | None
    last_ordering: Mapping[str, object]
    events_skipped: int
    lateDataStatusCounts: Mapping[str, int]
    lateEventIds: list[str]
    maxEventTimeLagSeconds: int | None


class IndexRunError(TypedDict, total=False):
    """Normalized error payload stored on failed index runs."""

    type: str
    message: str
    details: Mapping[str, object]
    adapterFailure: Mapping[str, object]
    trace: Mapping[str, str]


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


@dataclass(frozen=True)
class IndexRunRecord:
    run_id: str
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
    started_at: str
    completed_at: str | None
    created_at: str


@dataclass(frozen=True)
class ObjectRecordInsert:
    record_id: str
    tenant_id: str
    object_type_id: str
    object_type_api_name: str
    object_id: str
    properties: ObjectPropertyMap
    base_properties: ObjectPropertyMap
    edit_properties: ObjectPropertyMap
    property_versions: ObjectPropertyVersions
    source_dataset_version_id: str
    source_hash: str
    object_version: int
    deleted: bool
    deletion_reason: str | None
    created_at: str
    updated_at: str
    index_version: str = "active"
    is_active: bool = True


@dataclass(frozen=True)
class ObjectRecordSourceUpdate:
    record_id: str
    tenant_id: str
    properties: ObjectPropertyMap
    base_properties: ObjectPropertyMap
    source_dataset_version_id: str
    source_hash: str
    object_version: int
    updated_at: str


@dataclass(frozen=True)
class ObjectRecordSourceDeletion:
    record_id: str
    tenant_id: str
    source_dataset_version_id: str
    object_version: int
    deletion_reason: str
    updated_at: str


@dataclass(frozen=True)
class ObjectRecordCdcUpdate:
    record_id: str
    tenant_id: str
    properties: ObjectPropertyMap
    base_properties: ObjectPropertyMap
    property_versions: ObjectPropertyVersions
    source_dataset_version_id: str
    source_hash: str
    object_version: int
    deleted: bool
    deletion_reason: str | None
    updated_at: str


@dataclass(frozen=True)
class ObjectConflictRecord:
    conflict_id: str
    tenant_id: str
    object_type_id: str
    object_id: str
    property_api_name: str
    source_value: object
    edit_value: object
    source_dataset_version_id: str
    edit_id: str | None
    status: str
    created_at: str


@dataclass(frozen=True)
class ObjectLinkInsert:
    link_id: str
    tenant_id: str
    link_type_id: str
    link_type_api_name: str
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
    index_version: str = "active"
    is_active: bool = True


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
    ) -> None:
        """Mark an index run as succeeded."""
        ...

    def mark_index_run_failed(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
        error: IndexRunError,
        completed_at: str,
    ) -> None:
        """Mark an index run as failed."""
        ...

    def insert_object_record(self, *, transaction: TransactionContext, record: ObjectRecordInsert) -> None:
        """Insert a new object record."""
        ...

    def object_record_in_index(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
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
    ) -> None:
        """Apply one ordered CDC source patch or tombstone to an object record."""
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
