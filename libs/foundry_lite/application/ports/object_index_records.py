"""Write-model records accepted by the object index repository port.

Split from ``object_index_repository`` so the Protocol module stays inside
the application module-size gate; the repository port re-exports these, so
callers keep importing them from either module.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypedDict

ObjectPropertyMap = Mapping[str, object]
ObjectPropertyVersions = dict[str, object]


class IndexRunSourceRef(TypedDict, total=False):
    """Source position captured when an object index run starts."""

    dataset_version_id: str
    replay_of_run_id: str
    ontologyReindexKey: str
    sourceOntologyVersionId: str
    changedFields: list[str]
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


@dataclass(frozen=True)
class IndexRunRecord:
    """One indexing run row as first persisted (status transitions happen via CAS)."""

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
    """A new object row for an index version, carrying base and edit layers."""

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
    """Base-layer refresh of an existing record from a newer dataset version."""

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
    """Tombstone for a record whose primary key vanished from the source."""

    record_id: str
    tenant_id: str
    source_dataset_version_id: str
    object_version: int
    deletion_reason: str
    updated_at: str


@dataclass(frozen=True)
class ObjectRecordCdcUpdate:
    """Version-guarded incremental update applied from an ordered CDC event."""

    record_id: str
    tenant_id: str
    expected_object_version: int
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
    """A base-refresh value colliding with a user edit, kept for operator review."""

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
    """A materialized link row between two indexed objects."""

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


@dataclass(frozen=True)
class ObjectLinkSourceDeletion:
    """Tombstone for a link whose join row vanished from the backing dataset."""

    link_id: str
    tenant_id: str
    source_dataset_version_id: str
    link_version: int
    deletion_reason: str
    updated_at: str
