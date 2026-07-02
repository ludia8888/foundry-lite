"""Object store domain."""

from foundry_lite.domain.object_store.cdc import (
    cdc_deletion_reason,
    cdc_ordering_key,
    require_cdc_ordering,
    should_skip_cdc_event,
)
from foundry_lite.domain.object_store.indexing import (
    FULL_INDEX_MODE,
    SHADOW_INDEX_MODE,
    SOURCE_MISSING_DELETION_REASON,
    ShadowIndexValidation,
    require_unique_source_object_ids,
    shadow_validation_failed,
    should_delete_missing_source_item,
    should_delete_missing_source_link,
    should_emit_source_object_change,
    should_record_base_update_conflict,
)

__all__ = [
    "FULL_INDEX_MODE",
    "SHADOW_INDEX_MODE",
    "SOURCE_MISSING_DELETION_REASON",
    "ShadowIndexValidation",
    "cdc_deletion_reason",
    "cdc_ordering_key",
    "require_cdc_ordering",
    "require_unique_source_object_ids",
    "shadow_validation_failed",
    "should_delete_missing_source_item",
    "should_delete_missing_source_link",
    "should_emit_source_object_change",
    "should_record_base_update_conflict",
    "should_skip_cdc_event",
]
