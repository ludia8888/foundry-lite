"""Shape contract for the object-index write-model records.

These frozen dataclasses are the payloads every ObjectIndexRepository
implementation accepts; the repository contract exercises persistence, so
this file pins the record shapes themselves: immutability (services build
them once and share them across retries) and the index-version defaults the
shadow-index rebuild relies on.
"""

from __future__ import annotations

import dataclasses

import pytest
from foundry_lite.application.ports import (
    IndexRunRecord,
    ObjectLinkInsert,
    ObjectRecordInsert,
)


def _index_run_record() -> IndexRunRecord:
    return IndexRunRecord(
        run_id="idx_run_1",
        tenant_id="tenant-demo",
        object_type_id="ot_1",
        object_type_api_name="Order",
        trigger_type="manual",
        source_ref={"dataset_version_id": "dsv_1"},
        status="running",
        cursor={"last_row": 0},
        rows_read=0,
        objects_upserted=0,
        objects_deleted=0,
        links_upserted=0,
        error=None,
        started_at="2026-01-01T00:00:00+00:00",
        completed_at=None,
        created_at="2026-01-01T00:00:00+00:00",
    )


def _object_record_insert() -> ObjectRecordInsert:
    return ObjectRecordInsert(
        record_id="rec_1",
        tenant_id="tenant-demo",
        object_type_id="ot_1",
        object_type_api_name="Order",
        object_id="O-1",
        properties={"orderId": "O-1"},
        base_properties={"orderId": "O-1"},
        edit_properties={},
        property_versions={},
        source_dataset_version_id="dsv_1",
        source_hash="hash",
        object_version=1,
        deleted=False,
        deletion_reason=None,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def _object_link_insert() -> ObjectLinkInsert:
    return ObjectLinkInsert(
        link_id="lnk_1",
        tenant_id="tenant-demo",
        link_type_id="lt_1",
        link_type_api_name="OrderCustomer",
        from_object_type_id="ot_1",
        from_api_name="Order",
        from_object_id="O-1",
        to_object_type_id="ot_2",
        to_api_name="Customer",
        to_object_id="C-1",
        properties={},
        source_dataset_version_id="dsv_1",
        link_version=1,
        deleted=False,
        deletion_reason=None,
        updated_at="2026-01-01T00:00:00+00:00",
    )


def test_index_write_records_are_immutable() -> None:
    """Records are shared across retry paths; mutation must be impossible."""
    for record in (_index_run_record(), _object_record_insert(), _object_link_insert()):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(record, "tenant_id", "tenant-other")


def test_object_and_link_inserts_default_to_the_active_index_version() -> None:
    """Shadow rebuilds override index_version explicitly; plain inserts must land active."""
    record = _object_record_insert()
    link = _object_link_insert()
    assert record.index_version == "active"
    assert record.is_active is True
    assert link.index_version == "active"
    assert link.is_active is True


def test_shadow_index_inserts_carry_the_requested_version() -> None:
    """The shadow rebuild pins new rows to a non-active index version until the swap."""
    record = dataclasses.replace(_object_record_insert(), index_version="shadow-1", is_active=False)
    assert record.index_version == "shadow-1"
    assert record.is_active is False
