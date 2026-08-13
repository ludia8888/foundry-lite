from __future__ import annotations

import pytest
from foundry_lite.application.query_filters import matches_filter
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


def test_matches_filter_rejects_malformed_logical_filters() -> None:
    with pytest.raises(ValidationFailed, match="filter logical group must be a list"):
        matches_filter({"status": "PENDING"}, {"and": {"property": "status", "op": "eq", "value": "PENDING"}})
    with pytest.raises(ValidationFailed, match="filter logical group items must be objects"):
        matches_filter({"status": "PENDING"}, {"or": ["status"]})


def test_matches_filter_rejects_malformed_property_filters() -> None:
    with pytest.raises(ValidationFailed, match="filter field must be a string"):
        matches_filter({"status": "PENDING"}, {"op": "eq", "value": "PENDING"})
    with pytest.raises(ValidationFailed, match="filter field is required"):
        matches_filter({"status": "PENDING"}, {"property": "status", "op": "eq"})


def test_matches_filter_rejects_unsupported_operation() -> None:
    with pytest.raises(ValidationFailed, match="unsupported filter operation"):
        matches_filter({"status": "PENDING"}, {"property": "status", "op": "regex", "value": "P.*"})
