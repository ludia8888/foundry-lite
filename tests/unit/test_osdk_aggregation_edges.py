from __future__ import annotations

import pytest
from foundry_lite.application.osdk_aggregation import (
    OsdkAggregatePlan,
    OsdkAggregatePlanMetric,
    aggregate_request_select,
    aggregate_result_from_groups,
    build_aggregate_plan,
)
from foundry_lite.domain.errors import ValidationFailed


def test_osdk_aggregate_builds_property_metrics_groups_and_descending_buckets() -> None:
    plan = build_aggregate_plan(
        "Order",
        ("status", "amount"),
        {
            "$select": {
                "total": {"amount": {"$sum": "desc"}},
                "average": {"amount": {"$avg": "desc"}},
            },
            "group_by": {"status": "exact"},
        },
    )
    result = aggregate_result_from_groups(
        plan,
        {
            "rowCount": 3,
            "filteredRowCount": 3,
            "totalGroups": 2,
            "groups": [
                {"key": {"status": "open"}, "metrics": {"total": None, "average": None}},
                {"key": {"status": "closed"}, "metrics": {"total": 10, "average": 5.0}},
            ],
        },
    )

    assert aggregate_request_select(plan) == [
        {"name": "total", "function": "sum", "property": "amount"},
        {"name": "average", "function": "avg", "property": "amount"},
    ]
    assert [bucket["group"]["status"] for bucket in result["data"]] == ["closed", "open"]
    assert result["excludedItems"] == 0


def test_osdk_aggregate_supports_count_alias_and_empty_metric_order_fallback() -> None:
    plan = build_aggregate_plan("Order", (), {"select": {"rows": {"$count": "asc"}}})
    empty_metric_plan = OsdkAggregatePlan((), "asc", (), ())

    assert plan.metrics == (OsdkAggregatePlanMetric("rows", "count", None),)
    assert aggregate_result_from_groups(
        empty_metric_plan,
        {
            "rowCount": 1,
            "filteredRowCount": 1,
            "totalGroups": 1,
            "groups": [{"key": {}, "metrics": {}}],
        },
    )["data"] == [{"group": {}, "metrics": []}]


@pytest.mark.parametrize(
    ("aggregate_request", "message"),
    [
        ({}, "requires select"),
        ({"select": {}}, "must not be empty"),
        ({"select": [], "$select": {}}, "accepts one select"),
        ({"select": []}, "select must be an object"),
        (
            {"select": {"count": {"$count": "asc"}, "total": {"amount": {"$sum": "desc"}}}},
            "one ordering",
        ),
        ({"select": {"bad": {"amount": {"$median": "asc"}}}}, "must be"),
        ({"select": {"count": {"$count": "sideways"}}}, "order must be"),
        ({"select": {"total": {"missing": {"$sum": "asc"}}}}, "unknown metric property"),
        (
            {"select": {"count": {"$count": "asc"}}, "groupBy": {"missing": "exact"}},
            "unknown groupBy property",
        ),
        (
            {"select": {"count": {"$count": "asc"}}, "groupBy": {"status": "prefix"}},
            "only exact groupBy",
        ),
        (
            {
                "select": {"count": {"$count": "asc"}},
                "groupBy": {"status": "exact"},
                "$groupBy": {},
            },
            "accepts one groupBy",
        ),
        (
            {"select": {"count": {"$count": "asc"}}, "groupBy": []},
            "groupBy must be an object",
        ),
    ],
)
def test_osdk_aggregate_rejects_ambiguous_or_invalid_requests(
    aggregate_request: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationFailed, match=message):
        build_aggregate_plan("Order", ("status", "amount"), aggregate_request)


@pytest.mark.parametrize(
    "metric",
    [
        {"bad": []},
        {"bad": {"amount": []}},
    ],
)
def test_osdk_aggregate_rejects_malformed_metric_shapes(metric: dict[str, object]) -> None:
    with pytest.raises(ValidationFailed, match="metric must be"):
        build_aggregate_plan("Order", ("amount",), {"select": metric})
