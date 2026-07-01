"""Application service helpers for serving contract workflows."""

from __future__ import annotations

from foundry_lite.application.ports import ObjectTypeRow

OBJECT_TYPE_ID_SCOPE = "object_type_id"


def record_scope_object_type_id(object_type: ObjectTypeRow) -> str | None:
    """Return the object type id when the active ontology requires a scoped index."""
    if object_type["config"].get("servingRecordScope") == OBJECT_TYPE_ID_SCOPE:
        return object_type["id"]
    return None
