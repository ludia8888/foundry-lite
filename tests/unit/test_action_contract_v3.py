"""Canonical Action Contract v3 normalization and parameter semantics."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from foundry_lite.application.services.action_definition_validation import validate_yaml_action_definitions
from foundry_lite.domain.action_runtime.action_conditions import (
    StaticActionConditionContext,
    evaluate_action_condition,
)
from foundry_lite.domain.action_runtime.action_contract import (
    ACTION_PARAMETER_TYPES,
    action_contract_fingerprint,
    action_contract_payload,
    action_parameter_json_schema,
    compile_action_contract,
)
from foundry_lite.domain.action_runtime.action_parameters import (
    ActionParameterContext,
    parameter_config_payload,
    resolve_action_parameters,
)
from foundry_lite.domain.errors import ValidationFailed


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
    assert schema["properties"]["p_timestamp"]["format"] == "date-time"  # type: ignore[index]


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


def _context(submitted: dict[str, object]) -> ActionParameterContext:
    return ActionParameterContext(
        submitted=submitted,
        object_properties={"status": "OPEN"},
        actor_user_id="user-7",
        actor_groups=("ops",),
        now=datetime(2026, 8, 3, 10, 0, tzinfo=UTC),
        generate_id=lambda strategy: f"{strategy}-generated",
    )
