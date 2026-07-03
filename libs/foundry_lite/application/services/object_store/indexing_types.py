"""Application service helpers for indexing types workflows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass

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
