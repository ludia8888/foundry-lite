from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.ports import (
    DatasetVersionRow,
    ObjectIndexCdcResult,
    ObjectIndexRebuildResult,
    ObjectPropertyMap,
    ObjectTypeRow,
)


@dataclass(frozen=True)
class ObjectIndexRebuildPlan:
    run_id: str
    object_type_api_name: str
    object_type: ObjectTypeRow
    dataset_version: DatasetVersionRow
    source_dataset_version_id: str


@dataclass(frozen=True)
class ObjectIndexRebuildCounts:
    rows_read: int
    objects_upserted: int
    objects_deleted: int
    links_upserted: int


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


@dataclass(frozen=True)
class ObjectIndexCdcCounts:
    rows_read: int
    objects_upserted: int
    objects_deleted: int
    events_skipped: int
    links_upserted: int


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
