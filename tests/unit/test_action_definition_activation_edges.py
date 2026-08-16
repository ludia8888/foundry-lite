from __future__ import annotations

import copy

import pytest
from foundry_lite.application.services.action_definition_validation import validate_yaml_action_definitions
from foundry_lite.domain.errors import ValidationFailed


def _property(
    name: str,
    data_type: str = "string",
    *,
    is_editable: bool = True,
    **metadata: object,
) -> dict[str, object]:
    return {"apiName": name, "type": data_type, "editable": is_editable, **metadata}


def _object(name: str, properties: list[dict[str, object]], *interfaces: str) -> dict[str, object]:
    return {"apiName": name, "properties": properties, "implements": list(interfaces)}


def _action(
    *,
    target: str = "Order",
    target_kind: str = "object",
    risk: str = "high",
) -> dict[str, object]:
    return {
        "apiName": "UpdateOrder",
        "contractVersion": 3,
        "target": target,
        "targetKind": target_kind,
        "riskLevel": risk,
        "permissions": {"allowedRoles": ["ops_manager"]},
        "parameters": [{"apiName": "value", "type": "string"}],
        "rules": [
            {
                "kind": "modifyObject",
                "ruleId": "update",
                "objectType": target,
                "target": {"kind": "parameter", "parameter": "__target__"},
                "assignments": [{"property": "status", "value": {"kind": "parameter", "parameter": "value"}}],
            }
        ],
    }


def _definition(action: dict[str, object], **resources: object) -> dict[str, object]:
    return {"actionTypes": [action], **resources}


def test_activation_rejects_missing_object_and_interface_targets() -> None:
    with pytest.raises(ValidationFailed, match="target reference was not found"):
        validate_yaml_action_definitions(_definition(_action()), {}, {})

    interface_action = _action(target="Asset", target_kind="interface")
    with pytest.raises(ValidationFailed, match="target reference was not found"):
        validate_yaml_action_definitions(_definition(interface_action), {}, {})


def test_activation_rejects_invalid_compensation_configuration() -> None:
    base = _action()
    object_defs = {"Order": _object("Order", [_property("status")])}
    compensation = {**_action(), "apiName": "CompensateOrder", "rules": []}

    blank = {**base, "revert": {"enabled": True, "compensationAction": " "}}
    with pytest.raises(ValidationFailed, match="non-empty"):
        validate_yaml_action_definitions({"actionTypes": [blank, compensation]}, object_defs, {})

    disabled = {**base, "revert": {"enabled": False, "compensationAction": "CompensateOrder"}}
    with pytest.raises(ValidationFailed, match="requires revert to be enabled"):
        validate_yaml_action_definitions({"actionTypes": [disabled, compensation]}, object_defs, {})


def test_submission_criteria_rejects_missing_target_property_and_link_type() -> None:
    action = _action()
    action["submissionCriteria"] = {
        "op": "eq",
        "left": {"kind": "objectProperty", "property": "missing"},
        "right": {"kind": "literal", "value": "OPEN"},
    }
    objects = {"Order": _object("Order", [_property("status")])}
    with pytest.raises(ValidationFailed, match="target property was not found"):
        validate_yaml_action_definitions(_definition(action), objects, {})

    action["submissionCriteria"] = {
        "op": "contains",
        "left": {
            "kind": "linkedObjectProperty",
            "linkType": "OrderCustomer",
            "direction": "outgoing",
            "property": "tier",
        },
        "right": {"kind": "literal", "value": "gold"},
    }
    with pytest.raises(ValidationFailed, match="link type was not found"):
        validate_yaml_action_definitions(_definition(action), objects, {})


def test_linked_criteria_rejects_missing_endpoints_reverse_anchor_and_linked_property() -> None:
    action = _action()
    action["submissionCriteria"] = {
        "op": "contains",
        "left": {
            "kind": "linkedObjectProperty",
            "linkType": "OrderCustomer",
            "direction": "outgoing",
            "property": "tier",
        },
        "right": {"kind": "literal", "value": "gold"},
    }
    objects = {
        "Order": _object("Order", [_property("status")]),
        "Customer": _object("Customer", [_property("tier")]),
    }
    missing_endpoint = {"OrderCustomer": {"apiName": "OrderCustomer", "from": "Order", "to": "Missing"}}
    with pytest.raises(ValidationFailed, match="endpoint was not found"):
        validate_yaml_action_definitions(_definition(action), objects, missing_endpoint)

    reverse = copy.deepcopy(action)
    reverse["submissionCriteria"]["left"]["direction"] = "incoming"  # type: ignore[index]
    links = {"OrderCustomer": {"apiName": "OrderCustomer", "from": "Order", "to": "Customer"}}
    with pytest.raises(ValidationFailed, match="not anchored"):
        validate_yaml_action_definitions(_definition(reverse), objects, links)

    missing_property = copy.deepcopy(action)
    missing_property["submissionCriteria"]["left"]["property"] = "missing"  # type: ignore[index]
    with pytest.raises(ValidationFailed, match="linked property was not found"):
        validate_yaml_action_definitions(_definition(missing_property), objects, links)


def test_function_edit_rule_requires_a_declared_function() -> None:
    action = _action()
    action["rules"] = [
        {
            "kind": "functionEdit",
            "ruleId": "compute",
            "functionApiName": "missing",
            "functionVersion": "1.0.0",
        }
    ]
    objects = {"Order": _object("Order", [])}

    with pytest.raises(ValidationFailed, match="function rule reference was not found"):
        validate_yaml_action_definitions(_definition(action), objects, {})

    definition = _definition(action, functionTypes=[{"apiName": "calculate", "version": "1.0.0"}])
    action["rules"][0]["functionApiName"] = "calculate"  # type: ignore[index]
    validate_yaml_action_definitions(definition, objects, {})


def test_concrete_object_rule_requires_existing_type_editable_property_and_compatible_parameter() -> None:
    action = _action()
    objects = {"Order": _object("Order", [_property("status")])}
    action["rules"][0]["objectType"] = "Missing"  # type: ignore[index]
    with pytest.raises(ValidationFailed, match="object type reference was not found"):
        validate_yaml_action_definitions(_definition(action), objects, {})

    action = _action()
    action["rules"][0]["assignments"][0]["property"] = "missing"  # type: ignore[index]
    with pytest.raises(ValidationFailed, match="assignment property was not found"):
        validate_yaml_action_definitions(_definition(action), objects, {})

    action = _action()
    readonly = {"Order": _object("Order", [_property("status", is_editable=False)])}
    with pytest.raises(ValidationFailed, match="must be editable"):
        validate_yaml_action_definitions(_definition(action), readonly, {})

    action = _action()
    action["parameters"][0]["type"] = "boolean"  # type: ignore[index]
    with pytest.raises(ValidationFailed, match="parameter type does not match"):
        validate_yaml_action_definitions(_definition(action), objects, {})


@pytest.mark.parametrize(
    ("property_type", "literal"),
    [("boolean", "true"), ("integer", True), ("float", True), ("string", 42)],
)
def test_literal_assignment_rejects_python_bool_number_aliases(
    property_type: str,
    literal: object,
) -> None:
    action = _action()
    action["rules"][0]["assignments"][0]["value"] = {"kind": "literal", "value": literal}  # type: ignore[index]
    objects = {"Order": _object("Order", [_property("status", property_type)])}

    with pytest.raises(ValidationFailed, match="literal type does not match"):
        validate_yaml_action_definitions(_definition(action), objects, {})


@pytest.mark.parametrize(
    ("property_type", "literal"),
    [
        ("date", "2026-02-30"),
        ("timestamp", "2026-08-13T12:00:00"),
        ("float", float("nan")),
        ("float", float("inf")),
    ],
)
def test_literal_assignment_rejects_invalid_temporal_and_non_finite_values(
    property_type: str,
    literal: object,
) -> None:
    action = _action()
    action["rules"][0]["assignments"][0]["value"] = {"kind": "literal", "value": literal}  # type: ignore[index]
    objects = {"Order": _object("Order", [_property("status", property_type)])}

    with pytest.raises(ValidationFailed, match="literal type does not match"):
        validate_yaml_action_definitions(_definition(action), objects, {})


def test_non_nullable_property_rejects_null_literal_and_temporal_properties_require_typed_parameters() -> None:
    action = _action()
    action["rules"][0]["assignments"][0]["value"] = {"kind": "literal", "value": None}  # type: ignore[index]
    objects = {"Order": _object("Order", [_property("status", nullable=False)])}
    with pytest.raises(ValidationFailed, match="literal type does not match"):
        validate_yaml_action_definitions(_definition(action), objects, {})

    action = _action()
    temporal_objects = {"Order": _object("Order", [_property("status", "timestamp")])}
    with pytest.raises(ValidationFailed, match="parameter type does not match"):
        validate_yaml_action_definitions(_definition(action), temporal_objects, {})

    action["parameters"][0]["type"] = "timestamp"  # type: ignore[index]
    validate_yaml_action_definitions(_definition(action), temporal_objects, {})


def test_sensitive_property_requires_explicit_high_risk() -> None:
    action = _action(risk="medium")
    objects = {"Order": _object("Order", [_property("status", classification="pii")])}

    with pytest.raises(ValidationFailed, match="sensitive property edits require high"):
        validate_yaml_action_definitions(_definition(action), objects, {})

    action["riskLevel"] = "high"
    validate_yaml_action_definitions(_definition(action), objects, {})


@pytest.mark.parametrize(
    ("property_type", "allow_multiple", "parameter_type", "item_type", "expected"),
    [
        ("media_reference", False, "array", "media", "accepts one"),
        ("attachment", False, "array", "attachment", "multiplicity"),
        ("attachment", True, "attachment", None, "multiplicity"),
    ],
)
def test_media_assignment_requires_matching_single_or_collection_shape(
    property_type: str,
    allow_multiple: bool,
    parameter_type: str,
    item_type: str | None,
    expected: str,
) -> None:
    action = _action()
    parameter = {
        "apiName": "value",
        "type": parameter_type,
        "mediaSet": "legal.receipts",
    }
    if item_type is not None:
        parameter["itemType"] = item_type
    action["parameters"] = [parameter]
    objects = {
        "Order": _object(
            "Order",
            [
                _property(
                    "status",
                    property_type,
                    mediaSet="legal.receipts",
                    allowMultiple=allow_multiple,
                )
            ],
        )
    }

    with pytest.raises(ValidationFailed, match=expected):
        validate_yaml_action_definitions(_definition(action), objects, {})


def test_media_assignment_requires_the_property_media_set() -> None:
    action = _action()
    action["parameters"] = [
        {"apiName": "value", "type": "media", "mediaSet": "other.uploads"},
    ]
    objects = {
        "Order": _object(
            "Order",
            [_property("status", "media_reference", mediaSet="legal.receipts")],
        )
    }

    with pytest.raises(ValidationFailed, match="must use the property Media Set"):
        validate_yaml_action_definitions(_definition(action), objects, {})


def _interface_definition() -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    interface = {
        "apiName": "Asset",
        "properties": [_property("riskScore", "float")],
        "linkConstraints": [{"apiName": "customer"}],
    }
    objects = {
        "Order": _object("Order", [_property("riskScore", "float"), _property("privateNote")], "Asset"),
        "Customer": _object("Customer", [_property("name")]),
    }
    return interface, objects


def test_interface_action_rejects_missing_declaration_and_non_shared_assignment() -> None:
    action = _action(target="Asset", target_kind="interface")
    action["parameters"][0]["type"] = "float"  # type: ignore[index]
    action["rules"][0].update({"objectType": "Order", "onInterface": "Wrong"})  # type: ignore[index]
    action["rules"][0]["assignments"][0]["property"] = "riskScore"  # type: ignore[index]
    interface, objects = _interface_definition()
    definition = _definition(action, interfaces=[interface])

    with pytest.raises(ValidationFailed, match="must declare the target interface"):
        validate_yaml_action_definitions(definition, objects, {})

    action["rules"][0]["onInterface"] = "Asset"  # type: ignore[index]
    action["rules"][0]["assignments"][0]["property"] = "privateNote"  # type: ignore[index]
    with pytest.raises(ValidationFailed, match="shared interface properties only"):
        validate_yaml_action_definitions(definition, objects, {})


def test_interface_generic_create_requires_primary_key_and_shared_properties() -> None:
    action = _action(target="Asset", target_kind="interface")
    action["parameters"][0]["type"] = "float"  # type: ignore[index]
    action["rules"] = [
        {
            "kind": "createObject",
            "ruleId": "create",
            "objectType": "Asset",
            "onInterface": "Asset",
            "assignments": [{"property": "riskScore", "value": {"kind": "parameter", "parameter": "value"}}],
        }
    ]
    interface, objects = _interface_definition()
    definition = _definition(action, interfaces=[interface])

    with pytest.raises(ValidationFailed, match="explicit primary key"):
        validate_yaml_action_definitions(definition, objects, {})

    action["rules"][0]["primaryKey"] = {"kind": "parameter", "parameter": "__target__"}  # type: ignore[index]
    action["rules"][0]["assignments"][0]["property"] = "privateNote"  # type: ignore[index]
    with pytest.raises(ValidationFailed, match="shared interface properties only"):
        validate_yaml_action_definitions(definition, objects, {})


def test_interface_link_rule_requires_exact_constraint_without_concrete_link() -> None:
    action = _action(target="Asset", target_kind="interface")
    action["rules"] = [
        {
            "kind": "createLink",
            "ruleId": "link",
            "onInterface": "Wrong",
            "interfaceLinkConstraint": "customer",
            "source": {"kind": "parameter", "parameter": "__target__"},
            "target": {"kind": "parameter", "parameter": "value"},
        }
    ]
    interface, objects = _interface_definition()
    definition = _definition(action, interfaces=[interface])
    with pytest.raises(ValidationFailed, match="must bind the Action target interface"):
        validate_yaml_action_definitions(definition, objects, {})

    action["rules"][0]["onInterface"] = "Asset"  # type: ignore[index]
    action["rules"][0]["linkType"] = "AssetCustomer"  # type: ignore[index]
    with pytest.raises(ValidationFailed, match="cannot also declare"):
        validate_yaml_action_definitions(definition, objects, {})

    del action["rules"][0]["linkType"]  # type: ignore[index]
    action["rules"][0]["interfaceLinkConstraint"] = "missing"  # type: ignore[index]
    with pytest.raises(ValidationFailed, match="constraint reference was not found"):
        validate_yaml_action_definitions(definition, objects, {})
