"""Arrow schema/type compatibility helpers for the Iceberg adapter."""

from __future__ import annotations

import hashlib
import json
from importlib import import_module
from typing import Any


def _arrow_table_row_hash(arrow_table: Any) -> str:
    rows = sorted(
        json.dumps(row, default=str, sort_keys=True, separators=(",", ":")) for row in arrow_table.to_pylist()
    )
    digest = hashlib.sha256()
    for row in rows:
        digest.update(row.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _arrow_schema_hash(arrow_table: Any) -> str:
    return hashlib.sha256(str(arrow_table.schema).encode("utf-8")).hexdigest()


def _iceberg_compatible_arrow_table(arrow_table: Any) -> Any:
    """Normalize Arrow types that Iceberg v2 cannot commit as-is."""
    pa = import_module("pyarrow")
    schema = arrow_table.schema
    fields = [_iceberg_compatible_field(pa, field) for field in schema]
    converted_schema = pa.schema(fields, metadata=schema.metadata)
    if converted_schema.equals(schema, check_metadata=True):
        return arrow_table
    return arrow_table.cast(converted_schema, safe=False)


def _iceberg_compatible_field(pa: Any, field: Any) -> Any:
    """Return the field with an Iceberg-compatible type, preserving field metadata."""
    converted_type = _iceberg_compatible_type(pa, field.type)
    if converted_type == field.type:
        return field
    return field.with_type(converted_type)


def _iceberg_compatible_type(pa: Any, data_type: Any) -> Any:
    """Return an Iceberg-compatible Arrow type."""
    if pa.types.is_timestamp(data_type) and data_type.unit == "ns":
        return pa.timestamp("us", tz=data_type.tz)
    if pa.types.is_struct(data_type):
        return pa.struct([_iceberg_compatible_field(pa, child) for child in data_type])
    if pa.types.is_fixed_size_list(data_type):
        return pa.list_(_iceberg_compatible_field(pa, data_type.value_field), list_size=data_type.list_size)
    if pa.types.is_list(data_type):
        return pa.list_(_iceberg_compatible_field(pa, data_type.value_field))
    if pa.types.is_large_list(data_type):
        return pa.large_list(_iceberg_compatible_field(pa, data_type.value_field))
    if pa.types.is_map(data_type):
        key_field = _iceberg_compatible_field(pa, data_type.key_field)
        item_field = _iceberg_compatible_field(pa, data_type.item_field)
        return pa.map_(key_field.type, item_field.type, keys_sorted=data_type.keys_sorted)
    return data_type
