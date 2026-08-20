from __future__ import annotations

import pytest
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.object_store.indexing import (
    ShadowIndexValidation,
    require_unique_source_object_ids,
    shadow_validation_failed,
    should_delete_missing_source_item,
    should_delete_missing_source_link,
    should_emit_source_object_change,
    should_record_base_update_conflict,
)


def test_object_indexing_domain_rules_gate_full_mode_side_effects() -> None:
    assert should_emit_source_object_change(is_changed=True, index_mode="full") is True
    assert should_emit_source_object_change(is_changed=True, index_mode="shadow") is False
    assert (
        should_delete_missing_source_item(
            is_deleted=False,
            is_present_in_source=False,
            is_source_backed=True,
            index_mode="full",
        )
        is True
    )
    assert (
        should_delete_missing_source_item(
            is_deleted=False,
            is_present_in_source=False,
            is_source_backed=True,
            index_mode="shadow",
        )
        is False
    )
    assert (
        should_delete_missing_source_item(
            is_deleted=False,
            is_present_in_source=False,
            is_source_backed=False,
            index_mode="full",
        )
        is False
    )
    assert (
        should_delete_missing_source_link(
            is_deleted=True,
            is_present_in_source=False,
            is_source_backed=True,
            index_mode="full",
        )
        is False
    )
    assert (
        should_delete_missing_source_link(
            is_deleted=False,
            is_present_in_source=False,
            is_source_backed=False,
            index_mode="full",
        )
        is False
    )


def test_object_indexing_domain_records_only_real_base_update_conflicts() -> None:
    edits = {"status": "APPROVED"}
    assert (
        should_record_base_update_conflict(
            edit_policy="conflict_requires_review",
            property_api_name="status",
            edit_properties=edits,
            source_value="PENDING",
        )
        is True
    )
    assert (
        should_record_base_update_conflict(
            edit_policy="source_wins",
            property_api_name="status",
            edit_properties=edits,
            source_value="PENDING",
        )
        is False
    )
    assert (
        should_record_base_update_conflict(
            edit_policy="conflict_requires_review",
            property_api_name="amount",
            edit_properties=edits,
            source_value=10,
        )
        is False
    )


def test_object_indexing_domain_rejects_duplicate_source_ids() -> None:
    with pytest.raises(ValidationFailed) as exc_info:
        require_unique_source_object_ids(
            "Order",
            ["O-1", "O-2", "O-1"],
            source_dataset_version_id="version-1",
        )

    assert exc_info.value.details["objectType"] == "Order"
    assert exc_info.value.details["duplicateObjectIds"] == ["O-1"]
    assert exc_info.value.details["sourceDatasetVersionId"] == "version-1"


def test_object_indexing_domain_shadow_validation_failure() -> None:
    passing = ShadowIndexValidation(2, 2, "hash-a", "hash-a")
    failing = ShadowIndexValidation(2, 3, "hash-a", "hash-b")

    assert shadow_validation_failed(passing) is False
    assert shadow_validation_failed(failing) is True
    assert passing.to_result() == {
        "expectedCount": 2,
        "actualCount": 2,
        "expectedHash": "hash-a",
        "actualHash": "hash-a",
    }
