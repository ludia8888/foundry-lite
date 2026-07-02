"""Pure object-indexing rules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from foundry_lite.domain.errors import ValidationFailed

FULL_INDEX_MODE = "full"
SHADOW_INDEX_MODE = "shadow"
SOURCE_MISSING_DELETION_REASON = "source_missing"


@dataclass(frozen=True)
class ShadowIndexValidation:
    expected_count: int
    actual_count: int
    expected_hash: str
    actual_hash: str

    def to_result(self) -> dict[str, object]:
        return {
            "expectedCount": self.expected_count,
            "actualCount": self.actual_count,
            "expectedHash": self.expected_hash,
            "actualHash": self.actual_hash,
        }


def should_emit_source_object_change(*, is_changed: bool, index_mode: str) -> bool:
    return is_changed and index_mode == FULL_INDEX_MODE


def should_prune_missing_source_records(index_mode: str) -> bool:
    return index_mode == FULL_INDEX_MODE


def should_prune_missing_source_links(index_mode: str) -> bool:
    return index_mode == FULL_INDEX_MODE


def should_delete_missing_source_item(
    *,
    is_deleted: bool,
    is_present_in_source: bool,
    index_mode: str,
) -> bool:
    return should_prune_missing_source_records(index_mode) and not is_deleted and not is_present_in_source


def should_delete_missing_source_link(
    *,
    is_deleted: bool,
    is_present_in_source: bool,
    index_mode: str,
) -> bool:
    return should_prune_missing_source_links(index_mode) and not is_deleted and not is_present_in_source


def should_record_base_update_conflict(
    *,
    edit_policy: str,
    property_api_name: str,
    edit_properties: Mapping[str, object],
    source_value: object,
) -> bool:
    if edit_policy != "conflict_requires_review":
        return False
    return property_api_name in edit_properties and edit_properties[property_api_name] != source_value


def should_refresh_existing_link(*, is_present: bool) -> bool:
    return is_present


def require_unique_source_object_ids(
    object_type_api_name: str,
    object_ids: Sequence[str],
    *,
    source_dataset_version_id: str | None = None,
) -> None:
    duplicates = sorted(_duplicate_values(object_ids))
    if not duplicates:
        return
    details: dict[str, object] = {"objectType": object_type_api_name, "duplicateObjectIds": duplicates}
    if source_dataset_version_id is not None:
        details["sourceDatasetVersionId"] = source_dataset_version_id
    raise ValidationFailed(
        "object source snapshot contains duplicate primary keys",
        details=details,
    )


def shadow_validation_failed(validation: ShadowIndexValidation) -> bool:
    return validation.expected_count != validation.actual_count or validation.expected_hash != validation.actual_hash


def _duplicate_values(values: Sequence[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates
