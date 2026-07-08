"""Contract for dataset aggregation payloads used by API and SDK surfaces."""

from __future__ import annotations

from foundry_lite.application.ports import (
    DatasetAggregationFilter,
    DatasetAggregationGroup,
    DatasetAggregationMetric,
    DatasetAggregationResult,
)


def test_dataset_aggregation_payload_contract_names_evidence_fields() -> None:
    metric: DatasetAggregationMetric = {"name": "value", "function": "count", "property": None}
    filter_condition: DatasetAggregationFilter = {"column": "status", "operator": "eq", "value": "PENDING"}
    group: DatasetAggregationGroup = {"key": {"status": "PENDING"}, "metrics": {"value": 2}}
    result: DatasetAggregationResult = {
        "datasetRef": "clean.orders",
        "versionId": "dsv-1",
        "rowCount": 3,
        "filteredRowCount": 2,
        "groups": [group],
        "totalGroups": 1,
    }

    assert metric["function"] == "count"
    assert filter_condition["operator"] == "eq"
    assert result["datasetRef"] == "clean.orders"
    assert result["groups"][0]["metrics"]["value"] == 2
