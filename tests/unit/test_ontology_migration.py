from __future__ import annotations

from collections.abc import Sequence

import pytest
from foundry_lite.application.ports.ontology_repository import (
    ActionTypeRow,
    LinkTypeRow,
    ObjectTypeRow,
    PropertyTypeRow,
)
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.ontology.migration import (
    _current_action_parameters,
    _mapping,
    _parameter_definitions_by_api,
    _yaml_rows_by_api,
    build_ontology_migration_plan,
    link_type_backing,
    mapping_sequence,
    object_type_backing,
    optional_bool,
    optional_str,
    property_source,
    required_str,
)
from foundry_lite.domain.ontology.migration_changes import (
    blocked_action_removed,
    blocked_action_target_changed,
    blocked_link_backing_changed,
    blocked_link_cardinality_changed,
    blocked_link_removed,
    blocked_object_removed,
    blocked_parameter_became_required,
    blocked_parameter_removed,
    blocked_parameter_type_changed,
    blocked_primary_key_changed,
    blocked_property_removed,
    blocked_property_type_change,
    warning_object_reindex,
    warning_optional_parameter_added,
    warning_parameter_became_optional,
    warning_property_reindex,
)
from foundry_lite.domain.ontology.migration_types import (
    OBJECT_REINDEX_COMPLETE,
    OBJECT_REINDEX_REQUIRED,
    OntologyMigrationChange,
    OntologyMigrationPlan,
    complete_object_reindex_config,
    completed_object_reindex,
    object_type_serving_config,
    pending_object_reindex_operation,
    reindex_operation,
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
    assert first.warning_changes[0].to_payload()["details"] == {
        "previousColumn": "source_status",
        "nextColumn": "normalized_status",
    }
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


def test_ontology_migration_direct_change_constructors_keep_operator_evidence() -> None:
    property_row = _property_row("status", "source_status")
    link_row = _link_type_row()
    changes = [
        blocked_object_removed("LegacyObject"),
        blocked_primary_key_changed("Order", "orderId", {"primaryKey": "newOrderId"}),
        warning_object_reindex("Order", "backing"),
        blocked_property_removed("Order", "legacyStatus"),
        blocked_property_type_change("Order", "status", property_row, {"type": "integer"}),
        warning_property_reindex("Order", "status", property_row, {"column": "normalized_status"}),
        blocked_link_removed("LegacyLink"),
        blocked_link_cardinality_changed("OrderCustomer"),
        blocked_link_backing_changed(
            "OrderCustomer",
            link_row["backing"],
            {"dataset": "clean.orders", "fromKey": "order_id", "toKey": "account_id"},
        ),
        blocked_action_removed("LegacyAction"),
        blocked_action_target_changed("ApproveOrder", "Order", "Customer"),
        blocked_parameter_removed("ApproveOrder", "reason"),
        blocked_parameter_type_changed(
            "ApproveOrder",
            "reason",
            {"apiName": "reason", "type": "string", "required": True},
            {"apiName": "reason", "type": "integer", "required": True},
        ),
        blocked_parameter_became_required("ApproveOrder", "reason"),
        warning_parameter_became_optional("ApproveOrder", "reason"),
        warning_optional_parameter_added("ApproveOrder", "comment"),
    ]
    payloads = [change.to_payload() for change in changes]

    assert payloads[0]["kind"] == "object_removed"
    assert payloads[1]["details"] == {"previous": "orderId", "next": "newOrderId"}
    assert payloads[2]["requiresObjectReindex"] is True
    assert payloads[4]["details"] == {"previous": "string", "next": "integer"}
    assert payloads[5]["details"] == {"previousColumn": "source_status", "nextColumn": "normalized_status"}
    assert payloads[8]["details"] == {
        "previous": {"dataset": "clean.orders", "fromKey": "order_id", "toKey": "customer_id"},
        "next": {"dataset": "clean.orders", "fromKey": "order_id", "toKey": "account_id"},
    }
    assert payloads[12]["requiresSdkMajorVersion"] is True
    assert payloads[-1]["status"] == "warning"


def test_ontology_migration_plan_compatible_and_blocked_raise_paths() -> None:
    compatible = OntologyMigrationPlan(None, (), ())
    operation = reindex_operation("Order", ["properties.status", "properties.status", "backing"])
    blocked = OntologyMigrationPlan("ont_1", (blocked_action_removed("ApproveOrder"),), ())
    warning = OntologyMigrationPlan("ont_1", (), (operation,))

    assert compatible.has_changes is False
    assert compatible.status == "compatible"
    assert compatible.sdk_compatibility == "compatible"
    assert "sourceOntologyVersionId" not in compatible.to_payload()
    assert warning.status == "compatible_with_warning"
    assert warning.to_payload()["objectReindexPlan"] == [operation.to_payload()]
    with pytest.raises(ValidationFailed, match="ontology migration requires explicit migration plan"):
        blocked.raise_if_blocked()


def test_ontology_migration_blocks_shape_link_and_action_contract_changes() -> None:
    definition = _definition(
        order_properties=[
            _yaml_property("orderId", "order_id", nullable=False),
            {**_yaml_property("status", "normalized_status"), "type": "integer"},
        ],
        action_parameters=[
            {"apiName": "reason", "type": "integer", "required": False},
            _yaml_parameter("comment", required=False),
        ],
    )
    order = _single_yaml_row(definition, "objectTypes", "Order")
    order["primaryKey"] = "newOrderId"
    order["backing"] = {"dataset": "clean.orders_v2", "mode": "snapshot", "primaryKeyColumns": ["order_id"]}
    link = _single_yaml_row(definition, "linkTypes", "OrderCustomer")
    link["cardinality"] = "one_to_many"
    link["backing"] = {"dataset": "clean.orders", "fromKey": "order_id", "toKey": "account_id"}
    action = _single_yaml_row(definition, "actionTypes", "ApproveOrder")
    action["target"] = "Customer"

    plan = _plan(definition)

    assert plan.status == "blocked"
    assert set(_change_kinds(plan.blocking_changes)) >= {
        "object_primary_key_changed",
        "property_type_changed",
        "link_cardinality_changed",
        "link_backing_changed",
        "action_target_changed",
        "action_parameter_type_changed",
    }
    assert set(_change_kinds(plan.warning_changes)) >= {
        "object_reindex_required",
        "property_mapping_changed",
        "action_parameter_became_optional",
        "optional_action_parameter_added",
    }
    assert plan.reindex_operations[0].changed_fields == ("backing", "properties.status")


def test_ontology_migration_blocks_removed_action_and_duplicate_rows() -> None:
    definition = _definition(action_parameters=[])
    action = _single_yaml_row(definition, "actionTypes", "ApproveOrder")
    action["parameters"] = []

    plan = _plan(definition)

    assert "action_parameter_removed" in _change_kinds(plan.blocking_changes)
    duplicate_object = _object_type_row()
    with pytest.raises(ValidationFailed, match="duplicate persisted ontology apiName"):
        build_ontology_migration_plan(
            source_ontology_version_id="ont_1",
            current_objects={"Order": _object_type_row()},
            current_properties={"Order": (_property_row("orderId", "order_id", nullable=False),)},
            current_links=(_link_type_row(), _link_type_row()),
            current_actions=(_action_row(),),
            definition=_definition(),
        )
    with pytest.raises(ValidationFailed, match="duplicate persisted action apiName"):
        build_ontology_migration_plan(
            source_ontology_version_id="ont_1",
            current_objects={"Order": duplicate_object},
            current_properties={"Order": (_property_row("orderId", "order_id", nullable=False),)},
            current_links=(_link_type_row(),),
            current_actions=(_action_row(), _action_row()),
            definition=_definition(),
        )


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


def _single_yaml_row(definition: dict[str, object], key: str, api_name: str) -> dict[str, object]:
    rows = definition[key]
    assert isinstance(rows, list)
    for row in rows:
        assert isinstance(row, dict)
        if row.get("apiName") == api_name:
            return row
    raise AssertionError(f"missing {key}.{api_name}")


def test_ontology_migration_blocks_removed_object_link_and_action() -> None:
    plan = build_ontology_migration_plan(
        source_ontology_version_id="ont_1",
        current_objects={"LegacyObject": {"api_name": "LegacyObject"}},
        current_properties={},
        current_links=({"api_name": "LegacyLink"},),
        current_actions=({"api_name": "LegacyAction"},),
        definition={},
    )

    assert plan.status == "blocked"
    assert set(_change_kinds(plan.blocking_changes)) == {"object_removed", "link_removed", "action_removed"}


def test_ontology_migration_blocks_parameter_became_required() -> None:
    action = _action_row()
    action["definition"] = {
        "apiName": "ApproveOrder",
        "target": "Order",
        "parameters": (_yaml_parameter("reason", required=False),),
    }
    plan = build_ontology_migration_plan(
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
        current_actions=(action,),
        definition=_definition(action_parameters=[_yaml_parameter("reason", required=True)]),
    )

    assert "action_parameter_became_required" in _change_kinds(plan.blocking_changes)


def test_yaml_rows_by_api_rejects_duplicate_api_name() -> None:
    with pytest.raises(ValidationFailed, match="duplicate object apiName"):
        _yaml_rows_by_api(
            ({"apiName": "Order"}, {"apiName": "Order"}),
            "duplicate object apiName",
            "objectType",
        )


def test_current_action_parameters_rejects_non_list() -> None:
    with pytest.raises(ValidationFailed, match="persisted action parameters must be a list"):
        _current_action_parameters({"api_name": "ApproveOrder", "definition": {"parameters": "not-a-list"}})


def test_parameter_definitions_by_api_rejects_duplicate_api_name() -> None:
    with pytest.raises(ValidationFailed, match="duplicate action parameter apiName"):
        _parameter_definitions_by_api(
            ({"apiName": "reason", "type": "string"}, {"apiName": "reason", "type": "string"})
        )


def test_object_type_backing_falls_back_to_dataset() -> None:
    assert object_type_backing({"backing": {"dataset": "clean.orders"}}) == {"dataset": "clean.orders"}
    assert object_type_backing({"dataset": "clean.orders"}) == {"dataset": "clean.orders"}


def test_link_type_backing_falls_back_to_keys() -> None:
    assert link_type_backing({"backing": {"dataset": "d"}}) == {"dataset": "d"}
    assert link_type_backing({"dataset": "d", "fromKey": "a", "toKey": "b"}) == {
        "dataset": "d",
        "fromKey": "a",
        "toKey": "b",
    }


def test_mapping_sequence_handles_none_and_rejects_non_list() -> None:
    assert mapping_sequence({"objectTypes": None}, "objectTypes") == ()
    with pytest.raises(ValidationFailed, match="objectTypes must be a list"):
        mapping_sequence({"objectTypes": "not-a-list"}, "objectTypes")


def test_property_source_requires_a_value() -> None:
    with pytest.raises(ValidationFailed, match="property source must be set"):
        property_source({"apiName": "status", "source": None})


def test_optional_bool_rejects_non_boolean() -> None:
    with pytest.raises(ValidationFailed, match="deprecated must be a boolean"):
        optional_bool({"deprecated": "yes"}, "deprecated", False)


def test_optional_str_rejects_non_string() -> None:
    with pytest.raises(ValidationFailed, match="column must be a string"):
        optional_str({"column": 5}, "column")


def test_required_str_rejects_missing_value() -> None:
    with pytest.raises(ValidationFailed, match="primaryKey must be set"):
        required_str({}, "primaryKey")


def test_mapping_rejects_non_mapping() -> None:
    with pytest.raises(ValidationFailed, match="expected mapping"):
        _mapping("not-a-mapping")


def test_object_type_serving_config_binds_pending_reindex() -> None:
    operation = reindex_operation("Order", ["backing"])
    assert operation is not None
    plan = OntologyMigrationPlan("ont_1", (), (operation,))

    config = object_type_serving_config(plan, "Order")

    assert config["servingContractStatus"] == OBJECT_REINDEX_REQUIRED
    assert config["sourceOntologyVersionId"] == "ont_1"
    assert config["objectReindexPlan"] == [operation.to_payload()]
    assert object_type_serving_config(plan, "Missing") == {}
    assert "sourceOntologyVersionId" not in object_type_serving_config(
        OntologyMigrationPlan(None, (), (operation,)), "Order"
    )


def test_pending_object_reindex_operation_matches_only_active_key() -> None:
    operation = reindex_operation("Order", ["backing"])
    assert operation is not None
    config = object_type_serving_config(OntologyMigrationPlan("ont_1", (), (operation,)), "Order")

    assert pending_object_reindex_operation(config, operation.reindex_key) == operation.to_payload()
    assert pending_object_reindex_operation(config, "object_reindex:Order:missing") is None
    assert pending_object_reindex_operation({}, operation.reindex_key) is None
    non_list = {"servingContractStatus": OBJECT_REINDEX_REQUIRED, "objectReindexPlan": "not-a-list"}
    assert pending_object_reindex_operation(non_list, operation.reindex_key) is None
    mixed = {
        "servingContractStatus": OBJECT_REINDEX_REQUIRED,
        "objectReindexPlan": ["not-a-mapping", operation.to_payload()],
    }
    assert pending_object_reindex_operation(mixed, operation.reindex_key) == operation.to_payload()


def test_complete_and_read_object_reindex_config() -> None:
    operation = reindex_operation("Order", ["backing"])
    assert operation is not None
    config = object_type_serving_config(OntologyMigrationPlan("ont_1", (), (operation,)), "Order")

    completed_config = complete_object_reindex_config(
        config,
        operation.to_payload(),
        index_run_id="run_1",
        dataset_version_id="dsv_1",
        completed_at="2026-07-02T00:00:00Z",
    )

    assert completed_config["servingContractStatus"] == OBJECT_REINDEX_COMPLETE
    evidence = completed_object_reindex(completed_config, operation.reindex_key)
    assert evidence is not None
    assert evidence["indexRunId"] == "run_1"
    assert evidence["changedFields"] == list(operation.changed_fields)
    assert completed_object_reindex(completed_config, "object_reindex:Order:missing") is None
    assert completed_object_reindex({}, operation.reindex_key) is None
    assert completed_object_reindex({"servingContractStatus": OBJECT_REINDEX_COMPLETE}, operation.reindex_key) is None


def test_complete_object_reindex_config_handles_missing_changed_fields() -> None:
    completed_config = complete_object_reindex_config(
        {},
        {"reindexKey": "object_reindex:Order:abc"},
        index_run_id="run_1",
        dataset_version_id="dsv_1",
        completed_at="2026-07-02T00:00:00Z",
    )

    evidence = completed_config["objectReindexCompleted"]
    assert isinstance(evidence, dict)
    assert evidence["changedFields"] == []


def test_raise_if_blocked_is_a_noop_when_compatible() -> None:
    OntologyMigrationPlan(None, (), ()).raise_if_blocked()


def test_change_constructors_tolerate_missing_optional_and_required_fields() -> None:
    reindex = warning_property_reindex("Order", "status", {"column_name": "source_status"}, {})
    primary_key = blocked_primary_key_changed("Order", "orderId", {})

    assert reindex.details["nextColumn"] is None
    assert primary_key.details["next"] == ""
