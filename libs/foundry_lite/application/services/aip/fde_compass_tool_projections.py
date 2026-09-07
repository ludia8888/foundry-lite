"""Keep Compass discovery usable when resources contain generated application files."""

from __future__ import annotations

import json
from collections.abc import Mapping

from foundry_lite.application.services.aip.fde_tool_result import hash_json

_METADATA_INLINE_BYTES = 1024
_COLLECTION_KEYS = frozenset({"items", "resources", "folders", "projects"})
_RECORD_KEYS = frozenset({"resource", "project", "folder"})


def compass_tool_result(payload: Mapping[str, object]) -> dict[str, object]:
    """Project already-authorized records without changing their durable contents."""

    result = dict(payload)
    for key, value in payload.items():
        if key in _COLLECTION_KEYS and isinstance(value, list):
            result[key] = [_compact_record(item) if isinstance(item, Mapping) else item for item in value]
        elif key in _RECORD_KEYS and isinstance(value, Mapping):
            result[key] = _compact_record(value)
    return result


def _compact_record(record: Mapping[str, object]) -> dict[str, object]:
    result = dict(record)
    metadata = record.get("metadata")
    if not isinstance(metadata, Mapping):
        return result
    encoded = json.dumps(metadata, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) <= _METADATA_INLINE_BYTES:
        return result
    result.pop("metadata", None)
    result["metadataSummary"] = {
        "isContentIncluded": False,
        "byteSize": len(encoded),
        "fingerprint": hash_json(metadata),
        "fieldNames": sorted(str(key) for key in metadata)[:20],
        "fieldCount": len(metadata),
        "reason": "large_metadata_stored_on_governed_resource",
    }
    if isinstance(record.get("rid"), str):
        result["metadataRetrievePath"] = f"/api/resources/{record['rid']}"
    return result
