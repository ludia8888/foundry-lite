"""Application service helpers for indexing types workflows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from foundry_lite.application.ports import (
    DatasetVersionRow,
    ObjectIndexCdcResult,
    ObjectIndexRebuildResult,
    ObjectIndexShadowRebuildResult,
    ObjectIndexValidationResult,
    ObjectPropertyMap,
    ObjectRecordRow,
    ObjectTypeRow,
)


@dataclass(frozen=True)
class ObjectIndexSourceSegment:
    """One datasource segment resolved to a concrete dataset version for a rebuild."""

    name: str
    dataset_ref: str
    primary_key_column: str
    # Columns mapped by properties assigned to THIS segment: the merge always
    # takes their values from this segment's row (null when the PK is absent
    # here), even if another segment's dataset happens to share a column name.
    property_columns: tuple[str, ...]
    dataset_version: DatasetVersionRow


@dataclass(frozen=True)
class ObjectIndexMultiSourcePlan:
    """How a multi-datasource rebuild merges per-segment rows into one snapshot."""

    primary_key_column: str
    segments: tuple[ObjectIndexSourceSegment, ...]


@dataclass(frozen=True)
class ObjectIndexLinkSource:
    """One M:N join datasource pinned to the version used by a rebuild."""

    dataset_ref: str
    dataset_version: DatasetVersionRow


@dataclass(frozen=True)
class ObjectIndexRebuildPlan:
    run_id: str
    object_type_api_name: str
    object_type: ObjectTypeRow
    dataset_version: DatasetVersionRow
    source_dataset_version_id: str
    mode: str
    index_version: str
    previous_index_version: str
    # Changelog-hash diffing is only safe for plain dataset reindexes; the plan
    # carries its trigger so the rebuild can force full passes for shadow,
    # ontology-migration, and failed-run-replay flows that change index shape.
    trigger_type: str = "reindex"
    # Multi-datasource types read one parquet per segment and merge by primary
    # key (union of PKs); None keeps the legacy single-dataset read untouched.
    multi_source: ObjectIndexMultiSourcePlan | None = None
    # Unlike direct links, M:N links are backed by their own join datasource.
    # Capture those sources before creating the run so failure replay cannot
    # quietly switch to whatever happened to become the latest join snapshot.
    link_sources: Mapping[str, ObjectIndexLinkSource] = field(default_factory=dict)


@dataclass(frozen=True)
class ObjectIndexRebuildCounts:
    rows_read: int
    objects_upserted: int
    objects_deleted: int
    links_upserted: int
    # Refresh evidence for operators: how the run resolved the source snapshot
    # ("full" vs "changelog_incremental") and how many rows it could skip.
    refresh_mode: str = "full"
    rows_skipped: int = 0
    # M:N links read their own join datasource. Persisting the exact version
    # prevents an operator from mistaking the object snapshot for the link source.
    link_source_dataset_version_ids: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ObjectIndexSourceRow:
    object_id: str
    base_patch: ObjectPropertyMap


@dataclass(frozen=True)
class ObjectCdcEvent:
    event_id: str
    op: str
    object_id: str
    base_patch: ObjectPropertyMap
    ordering: ObjectPropertyMap
    late_data_status: str | None = None
    event_time_lag_seconds: int | None = None


@dataclass(frozen=True)
class ObjectIndexCdcCounts:
    rows_read: int
    objects_upserted: int
    objects_deleted: int
    events_skipped: int
    links_upserted: int


@dataclass(frozen=True)
class ObjectIndexStats:
    object_count: int
    object_hash: str


def object_index_stats(records: Sequence[ObjectRecordRow]) -> ObjectIndexStats:
    payload = [_validation_row(row) for row in sorted(records, key=lambda item: item["object_id"])]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return ObjectIndexStats(object_count=len(payload), object_hash=hashlib.sha256(encoded.encode()).hexdigest())


def _validation_row(row: ObjectRecordRow) -> dict[str, object]:
    return {
        "baseProperties": row["base_properties"],
        "deleted": row["deleted"],
        "deletionReason": row["deletion_reason"],
        "editProperties": row["edit_properties"],
        "objectId": row["object_id"],
        "properties": row["properties"],
    }


def object_index_rebuild_response(
    plan: ObjectIndexRebuildPlan,
    counts: ObjectIndexRebuildCounts,
) -> ObjectIndexRebuildResult:
    return {
        "index_run_id": plan.run_id,
        "object_type": plan.object_type_api_name,
        "rows_read": counts.rows_read,
        "objects_upserted": counts.objects_upserted,
        "objects_deleted": counts.objects_deleted,
        "links_upserted": counts.links_upserted,
    }


def object_index_shadow_response(
    plan: ObjectIndexRebuildPlan,
    counts: ObjectIndexRebuildCounts,
    validation: ObjectIndexValidationResult,
) -> ObjectIndexShadowRebuildResult:
    return {
        "index_run_id": plan.run_id,
        "object_type": plan.object_type_api_name,
        "indexVersion": plan.index_version,
        "previousIndexVersion": plan.previous_index_version,
        "rows_read": counts.rows_read,
        "objects_upserted": counts.objects_upserted,
        "objects_deleted": counts.objects_deleted,
        "links_upserted": counts.links_upserted,
        "is_switched": True,
        "validation": validation,
    }


def object_index_cdc_response(
    run_id: str,
    object_type_api_name: str,
    counts: ObjectIndexCdcCounts,
) -> ObjectIndexCdcResult:
    return {
        "index_run_id": run_id,
        "object_type": object_type_api_name,
        "rows_read": counts.rows_read,
        "objects_upserted": counts.objects_upserted,
        "objects_deleted": counts.objects_deleted,
        "events_skipped": counts.events_skipped,
    }
