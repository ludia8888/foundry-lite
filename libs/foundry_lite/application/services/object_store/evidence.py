"""Build fine-grained object explanation evidence."""

from __future__ import annotations

from collections.abc import Sequence

from foundry_lite.application.ports import ObjectRecordRow, PropertyTypeRow
from foundry_lite.application.ports.object_read_repository import ObjectPropertyLineageItem


def object_property_lineage(
    record: ObjectRecordRow,
    properties: Sequence[PropertyTypeRow],
    *,
    masked_property_names: set[str],
) -> list[ObjectPropertyLineageItem]:
    """Return per-property lineage coordinates without copying property values."""

    return [
        _lineage_item(record, prop, is_masked=prop["api_name"] in masked_property_names)
        for prop in properties
        if _has_property(record, prop["api_name"])
    ]


def _lineage_item(
    record: ObjectRecordRow,
    prop: PropertyTypeRow,
    *,
    is_masked: bool,
) -> ObjectPropertyLineageItem:
    item: ObjectPropertyLineageItem = {
        "propertyName": prop["api_name"],
        "valueSource": _value_source(record, prop),
        "sourceDatasetVersionId": _source_dataset_version_id(record, prop),
        "sourceObjectVersion": record["object_version"],
        "sourceColumn": prop["column_name"],
        "propertyVersion": record["property_versions"].get(prop["api_name"]),
        "sourceHash": record["source_hash"],
        "maskingStatus": "masked" if is_masked else "visible",
    }
    expression = _derivation_expression(prop)
    if expression is not None:
        item["derivationExpression"] = expression
    return item


def _has_property(record: ObjectRecordRow, property_name: str) -> bool:
    return property_name in record["properties"] or property_name in record["edit_properties"]


def _value_source(record: ObjectRecordRow, prop: PropertyTypeRow) -> str:
    if prop["api_name"] in record["edit_properties"] or prop["source"] == "edit_layer":
        return "edit_layer"
    if prop["derivation"] is not None:
        return "derived_expression"
    if prop["column_name"] is not None:
        return "dataset_column"
    return "unknown"


def _source_dataset_version_id(record: ObjectRecordRow, prop: PropertyTypeRow) -> str | None:
    if _value_source(record, prop) == "edit_layer":
        return None
    return record["source_dataset_version_id"]


def _derivation_expression(prop: PropertyTypeRow) -> str | None:
    derivation = prop["derivation"]
    if derivation is None:
        return None
    expression = derivation.get("expression")
    return expression if isinstance(expression, str) else None
