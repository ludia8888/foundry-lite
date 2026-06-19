from __future__ import annotations

from collections.abc import Sequence

from foundry_lite.application.ports.ontology_repository import (
    ActionTypeRow,
    LinkTypeRow,
    ObjectTypeRow,
    PropertyTypeRow,
)
from foundry_lite.application.services.ontology_migration import (
    OntologyMigrationChange,
    OntologyMigrationPlan,
    build_ontology_migration_plan,
)


def test_ontology_migration_blocks_property_rename_without_consumer_mapping() -> None:
    plan = _plan(
        _definition(
            order_properties=[
                _yaml_property("orderId", "order_id", nullable=False),
                _yaml_property("state", "source_status", renamed_from="status"),
            ]
        )
    )

    assert plan.status == "blocked"
    assert plan.sdk_compatibility == "major_version_required"
    assert _change_kinds(plan.blocking_changes) == ["property_renamed"]


def test_ontology_migration_marks_deprecated_property_as_consumer_warning() -> None:
    plan = _plan(
        _definition(
            order_properties=[
                _yaml_property("orderId", "order_id", nullable=False),
                _yaml_property("status", "source_status", deprecated=True),
            ]
        )
    )

    assert plan.status == "compatible_with_warning"
    assert plan.sdk_compatibility == "compatible_with_warning"
    assert _change_kinds(plan.warning_changes) == ["property_deprecated"]
    assert plan.to_payload()["consumerCompatibility"] == "compatible_with_warning"


def test_ontology_migration_object_reindex_plan_is_deterministic() -> None:
    definition = _definition(
        order_properties=[
            _yaml_property("orderId", "order_id", nullable=False),
            _yaml_property("status", "normalized_status"),
        ]
    )

    first = _plan(definition)
    second = _plan(definition)

    assert first.status == "compatible_with_warning"
    assert _change_kinds(first.warning_changes) == ["property_mapping_changed"]
    assert first.reindex_operations[0].reindex_key == second.reindex_operations[0].reindex_key
    assert first.reindex_operations[0].to_payload()["changedFields"] == ["properties.status"]


def test_sdk_breaking_change_requires_major_version() -> None:
    plan = _plan(
        _definition(
            action_parameters=[
                _yaml_parameter("reason", required=True),
                _yaml_parameter("approvalCode", required=True),
            ]
        )
    )
    payload = plan.to_payload()

    assert plan.status == "blocked"
    assert payload["sdkCompatibility"] == "major_version_required"
    assert _change_kinds(plan.blocking_changes) == ["required_action_parameter_added"]


def test_ontology_migration_blocks_link_endpoint_change() -> None:
    plan = _plan(_definition(link_to="Order"))

    assert plan.status == "blocked"
    assert _change_kinds(plan.blocking_changes) == ["link_endpoint_changed"]


def _plan(definition: dict[str, object]) -> OntologyMigrationPlan:
    return build_ontology_migration_plan(
        source_ontology_version_id="ont_1",
        current_objects={"Order": _object_type_row(), "Customer": _object_type_row("Customer", "customerId")},
        current_properties={
            "Order": (
                _property_row("orderId", "order_id", nullable=False),
                _property_row("status", "source_status"),
            ),
            "Customer": (_property_row("customerId", "customer_id", object_type_id="otype_Customer"),),
        },
        current_links=(_link_type_row(),),
        current_actions=(_action_row(),),
        definition=definition,
    )


def _definition(
    *,
    order_properties: list[dict[str, object]] | None = None,
    link_to: str = "Customer",
    action_parameters: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "objectTypes": [
            {
                "apiName": "Order",
                "primaryKey": "orderId",
                "backing": {"dataset": "clean.orders", "mode": "snapshot", "primaryKeyColumns": ["order_id"]},
                "properties": order_properties
                or [
                    _yaml_property("orderId", "order_id", nullable=False),
                    _yaml_property("status", "source_status"),
                ],
            },
            {
                "apiName": "Customer",
                "primaryKey": "customerId",
                "backing": {"dataset": "clean.customers"},
                "properties": [_yaml_property("customerId", "customer_id", nullable=False)],
            },
        ],
        "linkTypes": [
            {
                "apiName": "OrderCustomer",
                "from": "Order",
                "to": link_to,
                "cardinality": "many_to_one",
                "backing": {"dataset": "clean.orders", "fromKey": "order_id", "toKey": "customer_id"},
            }
        ],
        "actionTypes": [
            {
                "apiName": "ApproveOrder",
                "target": "Order",
                "parameters": action_parameters or [_yaml_parameter("reason", required=True)],
            }
        ],
    }


def _yaml_property(
    api_name: str,
    column: str,
    *,
    nullable: bool = True,
    deprecated: bool = False,
    renamed_from: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"apiName": api_name, "column": column, "type": "string", "nullable": nullable}
    if deprecated:
        payload["deprecated"] = True
    if renamed_from is not None:
        payload["renamedFrom"] = renamed_from
    return payload


def _yaml_parameter(api_name: str, *, required: bool) -> dict[str, object]:
    return {"apiName": api_name, "type": "string", "required": required}


def _object_type_row(api_name: str = "Order", primary_key: str = "orderId") -> ObjectTypeRow:
    dataset = "clean.customers" if api_name == "Customer" else "clean.orders"
    backing: dict[str, object] = {"dataset": dataset}
    if api_name == "Order":
        backing = {"dataset": dataset, "mode": "snapshot", "primaryKeyColumns": ["order_id"]}
    return {
        "id": f"otype_{api_name}",
        "tenant_id": "tenant-demo",
        "ontology_version_id": "ont_1",
        "api_name": api_name,
        "display_name": api_name,
        "description": None,
        "primary_key_property": primary_key,
        "backing": backing,
        "config": {},
    }


def _property_row(
    api_name: str,
    column_name: str,
    *,
    nullable: bool = True,
    object_type_id: str = "otype_Order",
) -> PropertyTypeRow:
    return {
        "id": f"ptype_{api_name}",
        "tenant_id": "tenant-demo",
        "object_type_id": object_type_id,
        "api_name": api_name,
        "display_name": api_name,
        "data_type": "string",
        "nullable": nullable,
        "indexed": False,
        "searchable": False,
        "editable": False,
        "classification": None,
        "source": "dataset",
        "column_name": column_name,
        "edit_policy": "source_wins",
        "derivation": None,
    }


def _link_type_row() -> LinkTypeRow:
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
        "backing": {"dataset": "clean.orders", "fromKey": "order_id", "toKey": "customer_id"},
    }


def _action_row() -> ActionTypeRow:
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
            "parameters": (_yaml_parameter("reason", required=True),),
        },
        "enabled": True,
    }


def _change_kinds(changes: Sequence[OntologyMigrationChange]) -> list[str]:
    return [change.kind for change in changes]
