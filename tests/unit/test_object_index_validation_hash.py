from __future__ import annotations

import pytest
from foundry_lite.application.ports import DatasetVersionRow, ObjectTypeRow
from foundry_lite.application.ports.object_read_repository import ObjectRecordRow
from foundry_lite.application.services.object_store.indexing_rebuild_service import _require_unique_source_object_ids
from foundry_lite.application.services.object_store.indexing_types import (
    ObjectIndexRebuildPlan,
    ObjectIndexSourceRow,
    object_index_stats,
)
from foundry_lite.domain.errors import ValidationFailed


def _record(**overrides: object) -> ObjectRecordRow:
    base: ObjectRecordRow = {
        "id": "obj_1",
        "tenant_id": "tenant-demo",
        "object_type_id": "ot_order",
        "object_type_api_name": "Order",
        "object_id": "O-1001",
        "index_version": "index_run_1",
        "is_active": True,
        "properties": {"orderId": "O-1001", "status": "PENDING"},
        "base_properties": {"orderId": "O-1001", "status": "PENDING"},
        "edit_properties": {},
        "property_versions": {"status": 1},
        "source_dataset_version_id": "dsv_1",
        "source_hash": "hash_1",
        "object_version": 1,
        "deleted": False,
        "deletion_reason": None,
        "created_at": "2026-06-15T00:00:00Z",
        "updated_at": "2026-06-15T00:00:00Z",
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def test_validation_hash_changes_with_current_properties() -> None:
    baseline = object_index_stats([_record()])
    drifted = object_index_stats([_record(properties={"orderId": "O-1001", "status": "APPROVED"})])

    # The shadow-reindex validation hash must reflect current_properties, so an
    # action edit that the shadow rebuild failed to replay is caught as drift
    # instead of passing on object_id/count alone.
    assert baseline.object_count == drifted.object_count == 1
    assert baseline.object_hash != drifted.object_hash


def test_validation_hash_changes_with_tombstone() -> None:
    baseline = object_index_stats([_record()])
    tombstoned = object_index_stats([_record(deleted=True, deletion_reason="source_deleted")])

    # A tombstone difference must change the hash so an index that resurrects a
    # deleted object cannot validate as identical to the live baseline.
    assert baseline.object_count == tombstoned.object_count == 1
    assert baseline.object_hash != tombstoned.object_hash


def test_shadow_reindex_validation_hash_includes_current_properties_and_tombstone() -> None:
    baseline = object_index_stats([_record()])
    property_drift = object_index_stats(
        [_record(properties={"orderId": "O-1001", "status": "APPROVED", "operatorNote": "edited"})]
    )
    tombstone_drift = object_index_stats([_record(deleted=True, deletion_reason="source_deleted")])

    # This exact evidence name is referenced by the tricky-failure checklist:
    # shadow validation must compare the current projection and tombstone state,
    # not just count/object_id, before an alias switch can be trusted.
    assert baseline.object_count == property_drift.object_count == tombstone_drift.object_count == 1
    assert baseline.object_hash != property_drift.object_hash
    assert baseline.object_hash != tombstone_drift.object_hash


def test_source_snapshot_duplicate_object_ids_fail_closed_before_reindex_writes() -> None:
    plan = ObjectIndexRebuildPlan(
        run_id="index_run_1",
        object_type_api_name="Order",
        object_type=_object_type(),
        dataset_version=_dataset_version(),
        source_dataset_version_id="dsv_orders_1",
        mode="full",
        index_version="active",
        previous_index_version="active",
    )

    with pytest.raises(ValidationFailed) as raised:
        _require_unique_source_object_ids(
            plan,
            [
                ObjectIndexSourceRow(object_id="O-1001", base_patch={"amount": 100}),
                ObjectIndexSourceRow(object_id="O-1002", base_patch={"amount": 200}),
                ObjectIndexSourceRow(object_id="O-1001", base_patch={"amount": 300}),
            ],
        )

    assert raised.value.details["duplicateObjectIds"] == ["O-1001"]
    assert raised.value.details["sourceDatasetVersionId"] == "dsv_orders_1"


def _object_type() -> ObjectTypeRow:
    return {
        "id": "ot_order",
        "tenant_id": "tenant-demo",
        "ontology_version_id": "ont_1",
        "api_name": "Order",
        "display_name": "Order",
        "description": None,
        "primary_key_property": "orderId",
        "backing": {},
        "config": {},
    }


def _dataset_version() -> DatasetVersionRow:
    return {
        "id": "dsv_orders_1",
        "tenant_id": "tenant-demo",
        "dataset_id": "ds_orders",
        "branch": "main",
        "version_number": 1,
        "transaction_id": "dstx_1",
        "schema_version": 1,
        "manifest_uri": "memory://manifest.json",
        "row_count": 3,
        "byte_size": 10,
        "status": "active",
        "superseded_by_version_id": None,
        "created_at": "2026-06-10T00:00:00Z",
    }
