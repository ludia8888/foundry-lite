from __future__ import annotations

import pytest
from foundry_lite.application.query_filters import matches_filter, validate_filter_ast
from foundry_lite.domain.errors import ValidationFailed


def test_matches_filter_supports_property_evaluators() -> None:
    properties = {"status": "PENDING", "amount": 700, "customer": "Acme Retail"}

    assert matches_filter(properties, {"property": "status", "op": "eq", "value": "PENDING"}) is True
    assert matches_filter(properties, {"property": "status", "op": "in", "value": ["PENDING", "REVIEW"]}) is True
    assert matches_filter(properties, {"property": "amount", "op": "gte", "value": 500}) is True
    assert matches_filter(properties, {"property": "amount", "op": "gt", "value": 699}) is True
    assert matches_filter(properties, {"property": "amount", "op": "lte", "value": 800}) is True
    assert matches_filter(properties, {"property": "amount", "op": "lt", "value": 701}) is True
    assert matches_filter(properties, {"property": "customer", "op": "contains", "value": "retail"}) is True


def test_matches_filter_supports_nested_logical_filters() -> None:
    properties = {"status": "REVIEW", "amount": 300}

    assert (
        matches_filter(
            properties,
            {
                "and": [
                    {"property": "status", "op": "eq", "value": "REVIEW"},
                    {
                        "or": [
                            {"property": "amount", "op": "gte", "value": 500},
                            {"property": "amount", "op": "lte", "value": 300},
                        ]
                    },
                ]
            },
        )
        is True
    )


def test_matches_filter_treats_missing_values_as_non_matches_for_range_and_contains() -> None:
    properties: dict[str, object] = {}

    assert matches_filter(properties, {"property": "amount", "op": "gte", "value": 1}) is False
    assert matches_filter(properties, {"property": "amount", "op": "lte", "value": 1}) is False
    assert matches_filter(properties, {"property": "customer", "op": "contains", "value": "acme"}) is False


def test_matches_filter_treats_mismatched_comparison_shapes_as_non_matches() -> None:
    assert matches_filter({"amount": "700"}, {"property": "amount", "op": "gte", "value": 500}) is False
    assert matches_filter({"amount": True}, {"property": "amount", "op": "lte", "value": 1}) is False
    assert matches_filter({"status": "PENDING"}, {"property": "status", "op": "in", "value": "PENDING"}) is False
    assert matches_filter({"approved": True}, {"property": "approved", "op": "eq", "value": 1}) is False
    assert matches_filter({"approved": True}, {"property": "approved", "op": "in", "value": [1]}) is False
    assert (
        matches_filter(
            {"metadata": {"priority": True}}, {"property": "metadata", "op": "contains", "value": "priority"}
        )
        is False
    )


def test_matches_filter_uses_declared_numeric_and_timestamp_semantics() -> None:
    property_types = {"amount": "float", "scheduledAt": "timestamp"}

    assert (
        matches_filter(
            {"amount": "700"},
            {"property": "amount", "op": "gte", "value": 500},
            property_data_types=property_types,
        )
        is True
    )
    assert (
        matches_filter(
            {"scheduledAt": "2026-01-01T01:00:00+02:00"},
            {"property": "scheduledAt", "op": "gte", "value": "2025-12-31T23:30:00Z"},
            property_data_types=property_types,
        )
        is False
    )


def test_typed_integer_filter_rejects_fractional_values() -> None:
    property_types = {"count": "integer"}

    with pytest.raises(ValidationFailed, match="filter value does not match property type"):
        validate_filter_ast(
            {"property": "count", "op": "gte", "value": 1.5},
            property_data_types=property_types,
        )
    assert (
        matches_filter(
            {"count": 2},
            {"property": "count", "op": "gte", "value": 1.5},
            property_data_types=property_types,
        )
        is False
    )


def test_matches_filter_rejects_malformed_logical_filters() -> None:
    with pytest.raises(ValidationFailed, match="filter logical group must be a list"):
        matches_filter({"status": "PENDING"}, {"and": {"property": "status", "op": "eq", "value": "PENDING"}})
    with pytest.raises(ValidationFailed, match="filter logical group items must be objects"):
        matches_filter({"status": "PENDING"}, {"or": ["status"]})
    with pytest.raises(ValidationFailed, match="non-empty"):
        matches_filter({"status": "PENDING"}, {"and": []})
    with pytest.raises(ValidationFailed, match="exactly one"):
        matches_filter(
            {"status": "PENDING"},
            {"and": [{"property": "status", "op": "eq", "value": "PENDING"}], "op": "eq"},
        )


def test_matches_filter_rejects_malformed_property_filters() -> None:
    with pytest.raises(ValidationFailed, match="filter field must be a string"):
        matches_filter({"status": "PENDING"}, {"op": "eq", "value": "PENDING"})
    with pytest.raises(ValidationFailed, match="filter field is required"):
        matches_filter({"status": "PENDING"}, {"property": "status", "op": "eq"})
    with pytest.raises(ValidationFailed, match="unsupported fields"):
        matches_filter({"status": "PENDING"}, {"property": "status", "op": "eq", "value": "PENDING", "typo": 1})


def test_matches_filter_rejects_unsupported_operation() -> None:
    with pytest.raises(ValidationFailed, match="unsupported filter operation"):
        matches_filter({"status": "PENDING"}, {"property": "status", "op": "regex", "value": "P.*"})
