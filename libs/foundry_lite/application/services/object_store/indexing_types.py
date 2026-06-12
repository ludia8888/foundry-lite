from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.ports import (
    DatasetVersionRow,
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
    links_upserted: int


@dataclass(frozen=True)
class ObjectIndexSourceRow:
    object_id: str
    base_patch: ObjectPropertyMap


def object_index_rebuild_response(
    plan: ObjectIndexRebuildPlan,
    counts: ObjectIndexRebuildCounts,
) -> ObjectIndexRebuildResult:
    return {
        "index_run_id": plan.run_id,
        "object_type": plan.object_type_api_name,
        "rows_read": counts.rows_read,
        "objects_upserted": counts.objects_upserted,
        "links_upserted": counts.links_upserted,
    }
