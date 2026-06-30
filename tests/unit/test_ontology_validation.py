from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.ontology_repository import (
    ActionTypeRow,
    LinkTypeRow,
    ObjectTypeRow,
    PropertyTypeRow,
)
from foundry_lite.application.services.ontology_migration_types import OntologyMigrationPlan, reindex_operation
from foundry_lite.application.services.ontology_service import OntologyService
from foundry_lite.application.services.ontology_validation import (
    ontology_validation_result,
    validate_ontology_definition,
    validate_persisted_link,
    validate_persisted_object_type,
)
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed


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


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("type", "currency", "unsupported property type"),
        ("source", "mirror", "unsupported property source"),
        ("editPolicy", "last_write_wins", "unsupported edit policy"),
        ("classification", "Finance", "unsupported classification"),
    ],
)
def test_ontology_definition_validation_rejects_unknown_property_enums(
    field: str,
    value: str,
    message: str,
) -> None:
    definition = _yaml_definition(primary_key_column="order_id")
    _status_property(definition)[field] = value

    with pytest.raises(ValidationFailed, match=message) as exc_info:
        validate_ontology_definition(FakeTransaction(), RequestContext(), definition, _dataset_columns)

    assert exc_info.value.details["property"] == "status"
    assert value not in exc_info.value.details["allowed"]


def test_ontology_definition_validation_rejects_unknown_link_cardinality() -> None:
    definition = _yaml_definition(primary_key_column="order_id")
    definition["linkTypes"] = [
        {
            "apiName": "OrderCustomer",
            "from": "Order",
            "to": "Order",
            "cardinality": "belongs_to",
            "backing": {"dataset": "clean.orders", "fromKey": "order_id", "toKey": "customer_id"},
        }
    ]

    with pytest.raises(ValidationFailed, match="unsupported link cardinality") as exc_info:
        validate_ontology_definition(FakeTransaction(), RequestContext(), definition, _dataset_columns)

    assert exc_info.value.details["linkType"] == "OrderCustomer"


def test_ontology_definition_validation_rejects_unknown_backing_and_action_enums() -> None:
    for mutation in (_set_object_backing_mode, _set_cdc_delete_policy, _set_action_parameter_type, _set_mutation_type):
        definition = _yaml_definition(primary_key_column="order_id")
        expected_message = mutation(definition)
        with pytest.raises(ValidationFailed, match=expected_message):
            validate_ontology_definition(FakeTransaction(), RequestContext(), definition, _dataset_columns)


def test_ontology_apply_rejects_duplicate_object_api_name_before_persisting(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ontology_path = tmp_path / "duplicate-ontology.yaml"
    ontology_path.write_text(
        """
objectTypes:
  - apiName: Order
    primaryKey: orderId
    backing: {dataset: clean.orders}
    properties:
      - {apiName: orderId, type: string, column: order_id}
  - apiName: Order
    primaryKey: orderId
    backing: {dataset: clean.orders}
    properties:
      - {apiName: orderId, type: string, column: order_id}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationFailed, match="duplicate object apiName") as exc_info:
        foundry.ontology.apply(ontology_path, ctx=demo_admin_context())

    assert exc_info.value.details == {"objectType": "Order"}


def test_ontology_definition_validation_rejects_duplicate_link_api_name() -> None:
    definition = _yaml_definition(primary_key_column="order_id")
    definition["linkTypes"] = [
        {
            "apiName": "OrderCustomer",
            "from": "Order",
            "to": "Order",
            "backing": {"dataset": "clean.orders", "fromKey": "order_id", "toKey": "customer_id"},
        },
        {
            "apiName": "OrderCustomer",
            "from": "Order",
            "to": "Order",
            "backing": {"dataset": "clean.orders", "fromKey": "order_id", "toKey": "customer_id"},
        },
    ]

    with pytest.raises(ValidationFailed, match="duplicate link apiName") as exc_info:
        validate_ontology_definition(FakeTransaction(), RequestContext(), definition, _dataset_columns)

    assert exc_info.value.details == {"linkType": "OrderCustomer"}


def test_ontology_definition_validation_rejects_duplicate_action_api_name() -> None:
    definition = _yaml_definition(primary_key_column="order_id")
    action = _first_action(definition)
    definition["actionTypes"] = [action, dict(action)]

    with pytest.raises(ValidationFailed, match="duplicate action apiName") as exc_info:
        validate_ontology_definition(FakeTransaction(), RequestContext(), definition, _dataset_columns)

    assert exc_info.value.details == {"actionType": "ApproveOrder"}


@pytest.mark.parametrize(
    ("mutate_definition", "message", "details"),
    [
        (
            lambda definition: _append_case_variant_object_type(definition),
            "duplicate object apiName",
            {"objectType": "order", "conflictsWith": "Order"},
        ),
        (
            lambda definition: _append_case_variant_property(definition),
            "duplicate property apiName",
            {"property": "STATUS", "conflictsWith": "status"},
        ),
        (
            lambda definition: _append_case_variant_link_type(definition),
            "duplicate link apiName",
            {"linkType": "ordercustomer", "conflictsWith": "OrderCustomer"},
        ),
        (
            lambda definition: _append_case_variant_action_type(definition),
            "duplicate action apiName",
            {"actionType": "approveorder", "conflictsWith": "ApproveOrder"},
        ),
    ],
)
def test_ontology_definition_validation_rejects_case_insensitive_duplicate_api_names(
    mutate_definition: Callable[[dict[str, object]], None],
    message: str,
    details: dict[str, object],
) -> None:
    definition = _yaml_definition(primary_key_column="order_id")
    mutate_definition(definition)

    with pytest.raises(ValidationFailed, match=message) as exc_info:
        validate_ontology_definition(FakeTransaction(), RequestContext(), definition, _dataset_columns)

    assert exc_info.value.details == details


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


def test_ontology_activation_cas_conflict_reports_lost_draft_state() -> None:
    service = OntologyService(engine=object(), ontology_repository=_ActivationConflictRepository())

    with pytest.raises(ConflictDetected, match="lost its draft state") as exc_info:
        service._activate_ontology_version(FakeTransaction(), demo_admin_context(), "ont_candidate")

    assert exc_info.value.details == {"ontology_version_id": "ont_candidate"}


def test_ontology_activation_evidence_includes_migration_plan_payload() -> None:
    runtime = _RecordingRuntimeService()
    service = OntologyService(engine=object(), ontology_repository=_RecordingOntologyRepository())
    service.bind_collaborators(
        {
            "dataset_registry_service": object(),
            "dataset_version_service": object(),
            "runtime_service": runtime,
        }
    )
    operation = reindex_operation("Order", ["backing"])
    assert operation is not None

    service._record_ontology_activation(
        FakeTransaction(),
        demo_admin_context(),
        "ont_candidate",
        2,
        OntologyMigrationPlan("ont_active", (), (operation,)),
    )

    assert runtime.outbox_payloads[0]["ontologyMigration"]["sourceOntologyVersionId"] == "ont_active"
    assert runtime.audit_after_refs[0]["ontologyMigration"]["objectReindexPlan"]


def test_ontology_import_rejects_duplicate_property_at_persistence_boundary() -> None:
    service = OntologyService(engine=object(), ontology_repository=_RecordingOntologyRepository())

    with pytest.raises(ValidationFailed, match="duplicate property apiName") as exc_info:
        service._import_properties_for_object_type(
            FakeTransaction(),
            demo_admin_context(),
            "otype_Order",
            {
                "properties": [
                    {"apiName": "status", "type": "string", "column": "source_status"},
                    {"apiName": "status", "type": "string", "column": "source_status"},
                ]
            },
        )

    assert exc_info.value.details == {"property": "status"}


def test_ontology_import_rejects_null_property_source() -> None:
    service = OntologyService(engine=object(), ontology_repository=_RecordingOntologyRepository())

    with pytest.raises(ValidationFailed, match="property source must be set") as exc_info:
        service._insert_property_type(
            FakeTransaction(),
            demo_admin_context(),
            "otype_Order",
            {"apiName": "manualStatus", "type": "string", "source": None},
        )

    assert exc_info.value.details == {"property": "manualStatus"}


def test_ontology_import_rejects_unknown_link_and_action_targets() -> None:
    service = OntologyService(engine=object(), ontology_repository=_RecordingOntologyRepository())

    with pytest.raises(ValidationFailed, match="link references unknown object type"):
        service._import_link_types(
            FakeTransaction(),
            demo_admin_context(),
            "ont_candidate",
            {"linkTypes": [{"apiName": "OrderCustomer", "from": "Order", "to": "Customer"}]},
            {"Order": "otype_Order"},
        )
    with pytest.raises(ValidationFailed, match="action target object type not found"):
        service._import_action_types(
            FakeTransaction(),
            demo_admin_context(),
            "ont_candidate",
            {"actionTypes": [{"apiName": "ApproveOrder", "target": "Customer"}]},
            {"Order": "otype_Order"},
        )


class _ActivationConflictRepository:
    def archive_active_ontology_versions(self, **_kwargs: object) -> int:
        return 1

    def activate_ontology_version(self, **_kwargs: object) -> bool:
        return False


class _RecordingOntologyRepository:
    def __init__(self) -> None:
        self.property_records: list[object] = []

    def insert_property_type(self, **kwargs: object) -> None:
        self.property_records.append(kwargs["record"])


class _RecordingRuntimeService:
    def __init__(self) -> None:
        self.outbox_payloads: list[dict[str, object]] = []
        self.audit_after_refs: list[dict[str, object]] = []

    def _outbox(self, *_args: object, **kwargs: object) -> None:
        payload = _args[5]
        assert isinstance(payload, dict)
        self.outbox_payloads.append(payload)
        assert kwargs["idempotency_key"] == "ont_candidate"

    def _audit(self, *_args: object, **kwargs: object) -> None:
        after_ref = kwargs["after_ref"]
        assert isinstance(after_ref, dict)
        self.audit_after_refs.append(after_ref)


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


def _first_object_type(definition: dict[str, object]) -> dict[str, object]:
    object_types = definition["objectTypes"]
    assert isinstance(object_types, list)
    item = object_types[0]
    assert isinstance(item, dict)
    return item


def _status_property(definition: dict[str, object]) -> dict[str, object]:
    properties = _first_object_type(definition)["properties"]
    assert isinstance(properties, list)
    item = properties[1]
    assert isinstance(item, dict)
    return item


def _first_action(definition: dict[str, object]) -> dict[str, object]:
    action_types = definition["actionTypes"]
    assert isinstance(action_types, list)
    item = action_types[0]
    assert isinstance(item, dict)
    return item


def _append_case_variant_object_type(definition: dict[str, object]) -> None:
    object_types = definition["objectTypes"]
    assert isinstance(object_types, list)
    object_types.append(
        {
            "apiName": "order",
            "primaryKey": "orderId",
            "backing": {"dataset": "clean.orders"},
            "properties": [{"apiName": "orderId", "type": "string", "column": "order_id"}],
        }
    )


def _append_case_variant_property(definition: dict[str, object]) -> None:
    properties = _first_object_type(definition)["properties"]
    assert isinstance(properties, list)
    properties.append({"apiName": "STATUS", "type": "string", "column": "source_status"})


def _append_case_variant_link_type(definition: dict[str, object]) -> None:
    definition["linkTypes"] = [
        {
            "apiName": "OrderCustomer",
            "from": "Order",
            "to": "Order",
            "backing": {"dataset": "clean.orders", "fromKey": "order_id", "toKey": "customer_id"},
        },
        {
            "apiName": "ordercustomer",
            "from": "Order",
            "to": "Order",
            "backing": {"dataset": "clean.orders", "fromKey": "order_id", "toKey": "customer_id"},
        },
    ]


def _append_case_variant_action_type(definition: dict[str, object]) -> None:
    action_types = definition["actionTypes"]
    assert isinstance(action_types, list)
    action = _first_action(definition)
    duplicate = dict(action)
    duplicate["apiName"] = "approveorder"
    action_types.append(duplicate)


def _set_object_backing_mode(definition: dict[str, object]) -> str:
    backing = _first_object_type(definition)["backing"]
    assert isinstance(backing, dict)
    backing["mode"] = "streaming"
    return "unsupported object backing mode"


def _set_cdc_delete_policy(definition: dict[str, object]) -> str:
    backing = _first_object_type(definition)["backing"]
    assert isinstance(backing, dict)
    backing["cdc"] = {"dataset": "clean.orders", "deletePolicy": "hard_delete"}
    return "unsupported cdc delete policy"


def _set_action_parameter_type(definition: dict[str, object]) -> str:
    _first_action(definition)["parameters"] = [{"apiName": "reason", "type": "json"}]
    return "unsupported action parameter type"


def _set_mutation_type(definition: dict[str, object]) -> str:
    action = _first_action(definition)
    mutations = action["mutations"]
    assert isinstance(mutations, list)
    mutation = mutations[0]
    assert isinstance(mutation, dict)
    mutation["type"] = "increment"
    return "unsupported action mutation type"


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
