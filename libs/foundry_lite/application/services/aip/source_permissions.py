"""Application service helpers for source permissions workflows."""

from __future__ import annotations

_SOURCE_PERMISSION_BY_TYPE = {
    "object": "object:read",
    "document": "object:read",
    "media_item_version": "object:read",
    "content_unit": "object:read",
    "dataset": "dataset:read",
    "dataset_version": "dataset:read",
    "function": "ontology:read",
}


def source_permission_for_type(resource_type: str) -> str:
    return _SOURCE_PERMISSION_BY_TYPE.get(resource_type, "object:read")
