from __future__ import annotations

from collections.abc import Mapping

import pytest
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.ontology_repository import (
    ActionTypeRow,
    LinkTypeRow,
    ObjectTypeRow,
    PropertyTypeRow,
)
from foundry_lite.application.services.ontology_validation import (
    ontology_validation_result,
    validate_ontology_definition,
    validate_persisted_link,
    validate_persisted_object_type,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


class FakeTransaction:
    pass


def test_ontology_definition_validation_uses_dataset_schema() -> None:
    definition = _yaml_definition(primary_key_column="order_id")

    validate_ontology_definition(FakeTransaction(), RequestContext(), definition, _dataset_columns)

    assert ontology_validation_result(definition) == {
        "status": "valid",
        "object_type_count": 1,
        "link_type_count": 0,
        "action_type_count": 1,
    }


def test_ontology_definition_validation_rejects_missing_primary_key_column() -> None:
    definition = _yaml_definition(primary_key_column="missing_order_id")

    with pytest.raises(ValidationFailed, match="primary key column missing") as exc_info:
        validate_ontology_definition(FakeTransaction(), RequestContext(), definition, _dataset_columns)

    assert exc_info.value.details == {"column": "missing_order_id"}


def test_persisted_validation_rejects_uneditable_action_mutation() -> None:
    properties = [_property_row("orderId", "order_id", editable=False), _property_row("status", "source_status")]
    action = _action_row(property_name="orderId")

    with pytest.raises(ValidationFailed, match="action mutation property must be editable"):
        validate_persisted_object_type(_object_type_row(), properties, [action], _dataset_columns_by_name())


def test_persisted_link_validation_rejects_missing_backing_key() -> None:
    link = _link_type_row(to_key="missing_customer_id")

    with pytest.raises(ValidationFailed, match="link backing key missing") as exc_info:
        validate_persisted_link(
            link,
            {"Order": _object_type_row(), "Customer": _customer_type_row()},
            _dataset_columns_by_name(),
        )

    assert exc_info.value.details == {"linkType": "OrderCustomer", "missing": ["missing_customer_id"]}


def _dataset_columns(
    conn: TransactionContext,
    ctx: RequestContext,
    dataset_ref: str,
) -> Mapping[str, Mapping[str, object]]:
    assert isinstance(conn, FakeTransaction)
    assert ctx.tenant_id == "tenant-demo"
    assert dataset_ref == "clean.orders"
    return _dataset_columns_by_name()


def _dataset_columns_by_name() -> Mapping[str, Mapping[str, object]]:
    return {
        "order_id": {"name": "order_id", "nullable": False},
        "source_status": {"name": "source_status", "nullable": False},
        "customer_id": {"name": "customer_id", "nullable": False},
    }


def _yaml_definition(primary_key_column: str) -> dict[str, object]:
    return {
        "objectTypes": [
            {
                "apiName": "Order",
                "primaryKey": "orderId",
                "backing": {"dataset": "clean.orders"},
                "properties": [
                    {"apiName": "orderId", "type": "string", "column": primary_key_column},
                    {"apiName": "status", "type": "string", "column": "source_status", "editable": True},
                ],
            }
        ],
        "actionTypes": [
            {
                "apiName": "ApproveOrder",
                "target": "Order",
                "mutations": [{"type": "setProperty", "property": "status", "value": "APPROVED"}],
            }
        ],
    }


def _object_type_row(api_name: str = "Order") -> ObjectTypeRow:
    return {
        "id": f"otype_{api_name}",
        "tenant_id": "tenant-demo",
        "ontology_version_id": "ont_1",
        "api_name": api_name,
        "display_name": api_name,
        "description": None,
        "primary_key_property": "orderId" if api_name == "Order" else "customerId",
        "backing": {"dataset": "clean.orders"},
        "config": {},
    }


def _customer_type_row() -> ObjectTypeRow:
    return _object_type_row("Customer")


def _property_row(api_name: str, column_name: str, *, editable: bool = True) -> PropertyTypeRow:
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
        "editable": editable,
        "classification": None,
        "source": "dataset",
        "column_name": column_name,
        "edit_policy": "source_wins",
        "derivation": None,
    }


def _action_row(property_name: str) -> ActionTypeRow:
    return {
        "id": "atype_ApproveOrder",
        "tenant_id": "tenant-demo",
        "ontology_version_id": "ont_1",
        "api_name": "ApproveOrder",
        "display_name": "Approve order",
        "target_object_type_id": "otype_Order",
        "target_api_name": "Order",
        "parameter_schema": {"type": "object"},
        "definition": {
            "apiName": "ApproveOrder",
            "target": "Order",
            "mutations": [{"type": "setProperty", "property": property_name, "value": "APPROVED"}],
        },
        "enabled": True,
    }


def _link_type_row(to_key: str) -> LinkTypeRow:
    return {
        "id": "ltype_OrderCustomer",
        "tenant_id": "tenant-demo",
        "ontology_version_id": "ont_1",
        "api_name": "OrderCustomer",
        "display_name": "Order belongs to Customer",
        "from_object_type_id": "otype_Order",
        "from_api_name": "Order",
        "to_object_type_id": "otype_Customer",
        "to_api_name": "Customer",
        "cardinality": "many_to_one",
        "backing": {"dataset": "clean.orders", "fromKey": "order_id", "toKey": to_key},
    }
