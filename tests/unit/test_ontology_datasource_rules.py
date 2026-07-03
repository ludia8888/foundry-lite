"""Direct tests for the pure multi-datasource domain rules.

Covers ``foundry_lite.domain.ontology.datasources`` (normalization, property
segment resolution, composite version ids, segment masking) and
``foundry_lite.domain.ontology.migration_datasources`` (add/remove/move/
rebind/requiredRole migration classification).
"""

from __future__ import annotations

import pytest
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.ontology.datasources import (
    backing_datasources,
    composite_source_dataset_version_id,
    datasource_required_roles,
    is_multi_datasource_backing,
    primary_dataset_ref,
    property_datasource_name,
    property_datasource_rows,
    segment_masked_property_names,
    segment_primary_key_column,
    split_source_dataset_version_id,
)
from foundry_lite.domain.ontology.migration_datasources import datasource_migration_changes

_MULTI_BACKING = {
    "mode": "snapshot",
    "datasources": [
        {"name": "erp", "dataset": "clean.orders", "primaryKeyColumns": ["order_id"]},
        {
            "name": "finance",
            "dataset": "clean.order_finance",
            "primaryKeyColumns": ["order_id"],
            "requiredRole": "finance",
        },
    ],
}
_SINGLE_BACKING = {"dataset": "clean.orders", "mode": "snapshot"}


def test_single_dataset_backing_normalizes_to_one_primary_segment() -> None:
    segments = backing_datasources(_SINGLE_BACKING)

    assert is_multi_datasource_backing(_SINGLE_BACKING) is False
    assert [segment.name for segment in segments] == ["primary"]
    assert segments[0].dataset_ref == "clean.orders"
    assert segments[0].required_role is None
    assert primary_dataset_ref(_SINGLE_BACKING) == "clean.orders"


def test_multi_datasource_backing_normalizes_in_declaration_order() -> None:
    segments = backing_datasources(_MULTI_BACKING)

    assert is_multi_datasource_backing(_MULTI_BACKING) is True
    assert [segment.name for segment in segments] == ["erp", "finance"]
    assert segments[1].required_role == "finance"
    assert segment_primary_key_column(segments[1], "fallback") == "order_id"
    assert datasource_required_roles(segments) == {"finance": "finance"}
    assert primary_dataset_ref(_MULTI_BACKING) == "clean.orders"


def test_property_datasource_defaults_to_first_segment_and_rejects_row_wise() -> None:
    segments = backing_datasources(_MULTI_BACKING)

    assert property_datasource_name({"apiName": "amount", "column": "amount"}, segments) == "erp"
    assert (
        property_datasource_name({"apiName": "margin", "column": "margin", "datasource": "finance"}, segments)
        == "finance"
    )
    assert property_datasource_name({"apiName": "note", "source": "edit_layer"}, segments) is None
    with pytest.raises(ValidationFailed, match="row-wise multi-datasource is not supported"):
        property_datasource_name({"apiName": "margin", "column": "margin", "datasource": ["erp", "finance"]}, segments)


def test_composite_source_version_id_round_trips_in_declaration_order() -> None:
    composite = composite_source_dataset_version_id(["dsv_a", "dsv_b"])

    assert composite == "dsv_a+dsv_b"
    assert split_source_dataset_version_id(composite) == ("dsv_a", "dsv_b")
    assert composite_source_dataset_version_id(["dsv_only"]) == "dsv_only"
    assert split_source_dataset_version_id("dsv_only") == ("dsv_only",)


def test_segment_masking_requires_declared_role_with_admin_implicit() -> None:
    property_datasources = {"margin": "finance", "amount": "erp"}
    roles = {"finance": "finance"}

    assert segment_masked_property_names(property_datasources, roles, ["viewer"]) == {"margin"}
    assert segment_masked_property_names(property_datasources, roles, ["finance"]) == set()
    assert segment_masked_property_names(property_datasources, roles, ["admin"]) == set()


def test_property_datasource_rows_derives_segment_roles_from_backing_and_config() -> None:
    rows = property_datasource_rows(
        [
            {
                "object_type_api_name": "Order",
                "property_api_name": "margin",
                "column_name": "margin",
                "source": "dataset",
                "backing": _MULTI_BACKING,
                "config": {"propertyDatasources": {"margin": "finance", "amount": "erp"}},
            },
            {
                "object_type_api_name": "Order",
                "property_api_name": "amount",
                "column_name": "amount",
                "source": "dataset",
                "backing": _MULTI_BACKING,
                "config": {"propertyDatasources": {"margin": "finance", "amount": "erp"}},
            },
            {
                "object_type_api_name": "Order",
                "property_api_name": "operatorNote",
                "column_name": None,
                "source": "edit_layer",
                "backing": _MULTI_BACKING,
                "config": {},
            },
            {
                "object_type_api_name": "Customer",
                "property_api_name": "name",
                "column_name": "customer_name",
                "source": "dataset",
                "backing": _SINGLE_BACKING,
                "config": {},
            },
        ]
    )

    assert rows == [
        {
            "object_type_api_name": "Order",
            "property_api_name": "margin",
            "column_name": "margin",
            "classification": None,
            "segment_required_role": "finance",
        }
    ]


def _current_object(backing: dict[str, object], config: dict[str, object] | None = None) -> dict[str, object]:
    return {"api_name": "Order", "backing": backing, "config": config or {}}


def _current_properties() -> list[dict[str, object]]:
    return [
        {"api_name": "orderId", "source": "dataset", "column_name": "order_id"},
        {"api_name": "amount", "source": "dataset", "column_name": "amount"},
        {"api_name": "margin", "source": "dataset", "column_name": "margin"},
    ]


def _candidate(backing: dict[str, object], margin_datasource: str | None = None) -> dict[str, object]:
    margin: dict[str, object] = {"apiName": "margin", "type": "float", "column": "margin"}
    if margin_datasource is not None:
        margin["datasource"] = margin_datasource
    return {
        "apiName": "Order",
        "backing": backing,
        "properties": [
            {"apiName": "orderId", "type": "string", "column": "order_id"},
            {"apiName": "amount", "type": "float", "column": "amount"},
            margin,
        ],
    }


def _candidate_properties(candidate: dict[str, object]) -> dict[str, dict[str, object]]:
    return {str(prop["apiName"]): prop for prop in candidate["properties"]}  # type: ignore[index, union-attr]


def _changes(current: dict[str, object], candidate: dict[str, object]) -> dict[str, str]:
    changes = datasource_migration_changes(
        "Order",
        current,
        _current_properties(),
        candidate,
        _candidate_properties(candidate),
    )
    return {change.kind: change.status for change in changes}


def test_migration_adding_a_datasource_is_a_warning() -> None:
    current = _current_object(
        {"datasources": [{"name": "erp", "dataset": "clean.orders"}]},
        {"propertyDatasources": {"orderId": "erp", "amount": "erp", "margin": "erp"}},
    )
    candidate = _candidate(_MULTI_BACKING)

    assert _changes(current, candidate) == {"object_datasource_added": "warning"}


def test_migration_removing_a_datasource_is_blocked() -> None:
    current = _current_object(
        dict(_MULTI_BACKING),
        {"propertyDatasources": {"orderId": "erp", "amount": "erp", "margin": "finance"}},
    )
    candidate = _candidate({"datasources": [{"name": "erp", "dataset": "clean.orders"}]})

    kinds = _changes(current, candidate)
    assert kinds["object_datasource_removed"] == "blocked"


def test_migration_moving_a_property_between_datasources_is_blocked() -> None:
    current = _current_object(
        dict(_MULTI_BACKING),
        {"propertyDatasources": {"orderId": "erp", "amount": "erp", "margin": "erp"}},
    )
    candidate = _candidate(dict(_MULTI_BACKING), margin_datasource="finance")

    assert _changes(current, candidate) == {"property_datasource_moved": "blocked"}


def test_migration_rebinding_a_datasource_dataset_is_blocked() -> None:
    current = _current_object(
        dict(_MULTI_BACKING),
        {"propertyDatasources": {"orderId": "erp", "amount": "erp", "margin": "finance"}},
    )
    rebound = {
        "mode": "snapshot",
        "datasources": [
            {"name": "erp", "dataset": "clean.orders_v2", "primaryKeyColumns": ["order_id"]},
            {
                "name": "finance",
                "dataset": "clean.order_finance",
                "primaryKeyColumns": ["order_id"],
                "requiredRole": "finance",
            },
        ],
    }
    candidate = _candidate(rebound, margin_datasource="finance")

    kinds = _changes(current, candidate)
    assert kinds["object_datasource_dataset_changed"] == "blocked"


def test_migration_required_role_change_is_a_warning() -> None:
    current = _current_object(
        dict(_MULTI_BACKING),
        {"propertyDatasources": {"orderId": "erp", "amount": "erp", "margin": "finance"}},
    )
    relaxed = {
        "mode": "snapshot",
        "datasources": [
            {"name": "erp", "dataset": "clean.orders", "primaryKeyColumns": ["order_id"]},
            {"name": "finance", "dataset": "clean.order_finance", "primaryKeyColumns": ["order_id"]},
        ],
    }
    candidate = _candidate(relaxed, margin_datasource="finance")

    assert _changes(current, candidate) == {"object_datasource_required_role_changed": "warning"}


def test_migration_single_dataset_types_are_untouched_by_datasource_rules() -> None:
    current = _current_object(dict(_SINGLE_BACKING))
    candidate = _candidate({"dataset": "clean.orders_v2", "mode": "snapshot"})

    assert (
        datasource_migration_changes(
            "Order", current, _current_properties(), candidate, _candidate_properties(candidate)
        )
        == []
    )
