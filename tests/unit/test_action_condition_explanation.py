"""Domain rule tests for redacted Action condition explanations.

The explanation tree is handed to browser UI and to external MCP agents, so the rule that
matters is what it must *not* carry: the runtime values a condition read. A caller who can
see the explanation may not be cleared to see the order total or the requester's attributes,
so sources are reported by reference only. Literals are the exception — they come from the
Action definition, not from tenant data.
"""

from __future__ import annotations

from typing import cast

from foundry_lite.domain.action_runtime.action_condition_explanation import explain_action_condition
from foundry_lite.domain.action_runtime.action_conditions import StaticActionConditionContext


class _Context:
    """Condition context whose values are all distinctive, greppable secrets."""

    def parameter(self, name: str) -> object:
        return f"SECRET-parameter-{name}"

    def object_property(self, name: str) -> object:
        return f"SECRET-property-{name}"

    def current_user(self, attribute: str | None) -> object:
        return f"SECRET-user-{attribute or 'id'}"

    def linked_object_property(self, link_type: str, direction: str, property_name: str, aggregation: str) -> object:
        return f"SECRET-linked-{link_type}-{direction}-{property_name}-{aggregation}"


def _payload_text(payload: object) -> str:
    return repr(payload)


def test_explanation_reports_reference_without_leaking_the_read_value() -> None:
    condition = {
        "op": "eq",
        "left": {"kind": "objectProperty", "property": "status"},
        "right": {"kind": "literal", "value": "PENDING"},
    }

    payload = explain_action_condition(condition, _Context())

    assert payload["kind"] == "comparison"
    assert payload["operator"] == "eq"
    assert payload["left"] == {"kind": "objectProperty", "reference": "status"}
    # The literal is definition data, so it stays visible; the property value must not.
    assert payload["right"] == {"kind": "literal", "literal": "PENDING"}
    assert "SECRET-property-status" not in _payload_text(payload)


def test_every_value_source_kind_is_redacted_to_a_reference() -> None:
    condition = {
        "any": [
            {
                "op": "eq",
                "left": {"kind": "parameter", "parameter": "reason"},
                "right": {"kind": "currentUser", "attribute": "department"},
            },
            {
                "op": "eq",
                "left": {
                    "kind": "linkedObjectProperty",
                    "linkType": "orderItems",
                    "direction": "outgoing",
                    "property": "amount",
                    "aggregation": "count",
                },
                "right": {"kind": "literal", "value": "expected-count"},
            },
        ]
    }

    payload = explain_action_condition(condition, _Context())

    text = _payload_text(payload)
    assert "SECRET" not in text, "no context-read value may reach the explanation tree"
    first, second = cast(list[dict[str, object]], payload["children"])
    assert first["left"] == {"kind": "parameter", "reference": "reason"}
    assert first["right"] == {"kind": "currentUser", "reference": "department"}
    assert second["left"] == {
        "kind": "linkedObjectProperty",
        "reference": {
            "linkType": "orderItems",
            "direction": "outgoing",
            "property": "amount",
            "aggregation": "count",
        },
    }


def test_current_user_reference_defaults_to_id_when_attribute_is_omitted() -> None:
    condition = {
        "op": "eq",
        "left": {"kind": "currentUser"},
        "right": {"kind": "literal", "value": "u-1"},
    }

    payload = explain_action_condition(condition, _Context())

    assert payload["left"] == {"kind": "currentUser", "reference": "id"}


def test_exists_operator_omits_the_right_source() -> None:
    condition = {"op": "exists", "left": {"kind": "parameter", "parameter": "attachment"}}

    payload = explain_action_condition(condition, _Context())

    assert payload["operator"] == "exists"
    assert "right" not in payload


def test_explanation_does_not_turn_a_missing_value_into_a_satisfied_not_condition() -> None:
    """The redacted tree and the real evaluator must make the same fail-closed decision."""
    condition = {
        "not": {
            "op": "eq",
            "left": {"kind": "objectProperty", "property": "missing"},
            "right": {"kind": "literal", "value": "APPROVED"},
        }
    }
    context = StaticActionConditionContext({}, {}, "u-1", ())

    payload = explain_action_condition(condition, context)

    assert payload["isSatisfied"] is False
    children = cast(list[dict[str, object]], payload["children"])
    assert children[0]["isSatisfied"] is False


def test_group_explanation_uses_the_same_unknown_propagation_as_execution() -> None:
    missing = {
        "op": "eq",
        "left": {"kind": "parameter", "parameter": "missing"},
        "right": {"kind": "literal", "value": "yes"},
    }
    condition = {
        "all": [
            {"op": "eq", "left": {"kind": "literal", "value": 1}, "right": {"kind": "literal", "value": 1}},
            missing,
        ]
    }
    context = StaticActionConditionContext({}, {}, "u-1", ())

    payload = explain_action_condition(condition, context)

    assert payload["isSatisfied"] is False


def test_group_paths_are_deterministic_and_address_each_child() -> None:
    condition = {
        "all": [
            {"op": "exists", "left": {"kind": "parameter", "parameter": "a"}},
            {"any": [{"op": "exists", "left": {"kind": "parameter", "parameter": "b"}}]},
        ]
    }

    payload = explain_action_condition(condition, _Context())

    assert payload["path"] == "root"
    assert payload["kind"] == "all"
    first, second = cast(list[dict[str, object]], payload["children"])
    assert first["path"] == "root.all[0]"
    assert second["path"] == "root.all[1]"
    nested = cast(list[dict[str, object]], second["children"])
    assert nested[0]["path"] == "root.all[1].any[0]"


def test_all_requires_every_child_while_any_requires_one() -> None:
    satisfied = {"op": "eq", "left": {"kind": "literal", "value": 1}, "right": {"kind": "literal", "value": 1}}
    unsatisfied = {"op": "eq", "left": {"kind": "literal", "value": 1}, "right": {"kind": "literal", "value": 2}}

    every = explain_action_condition({"all": [satisfied, unsatisfied]}, _Context())
    either = explain_action_condition({"any": [satisfied, unsatisfied]}, _Context())

    assert every["isSatisfied"] is False
    assert either["isSatisfied"] is True


def test_not_inverts_its_child_and_keeps_the_child_visible() -> None:
    satisfied = {"op": "eq", "left": {"kind": "literal", "value": 1}, "right": {"kind": "literal", "value": 1}}

    payload = explain_action_condition({"not": satisfied}, _Context())

    assert payload["kind"] == "not"
    assert payload["isSatisfied"] is False
    child = payload["children"][0]  # type: ignore[index]
    assert child["isSatisfied"] is True
    assert child["path"] == "root.not"


def test_authored_message_is_carried_so_operators_see_why_a_rule_exists() -> None:
    condition = {
        "op": "exists",
        "left": {"kind": "parameter", "parameter": "reason"},
        "message": "a reason is required before approval",
    }

    payload = explain_action_condition(condition, _Context())

    assert payload["message"] == "a reason is required before approval"


def test_explanation_is_stable_across_repeated_evaluation() -> None:
    condition = {
        "all": [
            {"op": "exists", "left": {"kind": "parameter", "parameter": "reason"}},
            {"not": {"op": "exists", "left": {"kind": "objectProperty", "property": "closedAt"}}},
        ]
    }

    first = explain_action_condition(condition, _Context())
    second = explain_action_condition(condition, _Context())

    assert first == second
