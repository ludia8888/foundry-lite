"""Object-index record payload factory."""

from __future__ import annotations

from foundry_lite.application.ports import ObjectPropertyMap, ObjectRecordInsert, ObjectRecordRow
from foundry_lite.application.primitives import _json_hash, _new_id, _now
from foundry_lite.application.services.object_store.indexing_types import (
    ObjectIndexRebuildPlan,
    ObjectIndexSourceRow,
)
from foundry_lite.domain.context import RequestContext


def new_object_record_insert(
    ctx: RequestContext,
    plan: ObjectIndexRebuildPlan,
    source: ObjectIndexSourceRow,
    active: ObjectRecordRow | None,
    base_patch: ObjectPropertyMap,
    edit_properties: ObjectPropertyMap,
    property_versions: ObjectPropertyMap | None,
    current: ObjectPropertyMap,
) -> ObjectRecordInsert:
    current_properties = dict(current)
    property_version_values: dict[str, object] = (
        dict(property_versions) if property_versions is not None else {key: 1 for key in current_properties}
    )
    now = _now()
    return ObjectRecordInsert(
        record_id=_new_id("obj"),
        tenant_id=ctx.tenant_id,
        object_type_id=plan.object_type["id"],
        object_type_api_name=plan.object_type["api_name"],
        object_id=source.object_id,
        properties=current_properties,
        base_properties=base_patch,
        edit_properties=edit_properties,
        property_versions=property_version_values,
        source_dataset_version_id=plan.source_dataset_version_id,
        source_hash=_json_hash(base_patch),
        object_version=active["object_version"] if active is not None else 1,
        deleted=False,
        deletion_reason=None,
        created_at=now,
        updated_at=now,
        index_version=plan.index_version,
        is_active=plan.mode == "full",
    )
