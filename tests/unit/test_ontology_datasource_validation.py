"""Direct tests for apply-time multi-datasource backing validation."""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.services.ontology_datasource_validation import (
    resolved_property_datasources,
    validate_yaml_object_datasources,
)
from foundry_lite.application.services.ontology_persisted_validation import validate_persisted_ontology
from foundry_lite.application.services.ontology_yaml import object_type_backing
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


class FakeTransaction:
    pass


_COLUMNS_BY_DATASET: dict[str, dict[str, dict[str, object]]] = {
    "clean.orders": {
        "order_id": {"name": "order_id", "nullable": False},
        "amount": {"name": "amount", "nullable": True},
    },
    "clean.order_finance": {
        "order_id": {"name": "order_id", "nullable": False},
        "margin": {"name": "margin", "nullable": True},
    },
    "clean.order_finance_no_pk": {
        "margin": {"name": "margin", "nullable": True},
    },
    "clean.order_finance_null_pk": {
        "order_id": {"name": "order_id", "nullable": True},
        "margin": {"name": "margin", "nullable": True},
    },
}


def _dataset_columns(
    conn: TransactionContext,
    ctx: RequestContext,
    dataset_ref: str,
) -> Mapping[str, Mapping[str, object]]:
    return _COLUMNS_BY_DATASET[dataset_ref]


def _object_def(
    *,
    finance_dataset: str = "clean.order_finance",
    margin_datasource: object = "finance",
    datasources: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    backing_datasources = datasources or [
        {"name": "erp", "dataset": "clean.orders", "primaryKeyColumns": ["order_id"]},
        {"name": "finance", "dataset": finance_dataset, "primaryKeyColumns": ["order_id"], "requiredRole": "finance"},
    ]
    return {
        "apiName": "Order",
        "primaryKey": "orderId",
        "backing": {"mode": "snapshot", "datasources": backing_datasources},
        "properties": [
            {"apiName": "orderId", "type": "string", "column": "order_id", "nullable": False},
            {"apiName": "amount", "type": "float", "column": "amount"},
            {"apiName": "margin", "type": "float", "column": "margin", "datasource": margin_datasource},
        ],
    }


def _property_defs(object_def: dict[str, object]) -> dict[str, dict[str, object]]:
    return {str(prop["apiName"]): prop for prop in object_def["properties"]}  # type: ignore[index, union-attr]


def _validate(object_def: dict[str, object]) -> None:
    validate_yaml_object_datasources(
        FakeTransaction(),
        RequestContext(),
        object_def,
        _property_defs(object_def),
        _dataset_columns,
    )


def test_valid_multi_datasource_declaration_passes_per_segment_checks() -> None:
    object_def = _object_def()

    _validate(object_def)

    backing = object_type_backing(object_def)
    assert resolved_property_datasources(backing, _property_defs(object_def)) == {
        "orderId": "erp",
        "amount": "erp",
        "margin": "finance",
    }


def test_property_referencing_unknown_datasource_is_rejected() -> None:
    object_def = _object_def(margin_datasource="treasury")

    with pytest.raises(ValidationFailed, match="property references unknown datasource") as exc_info:
        _validate(object_def)

    assert exc_info.value.details == {"objectType": "Order", "property": "margin", "datasource": "treasury"}


def test_row_wise_property_mapping_is_rejected() -> None:
    object_def = _object_def(margin_datasource=["erp", "finance"])

    with pytest.raises(ValidationFailed, match="row-wise multi-datasource is not supported"):
        _validate(object_def)


def test_duplicate_datasource_names_are_rejected() -> None:
    object_def = _object_def(
        datasources=[
            {"name": "erp", "dataset": "clean.orders", "primaryKeyColumns": ["order_id"]},
            {"name": "erp", "dataset": "clean.order_finance", "primaryKeyColumns": ["order_id"]},
        ],
        margin_datasource="erp",
    )

    with pytest.raises(ValidationFailed, match="duplicate datasource name"):
        _validate(object_def)


def test_primary_key_column_must_exist_in_every_datasource() -> None:
    object_def = _object_def(finance_dataset="clean.order_finance_no_pk")

    with pytest.raises(ValidationFailed, match="primary key column missing") as exc_info:
        _validate(object_def)

    assert exc_info.value.details == {"column": "order_id", "datasource": "finance"}


def test_primary_key_column_must_be_non_null_in_every_datasource() -> None:
    object_def = _object_def(finance_dataset="clean.order_finance_null_pk")

    with pytest.raises(ValidationFailed, match="primary key column must be non-null"):
        _validate(object_def)


def test_property_column_must_exist_in_its_own_datasource() -> None:
    object_def = _object_def(margin_datasource="erp")

    with pytest.raises(ValidationFailed, match="property column missing") as exc_info:
        _validate(object_def)

    assert exc_info.value.details == {"objectType": "Order", "property": "margin"}


def test_explicit_datasource_on_edit_layer_property_is_rejected() -> None:
    object_def = _object_def()
    properties = object_def["properties"]
    assert isinstance(properties, list)
    properties.append({"apiName": "note", "type": "string", "source": "edit_layer", "datasource": "erp"})

    with pytest.raises(ValidationFailed, match="datasource requires a dataset-backed property"):
        _validate(object_def)


def test_cdc_backing_is_rejected_for_multi_datasource_types() -> None:
    object_def = _object_def()
    backing = object_def["backing"]
    assert isinstance(backing, dict)
    backing["cdc"] = {"dataset": "cdc.orders"}

    with pytest.raises(ValidationFailed, match="cdc backing requires a single-datasource object type"):
        _validate(object_def)


def test_mixing_top_level_dataset_with_datasources_is_rejected() -> None:
    object_def = _object_def()
    backing = object_def["backing"]
    assert isinstance(backing, dict)
    backing["dataset"] = "clean.orders"

    with pytest.raises(ValidationFailed, match="backing cannot mix datasources with a top-level dataset"):
        _validate(object_def)


class _PersistedOntologyRepository:
    """Minimal persisted rows for one multi-datasource object type."""

    def __init__(self, *, margin_datasource: str) -> None:
        self._object_type = {
            "id": "otype_Order",
            "tenant_id": "tenant-demo",
            "ontology_version_id": "ont_1",
            "api_name": "Order",
            "display_name": "Order",
            "description": None,
            "primary_key_property": "orderId",
            "backing": {
                "datasources": [
                    {"name": "erp", "dataset": "clean.orders", "primaryKeyColumns": ["order_id"]},
                    {"name": "finance", "dataset": "clean.order_finance", "primaryKeyColumns": ["order_id"]},
                ]
            },
            "config": {"propertyDatasources": {"orderId": "erp", "margin": margin_datasource}},
        }

    def object_types_for_version(self, **_kwargs: object) -> list[dict[str, object]]:
        return [dict(self._object_type)]

    def link_types_for_version(self, **_kwargs: object) -> list[dict[str, object]]:
        return []

    def properties_for_object_type(self, **_kwargs: object) -> list[dict[str, object]]:
        return [
            _persisted_property("orderId", "order_id"),
            _persisted_property("margin", "margin"),
        ]

    def actions_for_target(self, **_kwargs: object) -> list[dict[str, object]]:
        return []


def _persisted_property(api_name: str, column_name: str) -> dict[str, object]:
    return {
        "id": f"ptype_{api_name}",
        "tenant_id": "tenant-demo",
        "object_type_id": "otype_Order",
        "api_name": api_name,
        "display_name": api_name,
        "data_type": "string",
        "nullable": False,
        "indexed": False,
        "searchable": False,
        "editable": False,
        "classification": None,
        "source": "dataset",
        "column_name": column_name,
        "edit_policy": "source_wins",
        "derivation": None,
    }


def test_persisted_multi_datasource_ontology_validates_each_property_in_its_segment() -> None:
    validate_persisted_ontology(
        _PersistedOntologyRepository(margin_datasource="finance"),  # type: ignore[arg-type]
        FakeTransaction(),
        RequestContext(),
        "ont_1",
        _dataset_columns,
    )

    with pytest.raises(ValidationFailed, match="property column missing") as exc_info:
        validate_persisted_ontology(
            _PersistedOntologyRepository(margin_datasource="erp"),  # type: ignore[arg-type]
            FakeTransaction(),
            RequestContext(),
            "ont_1",
            _dataset_columns,
        )

    assert exc_info.value.details == {"objectType": "Order", "property": "margin"}


def test_single_dataset_backing_keeps_legacy_error_details() -> None:
    object_def = {
        "apiName": "Order",
        "primaryKey": "orderId",
        "backing": {"dataset": "clean.orders"},
        "properties": [{"apiName": "orderId", "type": "string", "column": "missing_order_id"}],
    }

    with pytest.raises(ValidationFailed, match="primary key column missing") as exc_info:
        _validate(object_def)

    assert exc_info.value.details == {"column": "missing_order_id"}
