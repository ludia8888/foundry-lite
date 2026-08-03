"""Compile-time structural validation of Action IR v2 rule sets."""

from __future__ import annotations

import pytest
from foundry_lite.domain.action_runtime.action_ir import (
    ActionDefinitionV2,
    CreateLinkRule,
    CreateObjectRule,
    DeleteObjectRule,
    FunctionEditRule,
    ModifyObjectRule,
    PropertyAssignment,
    referenced_prior_rule_ids,
    validate_action_definition,
)
from foundry_lite.domain.action_runtime.value_expression import (
    LiteralValue,
    ParameterValue,
    PriorRuleOutputValue,
)
from foundry_lite.domain.errors import ValidationFailed


def _create(rule_id: str, object_type: str = "Order", pk: str | None = "O-1") -> CreateObjectRule:
    return CreateObjectRule(
        rule_id=rule_id,
        object_type=object_type,
        primary_key=LiteralValue(pk) if pk is not None else None,
        assignments=(PropertyAssignment("status", LiteralValue("NEW")),),
    )


def _modify(rule_id: str, target: object, object_type: str = "Shipment") -> ModifyObjectRule:
    return ModifyObjectRule(
        rule_id=rule_id,
        object_type=object_type,
        target=target,  # type: ignore[arg-type]
        assignments=(PropertyAssignment("state", LiteralValue("PACKED")),),
    )


def test_valid_create_then_link_ordering_passes() -> None:
    definition = ActionDefinitionV2(
        api_name="FulfillOrder",
        rules=(
            _create("mk-shipment", "Shipment"),
            CreateLinkRule(
                rule_id="link",
                link_type="OrderShipment",
                source=ParameterValue("order"),
                target=PriorRuleOutputValue("mk-shipment", "objectId"),
            ),
        ),
    )
    validate_action_definition(definition)  # does not raise


def test_reference_before_create_is_rejected() -> None:
    definition = ActionDefinitionV2(
        api_name="Bad",
        rules=(
            _modify("modify", PriorRuleOutputValue("mk", "objectId")),
            _create("mk", "Shipment"),
        ),
    )
    with pytest.raises(ValidationFailed, match="before it is created"):
        validate_action_definition(definition)


def test_reference_after_delete_is_rejected() -> None:
    definition = ActionDefinitionV2(
        api_name="Bad",
        rules=(
            _create("mk", "Shipment"),
            DeleteObjectRule(rule_id="del", object_type="Shipment", target=PriorRuleOutputValue("mk", "objectId")),
            _modify("modify", PriorRuleOutputValue("mk", "objectId")),
        ),
    )
    with pytest.raises(ValidationFailed, match="after it is deleted"):
        validate_action_definition(definition)


def test_double_create_of_same_literal_pk_is_rejected() -> None:
    definition = ActionDefinitionV2(api_name="Bad", rules=(_create("a", pk="O-1"), _create("b", pk="O-1")))
    with pytest.raises(ValidationFailed, match="created twice"):
        validate_action_definition(definition)


def test_duplicate_rule_ids_are_rejected() -> None:
    definition = ActionDefinitionV2(api_name="Bad", rules=(_create("dup", pk="A"), _create("dup", pk="B")))
    with pytest.raises(ValidationFailed, match="duplicate action rule id"):
        validate_action_definition(definition)


def test_function_rule_must_be_the_only_rule() -> None:
    definition = ActionDefinitionV2(
        api_name="Bad",
        rules=(FunctionEditRule("fn", "computeEdits", "1.0"), _create("also")),
    )
    with pytest.raises(ValidationFailed, match="only rule"):
        validate_action_definition(definition)


def test_empty_rule_set_is_rejected() -> None:
    with pytest.raises(ValidationFailed, match="no rules"):
        validate_action_definition(ActionDefinitionV2(api_name="Empty", rules=()))


def test_referenced_prior_rule_ids_walks_all_expressions() -> None:
    link = CreateLinkRule(
        rule_id="l",
        link_type="T",
        source=PriorRuleOutputValue("a", "objectId"),
        target=PriorRuleOutputValue("b", "objectId"),
    )
    assert referenced_prior_rule_ids(link) == frozenset({"a", "b"})
