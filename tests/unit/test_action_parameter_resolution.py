"""Action parameter resolution: server-computed defaults and value validation.

Defaults here are computed on the server, not supplied by the caller — `currentUser`,
`currentTime`, and `generatedId` exist precisely so a client cannot claim to be someone else,
backdate a record, or choose its own identifier. That makes the default resolver a trust
boundary, and an unsupported default kind must fail loudly rather than resolve to `None` and
be written as if the author had asked for an empty value.

Type checking matters for the same reason: an Action rule assigns these values into object
properties, so a string reaching an integer property is a data-shape bug that surfaces far
from here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from foundry_lite.domain.action_runtime.action_contract import compile_action_contract
from foundry_lite.domain.action_runtime.action_parameters import (
    ActionParameterContext,
    default_action_parameter_context,
    parameter_config_payload,
    resolve_action_parameters,
)
from foundry_lite.domain.errors import ValidationFailed

_NOW = datetime(2026, 8, 5, 12, 30, 45, tzinfo=UTC)


def _definition(parameters: list[dict[str, Any]]) -> dict[str, Any]:
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


def _context(
    submitted: dict[str, Any] | None = None,
    *,
    object_properties: dict[str, Any] | None = None,
    actor_user_id: str = "u-1",
    actor_groups: tuple[str, ...] = ("ops",),
    actor_attributes: dict[str, Any] | None = None,
) -> ActionParameterContext:
    return ActionParameterContext(
        submitted=submitted or {},
        object_properties=object_properties or {},
        actor_user_id=actor_user_id,
        actor_groups=actor_groups,
        actor_attributes=actor_attributes or {},
        now=_NOW,
        generate_id=lambda strategy: f"generated-{strategy}",
    )


def _resolve(parameters: list[dict[str, Any]], context: ActionParameterContext) -> dict[str, Any]:
    contract = compile_action_contract(_definition(parameters))
    return dict(resolve_action_parameters(contract, context).values)


def _param(api_name: str, data_type: str = "string", **extra: Any) -> dict[str, Any]:
    return {"apiName": api_name, "type": data_type, **extra}


# --- submitted values ---------------------------------------------------------------


def test_a_parameter_the_contract_never_declared_is_refused() -> None:
    """Accepting an undeclared parameter would let a caller smuggle a value past the schema."""
    with pytest.raises(ValidationFailed, match="unexpected action parameters"):
        _resolve([_param("reason")], _context({"reason": "ok", "ghost": 1}))


def test_a_missing_required_parameter_is_refused() -> None:
    with pytest.raises(ValidationFailed, match="missing required action parameters"):
        _resolve([_param("reason", required=True)], _context({}))


def test_an_optional_parameter_is_simply_absent_when_not_submitted() -> None:
    assert _resolve([_param("reason")], _context({})) == {}


# --- server-computed defaults -------------------------------------------------------


def test_current_user_default_uses_the_authenticated_actor() -> None:
    """The caller cannot claim to be someone else — the server fills this in."""
    values = _resolve(
        [_param("approver", default={"kind": "currentUser"})],
        _context({}, actor_user_id="u-42"),
    )

    assert values == {"approver": "u-42"}


@pytest.mark.parametrize("attribute", ["group", "groups", "roles"])
def test_current_user_default_can_resolve_the_actor_groups(attribute: str) -> None:
    values = _resolve(
        [_param("teams", "array", itemType="string", default={"kind": "currentUser", "attribute": attribute})],
        _context({}, actor_groups=("ops", "audit")),
    )

    assert values == {"teams": ["ops", "audit"]}


def test_current_user_default_can_resolve_a_named_attribute() -> None:
    values = _resolve(
        [_param("department", default={"kind": "currentUser", "attribute": "department"})],
        _context({}, actor_attributes={"department": "logistics"}),
    )

    assert values == {"department": "logistics"}


def test_current_user_default_with_a_blank_attribute_is_refused() -> None:
    with pytest.raises(ValidationFailed, match="current-user default attribute"):
        _resolve([_param("x", default={"kind": "currentUser", "attribute": "   "})], _context({}))


def test_current_time_default_renders_a_date() -> None:
    values = _resolve([_param("day", default={"kind": "currentTime", "unit": "date"})], _context({}))

    assert values == {"day": "2026-08-05"}


def test_current_time_default_renders_a_zulu_timestamp() -> None:
    """A `+00:00` suffix and a `Z` suffix are the same instant but not the same string."""
    values = _resolve([_param("at", default={"kind": "currentTime", "unit": "timestamp"})], _context({}))

    assert values == {"at": "2026-08-05T12:30:45Z"}


def test_current_time_default_with_an_unknown_unit_is_refused() -> None:
    with pytest.raises(ValidationFailed, match="current-time default unit"):
        _resolve([_param("at", default={"kind": "currentTime", "unit": "fortnight"})], _context({}))


def test_generated_id_default_delegates_to_the_server_generator() -> None:
    values = _resolve([_param("ref", default={"kind": "generatedId", "strategy": "uuid4"})], _context({}))

    assert values == {"ref": "generated-uuid4"}


def test_object_property_default_reads_the_target_object() -> None:
    values = _resolve(
        [_param("status", default={"kind": "objectProperty", "property": "status"})],
        _context({}, object_properties={"status": "PENDING"}),
    )

    assert values == {"status": "PENDING"}


def test_parameter_default_reads_an_earlier_parameter_in_declaration_order() -> None:
    values = _resolve(
        [
            _param("primary"),
            _param("mirror", default={"kind": "parameter", "parameter": "primary"}),
        ],
        _context({"primary": "value-1"}),
    )

    assert values == {"primary": "value-1", "mirror": "value-1"}


def test_an_unsupported_default_kind_is_refused_not_silently_none() -> None:
    """Resolving to None would persist an empty value as if the author had asked for it."""
    with pytest.raises(ValidationFailed, match="unsupported action parameter default"):
        _resolve([_param("x", default={"kind": "fromTheEther"})], _context({}))


def test_a_submitted_value_wins_over_a_default() -> None:
    values = _resolve(
        [_param("approver", default={"kind": "currentUser"})],
        _context({"approver": "u-explicit"}, actor_user_id="u-1"),
    )

    assert values == {"approver": "u-explicit"}


# --- context construction -----------------------------------------------------------


def test_default_context_stamps_the_current_time_once() -> None:
    context = default_action_parameter_context(
        submitted={},
        object_properties={},
        actor_user_id="u-1",
        actor_groups=("ops",),
        actor_attributes={},
        generate_id=lambda strategy: strategy,
    )

    assert context.now.tzinfo is not None, "a naive timestamp would render an ambiguous default"


# --- config payload -----------------------------------------------------------------


def test_config_payload_reports_the_resolved_form_state() -> None:
    contract = compile_action_contract(_definition([_param("reason", required=True)]))

    resolution = resolve_action_parameters(contract, _context({"reason": "why"}))
    payload = parameter_config_payload(resolution.configs["reason"])

    assert payload["required"] is True
    assert payload["visible"] is True
    assert payload["editable"] is True
    assert payload["default"] is None
    assert payload["matchedOverride"] is None


# --- value typing -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data_type", "value"),
    [
        ("string", "text"),
        ("integer", 3),
        ("long", 3),
        ("float", 3.5),
        ("float", 3),
        ("decimal", "10.25"),
        ("boolean", True),
        ("date", "2026-08-05"),
        ("timestamp", "2026-08-05T12:00:00Z"),
    ],
)
def test_a_value_of_the_declared_type_is_accepted(data_type: str, value: object) -> None:
    assert _resolve([_param("p", data_type)], _context({"p": value})) == {"p": value}


@pytest.mark.parametrize(
    ("data_type", "value"),
    [
        ("string", 1),
        ("integer", "3"),
        ("integer", True),
        ("float", True),
        ("decimal", 4),
        ("decimal", 4.5),
        ("decimal", " 10.25 "),
        ("decimal", "1_000.00"),
        ("decimal", "not-a-number"),
        ("decimal", True),
        ("boolean", "true"),
        ("date", "2026-13-45"),
        ("date", 20260805),
        ("timestamp", "yesterday"),
        ("timestamp", "2026-08-05T12:00:00"),
        ("timestamp", "2026-08-05 12:00:00Z"),
        ("float", float("nan")),
        ("float", float("inf")),
        ("decimal", "NaN"),
        ("decimal", "Infinity"),
    ],
)
def test_a_value_of_the_wrong_type_is_refused(data_type: str, value: object) -> None:
    """An Action rule assigns this into an object property, so the shape must hold here."""
    with pytest.raises(ValidationFailed, match="invalid action parameter types"):
        _resolve([_param("p", data_type)], _context({"p": value}))


def test_decimal_constraints_compare_exact_values_without_float_rounding() -> None:
    parameter = _param(
        "amount",
        "decimal",
        constraints={"minimum": "9007199254740993.000000000000000001"},
    )

    assert _resolve([parameter], _context({"amount": "9007199254740993.000000000000000001"})) == {
        "amount": "9007199254740993.000000000000000001"
    }
    with pytest.raises(ValidationFailed, match="constraint failed") as caught:
        _resolve([parameter], _context({"amount": "9007199254740993.000000000000000000"}))
    assert caught.value.details["constraint"] == "minimum"


def test_decimal_enum_uses_numeric_equality_without_losing_wire_precision() -> None:
    parameter = _param(
        "amount",
        "decimal",
        constraints={"enum": ["9007199254740993.000000000000000001"]},
    )

    assert _resolve([parameter], _context({"amount": "9007199254740993.0000000000000000010"})) == {
        "amount": "9007199254740993.0000000000000000010"
    }


def test_decimal_enum_rejects_numerically_duplicate_spellings() -> None:
    with pytest.raises(ValidationFailed, match="duplicate"):
        compile_action_contract(
            _definition(
                [
                    _param(
                        "amount",
                        "decimal",
                        constraints={"enum": ["1", "1.0"]},
                    )
                ]
            )
        )


def test_nested_decimal_constraints_use_the_same_wire_contract() -> None:
    parameter = _param(
        "lines",
        "struct",
        fields=[
            {
                "apiName": "amounts",
                "type": "array",
                "itemType": "decimal",
                "required": True,
            }
        ],
    )

    assert _resolve([parameter], _context({"lines": {"amounts": ["0.10", "0.20"]}})) == {
        "lines": {"amounts": ["0.10", "0.20"]}
    }
    with pytest.raises(ValidationFailed, match="invalid action parameter types"):
        _resolve([parameter], _context({"lines": {"amounts": [0.1, 0.2]}}))


@pytest.mark.parametrize("value", ["O-1", {"objectType": "Order", "objectId": "O-1"}])
def test_an_object_reference_accepts_an_id_or_a_typed_reference(value: object) -> None:
    assert _resolve([_param("p", "object")], _context({"p": value})) == {"p": value}


@pytest.mark.parametrize(
    "value",
    ["", {"objectType": "Order"}, {"objectId": "O-1"}, {"objectType": "", "objectId": "O-1"}, 7],
)
def test_an_incomplete_object_reference_is_refused(value: object) -> None:
    with pytest.raises(ValidationFailed, match="invalid action parameter types"):
        _resolve([_param("p", "object")], _context({"p": value}))


def test_an_array_checks_each_item_against_its_declared_item_type() -> None:
    assert _resolve([_param("p", "array", itemType="integer")], _context({"p": [1, 2]})) == {"p": [1, 2]}

    with pytest.raises(ValidationFailed, match="invalid action parameter types"):
        _resolve([_param("p", "array", itemType="integer")], _context({"p": [1, "two"]}))


def test_an_array_without_a_declared_item_type_is_refused() -> None:
    """An unchecked item type would let any element shape through the parameter contract."""
    with pytest.raises(ValidationFailed, match="invalid action parameter types"):
        _resolve([_param("p", "array")], _context({"p": ["anything"]}))


def test_a_string_is_not_an_array_even_though_it_is_a_sequence() -> None:
    with pytest.raises(ValidationFailed, match="invalid action parameter types"):
        _resolve([_param("p", "array", itemType="string")], _context({"p": "abc"}))


def test_an_object_set_rejects_duplicate_members() -> None:
    """A set with repeats would apply the same edit twice under one Action run."""
    assert _resolve([_param("p", "objectSet", itemType="string")], _context({"p": ["O-1", "O-2"]})) == {
        "p": ["O-1", "O-2"]
    }

    with pytest.raises(ValidationFailed, match="invalid action parameter types"):
        _resolve([_param("p", "objectSet", itemType="string")], _context({"p": ["O-1", "O-1"]}))
