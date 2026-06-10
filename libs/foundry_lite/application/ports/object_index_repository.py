from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from foundry_lite.application.ports.transaction_context import TransactionContext


@dataclass(frozen=True)
class IndexRunRecord:
    run_id: str
    tenant_id: str
    object_type_id: str
    object_type_api_name: str
    trigger_type: str
    source_ref: dict[str, Any]
    status: str
    cursor: dict[str, Any]
    rows_read: int
    objects_upserted: int
    objects_deleted: int
    links_upserted: int
    error: dict[str, Any] | None
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
    properties: dict[str, Any]
    base_properties: dict[str, Any]
    edit_properties: dict[str, Any]
    property_versions: dict[str, int]
    source_dataset_version_id: str
    source_hash: str
    object_version: int
    deleted: bool
    deletion_reason: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ObjectRecordSourceUpdate:
    record_id: str
    properties: dict[str, Any]
    base_properties: dict[str, Any]
    source_dataset_version_id: str
    source_hash: str
    object_version: int
    updated_at: str


@dataclass(frozen=True)
class ObjectConflictRecord:
    conflict_id: str
    tenant_id: str
    object_type_id: str
    object_id: str
    property_api_name: str
    source_value: Any
    edit_value: Any
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
    properties: dict[str, Any]
    source_dataset_version_id: str
    link_version: int
    deleted: bool
    deletion_reason: str | None
    updated_at: str


class ObjectIndexRepository(Protocol):
    """DB write boundary for object indexing runs, records, conflicts, and links."""

    def create_index_run(self, *, transaction: TransactionContext, record: IndexRunRecord) -> None:
        """Persist a running index run row."""
        ...

    def mark_index_run_succeeded(
        self,
        *,
        transaction: TransactionContext,
        run_id: str,
        rows_read: int,
        objects_upserted: int,
        links_upserted: int,
        cursor: dict[str, Any],
        completed_at: str,
    ) -> None:
        """Mark an index run as succeeded."""
        ...

    def mark_index_run_failed(
        self,
        *,
        transaction: TransactionContext,
        run_id: str,
        error: dict[str, Any],
        completed_at: str,
    ) -> None:
        """Mark an index run as failed."""
        ...

    def insert_object_record(self, *, transaction: TransactionContext, record: ObjectRecordInsert) -> None:
        """Insert a new object record."""
        ...

    def update_object_record_from_source(
        self,
        *,
        transaction: TransactionContext,
        record: ObjectRecordSourceUpdate,
    ) -> None:
        """Update source-owned object fields after a dataset re-index."""
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
    ) -> list[dict[str, Any]]:
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
    ) -> dict[str, Any] | None:
        """Return one object link by identity."""
        ...

    def refresh_object_link(
        self,
        *,
        transaction: TransactionContext,
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
