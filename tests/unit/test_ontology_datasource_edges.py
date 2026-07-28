from __future__ import annotations

import pytest
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.ontology.datasources import (
    backing_datasources,
    composite_source_dataset_version_id,
    property_datasource_rows,
)


def test_datasource_composite_version_requires_at_least_one_segment() -> None:
    with pytest.raises(ValidationFailed, match="at least one"):
        composite_source_dataset_version_id([])


@pytest.mark.parametrize(
    "backing",
    [
        {},
        {"datasources": [None]},
        {"datasources": [{"name": "", "dataset": "clean.orders"}]},
        {"datasources": [{"name": "erp", "dataset": ""}]},
        {"datasources": [{"name": "erp", "dataset": "clean.orders", "requiredRole": 3}]},
        {"datasources": [{"name": "erp", "dataset": "clean.orders", "primaryKeyColumns": "id"}]},
        {"datasources": [{"name": "erp", "dataset": "clean.orders", "primaryKeyColumns": [""]}]},
    ],
)
def test_datasource_backing_rejects_incomplete_segment_contracts(
    backing: dict[str, object],
) -> None:
    with pytest.raises(ValidationFailed):
        backing_datasources(backing)


def test_property_datasource_rows_skip_non_object_single_and_unrestricted_backings() -> None:
    rows = property_datasource_rows(
        [
            {
                "object_type_api_name": "Order",
                "property_api_name": "amount",
                "source": "dataset",
                "backing": "invalid",
            },
            {
                "object_type_api_name": "Order",
                "property_api_name": "amount",
                "source": "dataset",
                "backing": {"dataset": "clean.orders"},
            },
            {
                "object_type_api_name": "Order",
                "property_api_name": "amount",
                "source": "dataset",
                "backing": {
                    "datasources": [
                        {"name": "erp", "dataset": "clean.orders"},
                        {"name": "finance", "dataset": "clean.finance"},
                    ]
                },
            },
        ]
    )

    assert rows == []


def test_property_datasource_rows_falls_back_to_primary_segment_when_config_is_missing() -> None:
    rows = property_datasource_rows(
        [
            {
                "object_type_api_name": "Order",
                "property_api_name": "amount",
                "column_name": "amount",
                "source": "dataset",
                "backing": {
                    "datasources": [
                        {
                            "name": "erp",
                            "dataset": "clean.orders",
                            "requiredRole": "erp-reader",
                        },
                        {
                            "name": "finance",
                            "dataset": "clean.finance",
                            "requiredRole": "finance-reader",
                        },
                    ]
                },
                "config": [],
            }
        ]
    )

    assert rows[0]["segment_required_role"] == "erp-reader"
