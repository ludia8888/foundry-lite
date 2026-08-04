"""Stable inverse-operation evidence captured with every reversible Action edit."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import LinkTypeRow, ObjectRecordRow


def property_revert_payload(record: ObjectRecordRow, patch: Mapping[str, object]) -> dict[str, object]:
    """Capture prior edit-overlay presence so revert can restore or remove each key."""
    edit_properties = record["edit_properties"]
    return {
        "operation": "set_property",
        "committedObjectVersion": record["object_version"] + 1,
        "properties": {key: {"wasPresent": key in edit_properties, "value": edit_properties.get(key)} for key in patch},
    }


def created_object_revert_payload() -> dict[str, object]:
    """Pin the first version that a create-object revert is allowed to delete."""
    return {"operation": "create_object", "committedObjectVersion": 1}


def deleted_object_revert_payload(record: ObjectRecordRow) -> dict[str, object]:
    """Pin the soft-deleted version that a delete-object revert may restore."""
    return {"operation": "delete_object", "committedObjectVersion": record["object_version"] + 1}


def link_revert_payload(
    meta: LinkTypeRow,
    link_type: str,
    source_object_id: str,
    target_object_id: str,
    operation: str,
) -> dict[str, object]:
    """Capture the concrete link identity and expected post-commit active state."""
    return {
        "operation": operation,
        "linkTypeId": meta["id"],
        "linkType": link_type,
        "fromObjectTypeId": meta["from_object_type_id"],
        "fromApiName": meta["from_api_name"],
        "fromObjectId": source_object_id,
        "toObjectTypeId": meta["to_object_type_id"],
        "toApiName": meta["to_api_name"],
        "toObjectId": target_object_id,
        "expectedActive": operation == "create_link",
    }
