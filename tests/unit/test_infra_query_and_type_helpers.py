"""Unit tests for pure infrastructure helpers: object-query SQL value/condition
builders and the Iceberg Arrow-type compatibility mapping.
"""

from __future__ import annotations

import pyarrow as pa
import pytest
from foundry_lite.infrastructure.adapters.iceberg_dataset_storage import _iceberg_compatible_type
from foundry_lite.infrastructure.repositories.object_read_repository import (
    INVALID_SQL_VALUE,
    _cursor_sql_value,
    _filter_sql_value,
    _number_sql_value,
    _property_eq_condition,
    _property_in_condition,
    _property_range_condition,
    _property_value_expression,
    _sort_columns,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, INVALID_SQL_VALUE),  # bool is not a number
        (False, INVALID_SQL_VALUE),
        (5, 5.0),
        (3.5, 3.5),
        ("3.5", 3.5),
        ("nope", INVALID_SQL_VALUE),
        (None, INVALID_SQL_VALUE),
        ([1], INVALID_SQL_VALUE),
    ],
)
def test_number_sql_value(value: object, expected: object) -> None:
    assert (
        _number_sql_value(value) == expected
        if expected is not INVALID_SQL_VALUE
        else _number_sql_value(value) is INVALID_SQL_VALUE
    )


def test_filter_sql_value_by_type() -> None:
    assert _filter_sql_value(10, "number") == 10.0
    assert _filter_sql_value(True, "boolean") is True
    assert _filter_sql_value("yes", "boolean") is INVALID_SQL_VALUE
    assert _filter_sql_value("text", "string") == "text"
    assert _filter_sql_value(None, "string") == ""


def test_cursor_sql_value_by_type() -> None:
    assert _cursor_sql_value(7, "number") == 7.0
    assert _cursor_sql_value("bad", "number") == -1e308
    assert _cursor_sql_value(True, "boolean") is True
    assert _cursor_sql_value("nope", "boolean") is False
    assert _cursor_sql_value(None, "string") == ""
    assert _cursor_sql_value("k", "string") == "k"


def test_property_conditions_reject_invalid_inputs() -> None:
    # eq with an unparseable number -> always-false literal
    assert _property_eq_condition("margin", "number", "x") is not None
    # in-condition needs a non-string collection with at least one valid value
    assert _property_in_condition("margin", "number", "not-a-list") is not None
    assert _property_in_condition("margin", "number", ["bad"]) is not None
    assert _property_in_condition("margin", "number", [80, 90]) is not None
    # range on a boolean property is rejected
    assert _property_range_condition("flag", "boolean", True, is_lower_bound=True) is not None
    assert _property_range_condition("margin", "number", 50, is_lower_bound=False) is not None


def test_iceberg_compatible_type_normalizes_nested_and_timestamp() -> None:
    assert _iceberg_compatible_type(pa, pa.timestamp("ns", tz="UTC")) == pa.timestamp("us", tz="UTC")
    struct = _iceberg_compatible_type(pa, pa.struct([pa.field("ts", pa.timestamp("ns"))]))
    assert pa.types.is_struct(struct)
    listed = _iceberg_compatible_type(pa, pa.list_(pa.timestamp("ns")))
    assert pa.types.is_list(listed)
    large = _iceberg_compatible_type(pa, pa.large_list(pa.timestamp("ns")))
    assert pa.types.is_large_list(large)
    mapped = _iceberg_compatible_type(pa, pa.map_(pa.string(), pa.timestamp("ns")))
    assert pa.types.is_map(mapped)
    assert _iceberg_compatible_type(pa, pa.int64()) == pa.int64()


def test_property_value_expression_and_sort_columns_by_type() -> None:
    # value expression branches: boolean, number, string
    assert _property_value_expression("flag", "boolean") is not None
    assert _property_value_expression("margin", "number") is not None
    assert _property_value_expression("name", "string") is not None
    # sort columns over boolean and string properties exercise both coalesce branches
    sort_columns = _sort_columns(
        [{"property": "flag", "direction": "asc"}, {"property": "name", "direction": "desc"}],
        {"flag": "boolean", "name": "string"},
    )
    assert [data_type for _, _, data_type in sort_columns] == ["boolean", "string"]
