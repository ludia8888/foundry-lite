"""Domain rule tests for Action Contract v3 presentation metadata.

Builder, generated SDKs, runtime forms, and MCP clients all read this normalization, so the
rules that matter are the ones that stop those surfaces from diverging: every declared
parameter reaches exactly one form section, and an Action is only offered as an inline object
edit when its shape genuinely reduces to one property written from one primitive parameter.
"""

from __future__ import annotations

from typing import cast

import pytest
from foundry_lite.domain.action_runtime.action_presentation import (
    action_form_layout_payload,
    action_inline_eligibility,
    compile_action_form_layout,
)
from foundry_lite.domain.errors import ValidationFailed


def _modify_rule(**overrides: object) -> dict[str, object]:
    rule: dict[str, object] = {
        "kind": "modifyObject",
        "objectType": "Order",
        "target": {"kind": "parameter", "parameter": "__target__"},
        "assignments": [{"property": "status", "value": {"kind": "parameter", "parameter": "status"}}],
    }
    rule.update(overrides)
    return rule


def _eligibility(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "target_kind": "object",
        "target_api_name": "Order",
        "parameter_types": {"status": "string"},
        "rules": (_modify_rule(),),
        "has_function": False,
        "effect_count": 0,
    }
    kwargs.update(overrides)
    return action_inline_eligibility(**kwargs)  # type: ignore[arg-type]


def _reasons(payload: dict[str, object]) -> list[str]:
    return cast(list[str], payload["reasons"])


# --- form layout -------------------------------------------------------------------


def test_absent_layout_falls_back_to_one_section_holding_every_parameter() -> None:
    layout = compile_action_form_layout(None, ("reason", "amount"))

    assert len(layout.sections) == 1
    section = layout.sections[0]
    assert section.section_id == "parameters"
    assert section.parameter_names == ("reason", "amount")


def test_parameters_left_out_of_declared_sections_are_appended_not_dropped() -> None:
    raw = {"sections": [{"id": "main", "title": "Main", "parameterNames": ["reason"]}]}

    layout = compile_action_form_layout(raw, ("reason", "amount"))

    assert [section.section_id for section in layout.sections] == ["main", "other-parameters"]
    assert layout.sections[1].parameter_names == ("amount",)


def test_declared_section_order_is_preserved_for_every_reading_surface() -> None:
    raw = {
        "sections": [
            {"id": "second", "title": "Second", "parameterNames": ["amount"]},
            {"id": "first", "title": "First", "parameterNames": ["reason"]},
        ]
    }

    layout = compile_action_form_layout(raw, ("reason", "amount"))

    assert [section.section_id for section in layout.sections] == ["second", "first"]


def test_duplicate_section_ids_are_rejected() -> None:
    raw = {
        "sections": [
            {"id": "main", "title": "A", "parameterNames": ["reason"]},
            {"id": "main", "title": "B", "parameterNames": ["amount"]},
        ]
    }

    with pytest.raises(ValidationFailed, match="section ids must be unique"):
        compile_action_form_layout(raw, ("reason", "amount"))


def test_a_parameter_may_not_appear_in_two_sections() -> None:
    raw = {
        "sections": [
            {"id": "a", "title": "A", "parameterNames": ["reason"]},
            {"id": "b", "title": "B", "parameterNames": ["reason"]},
        ]
    }

    with pytest.raises(ValidationFailed, match="only one form section"):
        compile_action_form_layout(raw, ("reason",))


def test_sections_may_not_place_undeclared_parameters() -> None:
    raw = {"sections": [{"id": "a", "title": "A", "parameterNames": ["ghost"]}]}

    with pytest.raises(ValidationFailed, match="unknown action parameters"):
        compile_action_form_layout(raw, ("reason",))


def test_visibility_conditions_may_not_reference_undeclared_parameters() -> None:
    raw = {
        "sections": [
            {
                "id": "a",
                "title": "A",
                "parameterNames": ["reason"],
                "visibleWhen": {"op": "exists", "left": {"kind": "parameter", "parameter": "ghost"}},
            }
        ]
    }

    with pytest.raises(ValidationFailed, match="unknown action parameters"):
        compile_action_form_layout(raw, ("reason",))


def test_initially_collapsed_section_must_also_be_collapsible() -> None:
    raw = {
        "sections": [
            {"id": "a", "title": "A", "parameterNames": ["reason"], "isInitiallyCollapsed": True},
        ]
    }

    with pytest.raises(ValidationFailed, match="must be collapsible"):
        compile_action_form_layout(raw, ("reason",))


@pytest.mark.parametrize("columns", [0, 3, True, "2"])
def test_columns_outside_the_one_or_two_contract_are_rejected(columns: object) -> None:
    raw = {"sections": [{"id": "a", "title": "A", "columns": columns, "parameterNames": ["reason"]}]}

    with pytest.raises(ValidationFailed, match="columns must be 1 or 2"):
        compile_action_form_layout(raw, ("reason",))


def test_layout_payload_is_the_deterministic_public_shape() -> None:
    layout = compile_action_form_layout(None, ("reason",))

    payload = action_form_layout_payload(layout)

    assert payload == {
        "sections": [
            {
                "id": "parameters",
                "title": "Parameters",
                "description": None,
                "columns": 1,
                "isCollapsible": False,
                "isInitiallyCollapsed": False,
                "parameterNames": ["reason"],
                "visibleWhen": None,
            }
        ]
    }


# --- inline eligibility ------------------------------------------------------------


def test_single_property_modify_action_is_inline_eligible_with_its_binding() -> None:
    payload = _eligibility()

    assert payload["isEligible"] is True
    assert _reasons(payload) == []
    assert payload["propertyApiName"] == "status"
    assert payload["parameterApiName"] == "status"
    assert payload["parameterType"] == "string"


def test_interface_target_is_not_inline_eligible() -> None:
    payload = _eligibility(target_kind="interface")

    assert payload["isEligible"] is False
    assert "interface targets require concrete-type resolution" in _reasons(payload)


def test_function_backed_action_is_not_inline_eligible() -> None:
    payload = _eligibility(has_function=True)

    assert payload["isEligible"] is False
    assert "function-backed Actions require the full execution runtime" in _reasons(payload)


def test_action_with_side_effects_is_not_inline_eligible() -> None:
    payload = _eligibility(effect_count=1)

    assert payload["isEligible"] is False
    assert "Actions with side effects require the full execution runtime" in _reasons(payload)


def test_rule_must_modify_the_declared_target_object_type() -> None:
    payload = _eligibility(rules=(_modify_rule(objectType="Customer"),))

    assert payload["isEligible"] is False
    assert "inline edit rule must modify the declared Action target" in _reasons(payload)


def test_rule_must_target_the_selected_object_not_an_arbitrary_parameter() -> None:
    payload = _eligibility(rules=(_modify_rule(target={"kind": "parameter", "parameter": "other"}),))

    assert payload["isEligible"] is False
    assert "inline edit rule must target the selected object" in _reasons(payload)


def test_object_set_cardinality_is_not_inline_eligible() -> None:
    payload = _eligibility(rules=(_modify_rule(cardinality="many"),))

    assert payload["isEligible"] is False
    assert "inline edit cannot target an object set" in _reasons(payload)


def test_create_if_absent_is_not_inline_eligible() -> None:
    payload = _eligibility(rules=(_modify_rule(shouldCreateIfAbsent=True),))

    assert payload["isEligible"] is False
    assert "inline edit cannot create a missing object" in _reasons(payload)


def test_more_than_one_rule_is_not_inline_eligible() -> None:
    payload = _eligibility(rules=(_modify_rule(), _modify_rule()))

    assert payload["isEligible"] is False
    assert "inline edit requires exactly one modifyObject rule" in _reasons(payload)


def test_more_than_one_assignment_is_not_inline_eligible() -> None:
    rule = _modify_rule(
        assignments=[
            {"property": "status", "value": {"kind": "parameter", "parameter": "status"}},
            {"property": "note", "value": {"kind": "parameter", "parameter": "status"}},
        ]
    )

    payload = _eligibility(rules=(rule,))

    assert payload["isEligible"] is False
    assert "inline edit requires exactly one property assignment" in _reasons(payload)


def test_assignment_value_must_come_from_an_action_parameter() -> None:
    rule = _modify_rule(assignments=[{"property": "status", "value": {"kind": "literal", "value": "DONE"}}])

    payload = _eligibility(rules=(rule,))

    assert payload["isEligible"] is False
    assert "inline edit assignment value must come from its Action parameter" in _reasons(payload)


def test_assignment_referencing_an_undeclared_parameter_is_rejected() -> None:
    rule = _modify_rule(assignments=[{"property": "status", "value": {"kind": "parameter", "parameter": "ghost"}}])

    payload = _eligibility(rules=(rule,))

    assert payload["isEligible"] is False
    assert "inline edit assignment references an undeclared Action parameter" in _reasons(payload)


def test_ineligible_action_never_leaks_a_partial_binding() -> None:
    payload = _eligibility(has_function=True)

    assert "propertyApiName" not in payload
    assert "parameterApiName" not in payload
    assert "parameterType" not in payload
