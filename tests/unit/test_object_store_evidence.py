from __future__ import annotations

from foundry_lite.application.ports import ObjectRecordRow, PropertyTypeRow
from foundry_lite.application.services.object_store.evidence import object_property_lineage


def test_object_property_lineage_covers_edit_derived_dataset_and_unknown_sources() -> None:
    record = _object_record()
    lineage = {
        item["propertyName"]: item
        for item in object_property_lineage(
            record,
            [
                _property("amount", column_name="amount"),
                _property("approvalStatus", source="edit_layer"),
                _property("riskScore", derivation={"expression": "object.amount * 2"}),
                _property("looseValue"),
            ],
            masked_property_names={"riskScore"},
        )
    }

    assert lineage["amount"]["valueSource"] == "dataset_column"
    assert lineage["amount"]["sourceDatasetVersionId"] == "dsv_orders_v1"
    assert lineage["approvalStatus"]["valueSource"] == "edit_layer"
    assert lineage["approvalStatus"]["sourceDatasetVersionId"] is None
    assert lineage["riskScore"]["valueSource"] == "derived_expression"
    assert lineage["riskScore"]["derivationExpression"] == "object.amount * 2"
    assert lineage["riskScore"]["maskingStatus"] == "masked"
    assert lineage["looseValue"]["valueSource"] == "unknown"


def _object_record() -> ObjectRecordRow:
    return {
        "id": "obj_1",
        "tenant_id": "tenant-demo",
        "object_type_id": "otype_Order",
        "object_type_api_name": "Order",
        "object_id": "O-1",
        "index_version": "index_v1",
        "is_active": True,
        "properties": {"amount": 10, "riskScore": 20, "looseValue": "x"},
        "base_properties": {"amount": 10, "riskScore": 20, "looseValue": "x"},
        "edit_properties": {"approvalStatus": "APPROVED"},
        "property_versions": {"amount": 1, "approvalStatus": 2, "riskScore": 1, "looseValue": 1},
        "source_dataset_version_id": "dsv_orders_v1",
        "source_hash": "sha256:orders",
        "object_version": 3,
        "deleted": False,
        "deletion_reason": None,
        "created_at": "2026-06-10T00:00:00Z",
        "updated_at": "2026-06-10T00:01:00Z",
    }


def _property(
    api_name: str,
    *,
    source: str = "dataset",
    column_name: str | None = None,
    derivation: dict[str, object] | None = None,
) -> PropertyTypeRow:
    return {
        "id": f"ptype_{api_name}",
        "tenant_id": "tenant-demo",
        "object_type_id": "otype_Order",
        "api_name": api_name,
        "display_name": api_name,
        "data_type": "string",
        "nullable": True,
        "indexed": False,
        "searchable": False,
        "editable": source == "edit_layer",
        "classification": None,
        "source": source,
        "column_name": column_name,
        "edit_policy": "edit_only" if source == "edit_layer" else "source_wins",
        "derivation": derivation,
    }
