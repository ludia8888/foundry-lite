"""Canonical Action Contract v3 normalization and parameter semantics."""

from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest
from foundry_lite.application.services.action_definition_validation import validate_yaml_action_definitions
from foundry_lite.application.services.action_media_parameters import action_media_parameter
from foundry_lite.domain.action_runtime.action_conditions import (
    StaticActionConditionContext,
    evaluate_action_condition,
    referenced_linked_object_properties,
)
from foundry_lite.domain.action_runtime.action_contract import (
    ACTION_PARAMETER_TYPES,
    action_contract_fingerprint,
    action_contract_payload,
    action_parameter_json_schema,
    compile_action_contract,
    compile_action_contract_snapshot,
)
from foundry_lite.domain.action_runtime.action_effects import action_effect_payload
from foundry_lite.domain.action_runtime.action_parameters import (
    ActionParameterContext,
    parameter_config_payload,
    resolve_action_parameters,
)
from foundry_lite.domain.action_runtime.action_permissions import can_access_action, require_action_access
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, PermissionDenied, ValidationFailed


def _condition(parameter: str, value: object) -> dict[str, object]:
    return {
        "op": "eq",
        "left": {"kind": "parameter", "parameter": parameter},
        "right": {"kind": "literal", "value": value},
    }


def _definition(parameters: list[dict[str, object]]) -> dict[str, object]:
    return {
        "contractVersion": 3,
        "apiName": "UpdateOrder",
        "displayName": "Update order",
        "target": "Order",
        "parameters": parameters,
        "rules": [
            {
                "kind": "modifyObject",
                "ruleId": "update",
                "objectType": "Order",
                "target": {"kind": "parameter", "parameter": "order"},
                "assignments": [],
            }
        ],
    }


def test_all_v3_parameter_types_generate_one_deterministic_schema() -> None:
    parameters: list[dict[str, object]] = []
    for data_type in sorted(ACTION_PARAMETER_TYPES):
        parameter: dict[str, object] = {"apiName": f"p_{data_type}", "type": data_type}
        if data_type in {"array", "objectSet"}:
            parameter["itemType"] = "string"
        if data_type in {"media", "attachment"}:
            parameter["mediaSet"] = "action.uploads"
        if data_type == "struct":
            parameter["fields"] = [{"apiName": "label", "type": "string", "required": True}]
        parameters.append(parameter)

    first = compile_action_contract(_definition(parameters))
    second = compile_action_contract(_definition(parameters))
    schema = action_parameter_json_schema(first)

    assert action_contract_payload(first) == action_contract_payload(second)
    assert action_contract_fingerprint(first) == action_contract_fingerprint(second)
    assert schema["x-foundry-contract-fingerprint"] == action_contract_fingerprint(first)
    assert set(schema["properties"]) == {item["apiName"] for item in parameters}  # type: ignore[arg-type]
    assert schema["properties"]["p_decimal"] == {  # type: ignore[index]
        "type": "string",
        "format": "decimal",
        "pattern": r"^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?$",
        "x-foundry-parameter-config": {
            "apiName": "p_decimal",
            "type": "decimal",
            "required": False,
            "description": None,
            "default": None,
            "constraints": {},
            "overrides": [],
        },
    }
    assert schema["properties"]["p_timestamp"]["format"] == "date-time"  # type: ignore[index]


@pytest.mark.parametrize(
    ("data_type", "constraints", "message"),
    [
        ("string", {"type": "number"}, "unsupported action parameter constraints"),
        ("string", {"minimum": 1}, "unsupported action parameter constraints"),
        ("integer", {"minLength": 1}, "unsupported action parameter constraints"),
        ("integer", {"minimum": 1.5}, "wrong type"),
        ("decimal", {"minimum": 1}, "wrong type"),
        ("string", {"minLength": True}, "non-negative integer"),
        ("string", {"minLength": 4, "maxLength": 3}, "minimum cannot exceed maximum"),
        ("string", {"enum": []}, "non-empty list"),
        ("string", {"enum": ["a", "a"]}, "duplicate"),
        ("float", {"enum": [1, 1.0]}, "duplicate"),
        ("decimal", {"enum": [1.5]}, "wrong type"),
    ],
)
def test_parameter_constraints_cannot_override_schema_or_use_ambiguous_types(
    data_type: str, constraints: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationFailed, match=message):
        compile_action_contract(_definition([{"apiName": "value", "type": data_type, "constraints": constraints}]))


def test_override_constraints_are_compiled_before_runtime_condition_matching() -> None:
    definition = _definition(
        [
            {"apiName": "mode", "type": "string"},
            {
                "apiName": "amount",
                "type": "decimal",
                "overrides": [
                    {
                        "when": _condition("mode", "strict"),
                        "config": {"constraints": {"type": "number"}},
                    }
                ],
            },
        ]
    )

    with pytest.raises(ValidationFailed, match="unsupported action parameter constraints"):
        compile_action_contract(definition)


def test_decimal_schema_preserves_exact_bounds_as_server_owned_extensions() -> None:
    contract = compile_action_contract(
        _definition(
            [
                {
                    "apiName": "amount",
                    "type": "decimal",
                    "constraints": {"minimum": "0.000000000000000001", "maximum": "999999999999999999.99"},
                }
            ]
        )
    )
    schema = action_parameter_json_schema(contract)["properties"]["amount"]  # type: ignore[index]

    assert schema["type"] == "string"
    assert schema["x-foundry-decimal-minimum"] == "0.000000000000000001"
    assert schema["x-foundry-decimal-maximum"] == "999999999999999999.99"
    assert "minimum" not in schema and "maximum" not in schema


@pytest.mark.parametrize(
    ("parameter", "message"),
    [
        ({"apiName": "amount", "type": "decimal", "default": {"kind": "literal", "value": 1.5}}, "wrong type"),
        (
            {
                "apiName": "amount",
                "type": "decimal",
                "default": {"kind": "literal", "value": "0.5"},
                "constraints": {"minimum": "1"},
            },
            "constraint failed",
        ),
        ({"apiName": "day", "type": "date", "default": {"kind": "currentTime", "unit": "timestamp"}}, "does not match"),
        (
            {"apiName": "id", "type": "integer", "default": {"kind": "generatedId", "strategy": "uuid"}},
            "string parameter",
        ),
        (
            {"apiName": "actor", "type": "string", "default": {"kind": "currentUser", "attribute": " "}},
            "non-empty text",
        ),
        (
            {
                "apiName": "actor",
                "type": "string",
                "default": {"kind": "literal", "value": "u-1", "typo": True},
            },
            "default fields",
        ),
        (
            {
                "apiName": "amounts",
                "type": "array",
                "itemType": "decimal",
                "default": {"kind": "literal", "value": [0.1]},
            },
            "wrong type",
        ),
        (
            {
                "apiName": "references",
                "type": "objectSet",
                "itemType": "string",
                "default": {"kind": "literal", "value": ["O-1", "O-1"]},
            },
            "wrong type",
        ),
        (
            {
                "apiName": "guest",
                "type": "struct",
                "fields": [{"apiName": "name", "type": "string", "required": True}],
                "default": {"kind": "literal", "value": {}},
            },
            "wrong type",
        ),
    ],
)
def test_parameter_defaults_fail_during_contract_compilation_not_first_use(
    parameter: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationFailed, match=message):
        compile_action_contract(_definition([parameter]))


def test_nested_decimal_enum_is_checked_against_the_recursive_parameter_type() -> None:
    parameter = {
        "apiName": "amounts",
        "type": "array",
        "itemType": "decimal",
        "constraints": {"enum": [[0.1], ["0.20"]]},
    }

    with pytest.raises(ValidationFailed, match="wrong type"):
        compile_action_contract(_definition([parameter]))


def test_first_class_action_roles_are_independent_and_legacy_apply_is_normalized() -> None:
    definition = _definition([])
    definition["permissions"] = {
        "viewRoles": ["viewer", "ops_manager", "viewer"],
        "editRoles": ["data_engineer"],
        "applyRoles": ["ops_manager"],
    }
    contract = compile_action_contract(definition)

    assert contract.permissions["viewRoles"] == ["ops_manager", "viewer"]
    assert can_access_action(RequestContext(roles=("viewer",)), contract.permissions, "view") is True
    assert can_access_action(RequestContext(roles=("viewer",)), contract.permissions, "edit") is False
    assert can_access_action(RequestContext(roles=("data_engineer",)), contract.permissions, "edit") is True
    assert can_access_action(RequestContext(roles=("ops_manager",)), contract.permissions, "apply") is True
    assert can_access_action(RequestContext(roles=("admin",)), contract.permissions, "apply") is True
    with pytest.raises(PermissionDenied, match="permission denied to apply"):
        require_action_access(RequestContext(roles=("viewer",)), contract.api_name, contract.permissions, "apply")

    legacy = _definition([])
    legacy["permissions"] = {"allowedRoles": ["ops_manager"]}
    assert compile_action_contract(legacy).permissions["applyRoles"] == ["ops_manager"]


@pytest.mark.parametrize("field", ["viewRoles", "editRoles", "applyRoles"])
def test_first_class_action_role_contract_rejects_malformed_values(field: str) -> None:
    definition = _definition([])
    definition["permissions"] = {field: "ops_manager"}
    with pytest.raises(ValidationFailed, match=field):
        compile_action_contract(definition)


def test_nested_struct_media_upload_path_resolves_the_leaf_contract() -> None:
    contract = compile_action_contract(
        _definition(
            [
                {
                    "apiName": "evidence",
                    "type": "struct",
                    "fields": [
                        {
                            "apiName": "receipt",
                            "type": "attachment",
                            "mediaSet": "action.receipts",
                        }
                    ],
                }
            ]
        )
    )

    parameter, kind = action_media_parameter(contract, "evidence.receipt")

    assert parameter.api_name == "receipt"
    assert parameter.metadata["mediaSet"] == "action.receipts"
    assert kind == "attachment"
    with pytest.raises(NotFound):
        action_media_parameter(contract, "evidence.unknown")


def test_form_layout_is_validated_and_embedded_in_every_consumer_schema() -> None:
    definition = _definition(
        [
            {"apiName": "status", "type": "string"},
            {"apiName": "reason", "type": "string"},
            {"apiName": "note", "type": "string"},
        ]
    )
    definition["formLayout"] = {
        "sections": [
            {
                "id": "decision",
                "title": "Decision",
                "description": "Choose the new state and explain it.",
                "columns": 2,
                "isCollapsible": False,
                "parameterNames": ["status", "reason"],
                "visibleWhen": _condition("status", "PENDING"),
            }
        ]
    }

    contract = compile_action_contract(definition)
    payload = action_contract_payload(contract)
    schema = action_parameter_json_schema(contract)

    assert payload["formLayout"] == schema["x-foundry-form-layout"]
    sections = payload["formLayout"]["sections"]  # type: ignore[index]
    assert sections[0]["parameterNames"] == ["status", "reason"]
    assert sections[0]["visibleWhen"] == _condition("status", "PENDING")
    assert sections[1]["id"] == "other-parameters"
    assert sections[1]["parameterNames"] == ["note"]
    assert payload["inlineEligibility"] == {
        "isEligible": False,
        "reasons": [
            "inline edit rule must target the selected object",
            "inline edit requires exactly one property assignment",
            "inline edit requires exactly one Action parameter",
        ],
    }


@pytest.mark.parametrize(
    "sections,match",
    [
        (
            [
                {"id": "one", "title": "One", "parameterNames": ["status"]},
                {"id": "two", "title": "Two", "parameterNames": ["status"]},
            ],
            "only one form section",
        ),
        ([{"id": "one", "title": "One", "parameterNames": ["unknown"]}], "unknown action parameters"),
        (
            [
                {
                    "id": "one",
                    "title": "One",
                    "parameterNames": ["status"],
                    "visibleWhen": _condition("unknown", "PENDING"),
                }
            ],
            "condition references unknown action parameters",
        ),
        (
            [
                {
                    "id": "one",
                    "title": "One",
                    "parameterNames": ["status"],
                    "visibleWhen": {
                        "op": "eq",
                        "left": {"kind": "objectProperty", "property": "status"},
                        "right": {"kind": "literal", "value": "PENDING"},
                    },
                }
            ],
            "parameter and literal values only",
        ),
        (
            [
                {
                    "id": "one",
                    "title": "One",
                    "isInitiallyCollapsed": True,
                    "parameterNames": [],
                }
            ],
            "must be collapsible",
        ),
    ],
)
def test_invalid_form_layout_fails_closed(sections: list[dict[str, object]], match: str) -> None:
    definition = _definition([{"apiName": "status", "type": "string"}])
    definition["formLayout"] = {"sections": sections}

    with pytest.raises(ValidationFailed, match=match):
        compile_action_contract(definition)


def test_inline_eligibility_explains_why_full_runtime_is_required() -> None:
    definition = _definition([{"apiName": "note", "type": "string", "required": True}])
    definition["rules"] = [
        {
            "kind": "modifyObject",
            "ruleId": "set-note",
            "objectType": "Order",
            "target": {"kind": "parameter", "parameter": "__target__"},
            "assignments": [{"property": "operatorNote", "value": {"kind": "parameter", "parameter": "note"}}],
        }
    ]
    definition["effects"] = [
        {
            "effectId": "notify",
            "kind": "notification",
            "phase": "after_commit",
            "targetRef": "notification-policy:ops",
        }
    ]

    eligibility = action_contract_payload(compile_action_contract(definition))["inlineEligibility"]

    assert eligibility["isEligible"] is False
    assert eligibility["reasons"] == ["Actions with side effects require the full execution runtime"]


def test_inline_eligibility_seals_the_cell_property_and_parameter_binding() -> None:
    definition = _definition([{"apiName": "note", "type": "string", "required": True}])
    definition["rules"] = [
        {
            "kind": "modifyObject",
            "ruleId": "set-note",
            "objectType": "Order",
            "target": {"kind": "parameter", "parameter": "__target__"},
            "assignments": [{"property": "operatorNote", "value": {"kind": "parameter", "parameter": "note"}}],
        }
    ]

    eligibility = action_contract_payload(compile_action_contract(definition))["inlineEligibility"]

    assert eligibility == {
        "isEligible": True,
        "reasons": [],
        "propertyApiName": "operatorNote",
        "parameterApiName": "note",
        "parameterType": "string",
    }


def test_inline_eligibility_rejects_hidden_multi_parameter_or_bulk_semantics() -> None:
    definition = _definition(
        [
            {"apiName": "note", "type": "string", "required": True},
            {"apiName": "reason", "type": "string"},
        ]
    )
    definition["rules"] = [
        {
            "kind": "modifyObject",
            "ruleId": "set-note",
            "objectType": "Order",
            "cardinality": "many",
            "target": {"kind": "parameter", "parameter": "__target__"},
            "assignments": [{"property": "operatorNote", "value": {"kind": "parameter", "parameter": "note"}}],
        }
    ]

    eligibility = action_contract_payload(compile_action_contract(definition))["inlineEligibility"]

    assert eligibility["isEligible"] is False
    assert eligibility["reasons"] == [
        "inline edit cannot target an object set",
        "inline edit requires exactly one Action parameter",
    ]


def test_parameter_override_uses_first_match_and_earlier_values_only() -> None:
    contract = compile_action_contract(
        _definition(
            [
                {"apiName": "mode", "type": "string", "required": True},
                {
                    "apiName": "reason",
                    "type": "string",
                    "required": False,
                    "overrides": [
                        {"when": _condition("mode", "urgent"), "config": {"required": True, "editable": True}},
                        {"when": _condition("mode", "urgent"), "config": {"visible": False}},
                    ],
                },
            ]
        )
    )
    resolution = resolve_action_parameters(contract, _context({"mode": "urgent", "reason": "stockout"}))

    config = parameter_config_payload(resolution.configs["reason"])
    assert resolution.values == {"mode": "urgent", "reason": "stockout"}
    assert config["matchedOverride"] == 0
    assert config["required"] is True
    assert config["visible"] is True


def test_override_cannot_reference_current_or_later_parameter() -> None:
    with pytest.raises(ValidationFailed, match="earlier parameters only"):
        compile_action_contract(
            _definition(
                [
                    {
                        "apiName": "first",
                        "type": "string",
                        "overrides": [{"when": _condition("later", "yes"), "config": {"required": True}}],
                    },
                    {"apiName": "later", "type": "string"},
                ]
            )
        )


def test_defaults_resolve_from_prior_parameter_object_actor_time_and_generated_id() -> None:
    contract = compile_action_contract(
        _definition(
            [
                {"apiName": "base", "type": "string", "default": {"kind": "literal", "value": "seed"}},
                {"apiName": "copy", "type": "string", "default": {"kind": "parameter", "parameter": "base"}},
                {"apiName": "status", "type": "string", "default": {"kind": "objectProperty", "property": "status"}},
                {"apiName": "actor", "type": "string", "default": {"kind": "currentUser", "attribute": "id"}},
                {"apiName": "today", "type": "date", "default": {"kind": "currentTime", "unit": "date"}},
                {"apiName": "newId", "type": "string", "default": {"kind": "generatedId", "strategy": "uuid"}},
            ]
        )
    )
    resolution = resolve_action_parameters(contract, _context({}))

    assert resolution.values == {
        "base": "seed",
        "copy": "seed",
        "status": "OPEN",
        "actor": "user-7",
        "today": "2026-08-03",
        "newId": "uuid-generated",
    }


def test_nested_struct_and_object_set_values_are_validated_recursively() -> None:
    contract = compile_action_contract(
        _definition(
            [
                {
                    "apiName": "guest",
                    "type": "struct",
                    "required": True,
                    "fields": [
                        {"apiName": "name", "type": "string", "required": True},
                        {
                            "apiName": "contact",
                            "type": "struct",
                            "required": True,
                            "fields": [{"apiName": "phone", "type": "string", "required": True}],
                        },
                    ],
                },
                {"apiName": "tags", "type": "objectSet", "itemType": "string"},
            ]
        )
    )

    result = resolve_action_parameters(
        contract,
        _context({"guest": {"name": "Min", "contact": {"phone": "+8210"}}, "tags": ["vip", "window"]}),
    )

    assert result.values["guest"] == {"name": "Min", "contact": {"phone": "+8210"}}
    with pytest.raises(ValidationFailed, match="invalid action parameter types"):
        resolve_action_parameters(contract, _context({"guest": {"name": "Min", "contact": {}}, "tags": []}))
    with pytest.raises(ValidationFailed, match="invalid action parameter types"):
        resolve_action_parameters(
            contract,
            _context({"guest": {"name": "Min", "contact": {"phone": "+8210"}}, "tags": ["vip", "vip"]}),
        )


def test_struct_contract_requires_typed_unique_fields() -> None:
    with pytest.raises(ValidationFailed, match="at least one typed field"):
        compile_action_contract(_definition([{"apiName": "guest", "type": "struct", "fields": []}]))
    with pytest.raises(ValidationFailed, match="duplicate struct field"):
        compile_action_contract(
            _definition(
                [
                    {
                        "apiName": "guest",
                        "type": "struct",
                        "fields": [
                            {"apiName": "name", "type": "string"},
                            {"apiName": "name", "type": "string"},
                        ],
                    }
                ]
            )
        )


def test_nested_submission_condition_evaluates_without_expression_execution() -> None:
    condition = {
        "all": [
            _condition("mode", "urgent"),
            {
                "any": [
                    {
                        "op": "eq",
                        "left": {"kind": "currentUser", "attribute": "groups"},
                        "right": {"kind": "literal", "value": ["ops"]},
                    },
                    {"not": _condition("reason", "blocked")},
                ]
            },
        ]
    }
    context = StaticActionConditionContext(
        parameters={"mode": "urgent", "reason": "ready"},
        object_properties={"status": "OPEN"},
        actor_user_id="user-7",
        actor_groups=("ops",),
    )

    assert evaluate_action_condition(condition, context) is True


def test_decimal_parameter_condition_uses_numeric_not_lexicographic_order() -> None:
    condition = {
        "op": "gt",
        "left": {"kind": "parameter", "parameter": "amount"},
        "right": {"kind": "literal", "value": "2"},
    }
    context = StaticActionConditionContext(
        parameters={"amount": "10"},
        object_properties={},
        actor_user_id="user-7",
        actor_groups=("ops",),
        parameter_types={"amount": "decimal"},
    )

    assert evaluate_action_condition(condition, context) is True
    assert (
        evaluate_action_condition(
            {
                "op": "eq",
                "left": {"kind": "parameter", "parameter": "amount"},
                "right": {"kind": "literal", "value": "10.00"},
            },
            context,
        )
        is True
    )


def test_decimal_parameter_condition_rejects_numeric_literal_at_compile_time() -> None:
    definition = _definition([{"apiName": "amount", "type": "decimal"}])
    definition["submissionCriteria"] = {
        "op": "gte",
        "left": {"kind": "parameter", "parameter": "amount"},
        "right": {"kind": "literal", "value": 1.25},
    }

    with pytest.raises(ValidationFailed, match="condition literal"):
        compile_action_contract(definition)


def test_submission_criteria_rejects_unknown_parameter_reference() -> None:
    definition = _definition([{"apiName": "mode", "type": "string"}])
    definition["submissionCriteria"] = _condition("missing", "urgent")

    with pytest.raises(ValidationFailed, match="unknown parameters") as exc_info:
        compile_action_contract(definition)

    assert exc_info.value.details == {"invalidReferences": ["missing"]}


def test_linked_object_submission_values_are_typed_bounded_sources() -> None:
    values = {
        "outgoing:OrderCustomer:tier:values": ("gold", "silver"),
        "outgoing:OrderCustomer:tier:count": 2,
    }
    context = StaticActionConditionContext(
        parameters={},
        object_properties={},
        actor_user_id="user-7",
        actor_groups=("ops",),
        linked_object_properties=values,
    )
    contains_gold = {
        "op": "contains",
        "left": {
            "kind": "linkedObjectProperty",
            "linkType": "OrderCustomer",
            "direction": "outgoing",
            "property": "tier",
        },
        "right": {"kind": "literal", "value": "gold"},
    }
    has_two = {
        "op": "eq",
        "left": {
            "kind": "linkedObjectProperty",
            "linkType": "OrderCustomer",
            "direction": "outgoing",
            "property": "tier",
            "aggregation": "count",
        },
        "right": {"kind": "literal", "value": 2},
    }

    assert evaluate_action_condition({"all": [contains_gold, has_two]}, context) is True
    assert {reference.key for reference in referenced_linked_object_properties({"all": [contains_gold, has_two]})} == {
        "outgoing:OrderCustomer:tier:values",
        "outgoing:OrderCustomer:tier:count",
    }


@pytest.mark.parametrize("operator", ["neq", "notIn"])
def test_group_identity_submission_criteria_rejects_negative_operators(operator: str) -> None:
    definition = _definition([])
    definition["submissionCriteria"] = {
        "op": operator,
        "left": {"kind": "currentUser", "attribute": "groups"},
        "right": {"kind": "literal", "value": ["blocked"]},
    }

    with pytest.raises(ValidationFailed, match="positive operator"):
        compile_action_contract(definition)


def test_group_identity_rejects_negation_but_allows_verified_user_attributes() -> None:
    negated = _definition([])
    negated["submissionCriteria"] = {
        "not": {
            "op": "contains",
            "left": {"kind": "currentUser", "attribute": "groups"},
            "right": {"kind": "literal", "value": "blocked"},
        }
    }
    attribute_condition = _definition([])
    attribute_condition["submissionCriteria"] = {
        "op": "eq",
        "left": {"kind": "currentUser", "attribute": "department"},
        "right": {"kind": "literal", "value": "sales"},
    }

    with pytest.raises(ValidationFailed, match="cannot be negated"):
        compile_action_contract(negated)
    contract = compile_action_contract(attribute_condition)
    context = StaticActionConditionContext(
        parameters={},
        object_properties={},
        actor_user_id="user-7",
        actor_groups=("ops",),
        actor_attributes={"department": "sales", "region": "apac"},
    )
    assert contract.submission_criteria is not None
    assert evaluate_action_condition(contract.submission_criteria, context) is True


def test_legacy_v2_target_is_inferred_but_v3_requires_explicit_target() -> None:
    legacy = compile_action_contract(
        {
            "apiName": "LegacyCreate",
            "rulesV2": [{"kind": "createObject", "ruleId": "create", "objectType": "Order"}],
        }
    )
    assert legacy.target.api_name == "Order"
    assert legacy.source_version == 1

    with pytest.raises(ValidationFailed, match="target is required"):
        compile_action_contract({"contractVersion": 3, "apiName": "Broken", "rules": []})


def test_activation_validation_accepts_version_pinned_function_contract() -> None:
    definition = {
        "functionTypes": [{"apiName": "calculateEdits", "version": "2.1.0"}],
        "actionTypes": [
            {
                "apiName": "UpdateOrderFromFunction",
                "contractVersion": 3,
                "target": "Order",
                "function": {"apiName": "calculateEdits", "version": "2.1.0"},
            }
        ],
    }

    validate_yaml_action_definitions(
        definition,
        {"Order": {"apiName": "Order", "properties": []}},
        {},
    )


def test_function_contract_defaults_to_per_request_with_palantir_batch_limit() -> None:
    contract = compile_action_contract(
        {
            "apiName": "UpdateOrder",
            "contractVersion": 3,
            "target": "Order",
            "function": {"apiName": "calculateEdits", "version": "2.1.0"},
        }
    )

    assert contract.function is not None
    assert contract.function.execution_mode == "per_request"
    assert contract.function.max_batch_size == 20
    assert action_contract_payload(contract)["function"] == {
        "apiName": "calculateEdits",
        "version": "2.1.0",
        "executionMode": "per_request",
        "batchInputName": None,
        "maxBatchSize": 20,
    }


def test_batched_function_contract_requires_one_matching_list_of_struct_input() -> None:
    parameters = [{"apiName": "status", "type": "string", "required": True}]
    definition = {
        "functionTypes": [
            {
                "apiName": "calculateEdits",
                "version": "2.1.0",
                "inputs": [
                    {
                        "apiName": "requests",
                        "type": "array",
                        "itemType": "struct",
                        "required": True,
                        "fields": [dict(parameters[0])],
                    }
                ],
            }
        ],
        "actionTypes": [
            {
                "apiName": "UpdateOrders",
                "contractVersion": 3,
                "target": "Order",
                "parameters": parameters,
                "function": {
                    "apiName": "calculateEdits",
                    "version": "2.1.0",
                    "executionMode": "batched",
                    "batchInputName": "requests",
                    "maxBatchSize": 8_000,
                },
            }
        ],
    }

    validate_yaml_action_definitions(definition, {"Order": {"apiName": "Order", "properties": []}}, {})

    definition["functionTypes"][0]["inputs"][0]["fields"][0]["type"] = "integer"
    with pytest.raises(ValidationFailed, match="do not match"):
        validate_yaml_action_definitions(definition, {"Order": {"apiName": "Order", "properties": []}}, {})


def test_per_request_function_contract_accepts_array_of_struct_parameter() -> None:
    reservation_fields = [
        {"apiName": "objectId", "type": "string", "required": True},
        {"apiName": "objectVersion", "type": "integer", "required": True},
    ]
    parameters = [
        {
            "apiName": "existingReservations",
            "type": "array",
            "itemType": "struct",
            "required": True,
            "fields": reservation_fields,
        }
    ]
    definition = {
        "functionTypes": [
            {
                "apiName": "createReservation",
                "version": "1.0.0",
                "inputs": copy.deepcopy(parameters),
            }
        ],
        "actionTypes": [
            {
                "apiName": "BookReservation",
                "contractVersion": 3,
                "target": "Restaurant",
                "parameters": parameters,
                "function": {"apiName": "createReservation", "version": "1.0.0"},
            }
        ],
    }

    validate_yaml_action_definitions(
        definition,
        {"Restaurant": {"apiName": "Restaurant", "properties": []}},
        {},
    )


def test_batched_function_contract_rejects_missing_coordinate_and_excessive_limit() -> None:
    with pytest.raises(ValidationFailed, match="batchInputName"):
        compile_action_contract(
            {
                "apiName": "Broken",
                "contractVersion": 3,
                "target": "Order",
                "function": {
                    "apiName": "calculateEdits",
                    "version": "2.1.0",
                    "executionMode": "batched",
                },
            }
        )
    with pytest.raises(ValidationFailed, match="maxBatchSize"):
        compile_action_contract(
            {
                "apiName": "Broken",
                "contractVersion": 3,
                "target": "Order",
                "function": {
                    "apiName": "calculateEdits",
                    "version": "2.1.0",
                    "executionMode": "batched",
                    "batchInputName": "requests",
                    "maxBatchSize": 10_001,
                },
            }
        )


def test_activation_validates_linked_submission_criteria_against_ontology() -> None:
    action = _definition([])
    action["submissionCriteria"] = {
        "op": "contains",
        "left": {
            "kind": "linkedObjectProperty",
            "linkType": "OrderCustomer",
            "direction": "outgoing",
            "property": "segment",
        },
        "right": {"kind": "literal", "value": "enterprise"},
    }
    object_defs = {
        "Order": {"apiName": "Order", "properties": [{"apiName": "orderId", "type": "string"}]},
        "Customer": {"apiName": "Customer", "properties": [{"apiName": "segment", "type": "string"}]},
    }
    link_defs = {"OrderCustomer": {"apiName": "OrderCustomer", "from": "Order", "to": "Customer"}}

    validate_yaml_action_definitions({"actionTypes": [action]}, object_defs, link_defs)
    action["submissionCriteria"]["left"]["property"] = "missing"
    with pytest.raises(ValidationFailed, match="linked property was not found"):
        validate_yaml_action_definitions({"actionTypes": [action]}, object_defs, link_defs)


def test_activation_rejects_linked_criteria_not_anchored_at_action_target() -> None:
    action = _definition([])
    action["submissionCriteria"] = {
        "op": "contains",
        "left": {
            "kind": "linkedObjectProperty",
            "linkType": "CustomerOrder",
            "direction": "outgoing",
            "property": "status",
        },
        "right": {"kind": "literal", "value": "PENDING"},
    }
    object_defs = {
        "Order": {"apiName": "Order", "properties": [{"apiName": "status", "type": "string"}]},
        "Customer": {"apiName": "Customer", "properties": [{"apiName": "segment", "type": "string"}]},
    }
    link_defs = {"CustomerOrder": {"apiName": "CustomerOrder", "from": "Customer", "to": "Order"}}

    with pytest.raises(ValidationFailed, match="not anchored"):
        validate_yaml_action_definitions({"actionTypes": [action]}, object_defs, link_defs)


def test_activation_requires_a_distinct_existing_compensation_action() -> None:
    action = _definition([])
    action["revert"] = {"enabled": True, "compensationAction": "CompensateOrder"}
    compensation = {
        **_definition([]),
        "apiName": "CompensateOrder",
        "revert": {"enabled": False},
    }
    object_defs = {"Order": {"apiName": "Order", "properties": []}}

    validate_yaml_action_definitions({"actionTypes": [action, compensation]}, object_defs, {})

    action["revert"] = {"enabled": True, "compensationAction": "MissingAction"}
    with pytest.raises(ValidationFailed, match="compensation reference"):
        validate_yaml_action_definitions({"actionTypes": [action, compensation]}, object_defs, {})

    action["revert"] = {"enabled": True, "compensationAction": "UpdateOrder"}
    with pytest.raises(ValidationFailed, match="compensation reference"):
        validate_yaml_action_definitions({"actionTypes": [action, compensation]}, object_defs, {})


def test_v3_effects_are_typed_and_deterministic() -> None:
    definition = _definition([])
    definition["effects"] = [
        {
            "effectId": "notify-ops",
            "kind": "notification",
            "phase": "after_commit",
            "targetRef": "notification-policy:operations",
            "payload": {"template": "order-updated"},
        }
    ]

    first = compile_action_contract(definition)
    second = compile_action_contract(definition)

    assert tuple(map(action_effect_payload, first.effects)) == tuple(map(action_effect_payload, second.effects))
    assert action_effect_payload(first.effects[0]) == {
        "effectId": "notify-ops",
        "kind": "notification",
        "phase": "after_commit",
        "targetRef": "notification-policy:operations",
        "payload": {"template": "order-updated"},
        "responseFields": {},
        "maxAttempts": 3,
        "timeoutSeconds": 30,
    }


def test_effect_inline_destination_is_rejected_recursively() -> None:
    definition = _definition([])
    definition["effects"] = [
        {
            "effectId": "unsafe",
            "kind": "webhook",
            "phase": "before_commit",
            "targetRef": "connector:erp/orders",
            "payload": {"callback": {"url": "https://attacker.invalid"}},
        }
    ]

    with pytest.raises(ValidationFailed, match="inline destination"):
        compile_action_contract(definition)


def test_only_one_before_commit_webhook_is_allowed() -> None:
    definition = _definition([])
    definition["effects"] = [
        {
            "effectId": "first",
            "kind": "webhook",
            "phase": "before_commit",
            "targetRef": "connector:erp/orders",
        },
        {
            "effectId": "second",
            "kind": "webhook",
            "phase": "before_commit",
            "targetRef": "connector:erp/orders",
        },
    ]

    with pytest.raises(ValidationFailed, match="only one before-commit"):
        compile_action_contract(definition)


def test_v3_rule_may_use_a_declared_typed_before_effect_response() -> None:
    definition = _definition([])
    definition["rules"][0]["assignments"] = [
        {
            "property": "status",
            "value": {"kind": "webhookResponse", "field": "approvalStatus"},
        }
    ]
    definition["effects"] = [
        {
            "effectId": "erp-write",
            "kind": "webhook",
            "phase": "before_commit",
            "targetRef": "connector:erp/orders",
            "responseFields": {"approvalStatus": "string"},
        }
    ]

    contract = compile_action_contract(definition)

    assert contract.effects[0].response_fields == {"approvalStatus": "string"}


def test_webhook_response_rule_rejects_an_undeclared_field() -> None:
    definition = _definition([])
    definition["rules"][0]["assignments"] = [
        {"property": "status", "value": {"kind": "webhookResponse", "field": "approvalStatus"}}
    ]
    definition["effects"] = [
        {
            "effectId": "erp-write",
            "kind": "webhook",
            "phase": "before_commit",
            "targetRef": "connector:erp/orders",
        }
    ]

    with pytest.raises(ValidationFailed, match="not declared"):
        compile_action_contract(definition)


def test_activation_rejects_webhook_response_type_that_does_not_match_the_property() -> None:
    action = _definition([])
    action["rules"][0]["assignments"] = [
        {"property": "total", "value": {"kind": "webhookResponse", "field": "approvedTotal"}}
    ]
    action["effects"] = [
        {
            "effectId": "erp-write",
            "kind": "webhook",
            "phase": "before_commit",
            "targetRef": "connector:erp/orders",
            "responseFields": {"approvedTotal": "string"},
        }
    ]
    definition = {"actionTypes": [action]}

    with pytest.raises(ValidationFailed, match="response type does not match"):
        validate_yaml_action_definitions(
            definition,
            {
                "Order": {
                    "apiName": "Order",
                    "properties": [{"apiName": "total", "type": "float", "editable": True}],
                }
            },
            {},
        )


def test_legacy_writeback_keeps_its_compatible_connector_reference() -> None:
    contract = compile_action_contract(
        {
            "apiName": "LegacyWriteback",
            "target": "Order",
            "mutations": [],
            "writebacks": [{"apiName": "erp", "connector": "mock_erp_simulator"}],
        }
    )

    assert contract.source_version == 1
    assert contract.effects[0].target_ref == "mock_erp_simulator"


def test_canonical_snapshot_round_trip_preserves_legacy_effect_semantics() -> None:
    original = compile_action_contract(
        {
            "apiName": "LegacyWriteback",
            "target": "Order",
            "mutations": [],
            "writebacks": [{"apiName": "erp", "connector": "mock_erp_simulator"}],
        }
    )

    restored = compile_action_contract_snapshot(action_contract_payload(original))

    assert restored.source_version == 1
    assert action_contract_payload(restored) == action_contract_payload(original)


def _context(submitted: dict[str, object]) -> ActionParameterContext:
    return ActionParameterContext(
        submitted=submitted,
        object_properties={"status": "OPEN"},
        actor_user_id="user-7",
        actor_groups=("ops",),
        now=datetime(2026, 8, 3, 10, 0, tzinfo=UTC),
        generate_id=lambda strategy: f"{strategy}-generated",
    )
