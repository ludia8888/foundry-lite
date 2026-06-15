from __future__ import annotations

from foundry_lite.application.ports.object_read_repository import ObjectRecordRow
from foundry_lite.application.services.object_store.indexing_types import object_index_stats


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
