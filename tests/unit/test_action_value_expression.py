"""Value-expression AST: parse round-trips and context-driven evaluation."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from foundry_lite.domain.action_runtime.value_expression import (
    CurrentTimeValue,
    CurrentUserValue,
    GeneratedIdValue,
    LiteralValue,
    ObjectPropertyValue,
    ParameterValue,
    PriorRuleOutputValue,
    WebhookResponseValue,
    evaluate_value,
    parse_value_expression,
)
from foundry_lite.domain.errors import ValidationFailed


@dataclass
class _Context:
    params: dict[str, object]

    def parameter(self, name: str) -> object:
        return self.params.get(name)

    def object_property(self, parameter: str, prop: str) -> object:
        return f"{parameter}.{prop}"

    def prior_rule_output(self, rule_id: str, output: str) -> object:
        return f"{rule_id}#{output}"

    def function_output(self, key: str) -> object:
        return f"fn:{key}"

    def current_user(self, attribute: str | None) -> object:
        return "user-1" if attribute is None else f"attr:{attribute}"

    def current_time(self, unit: str) -> str:
        return f"now:{unit}"

    def generated_id(self, strategy: str) -> str:
        return f"gen:{strategy}"

    def webhook_response(self, field: str) -> object:
        return f"hook:{field}"


def test_parse_covers_every_value_source() -> None:
    assert parse_value_expression({"kind": "literal", "value": 7}) == LiteralValue(7)
    assert parse_value_expression({"kind": "parameter", "parameter": "qty"}) == ParameterValue("qty")
    assert parse_value_expression({"kind": "objectProperty", "parameter": "order", "property": "status"}) == (
        ObjectPropertyValue("order", "status")
    )
    assert parse_value_expression({"kind": "priorRuleOutput", "ruleId": "r1", "output": "pk"}) == (
        PriorRuleOutputValue("r1", "pk")
    )
    assert parse_value_expression({"kind": "currentUser"}) == CurrentUserValue(None)
    assert parse_value_expression({"kind": "currentUser", "attribute": "org"}) == CurrentUserValue("org")
    assert parse_value_expression({"kind": "currentTime", "unit": "timestamp"}) == CurrentTimeValue("timestamp")
    assert parse_value_expression({"kind": "generatedId", "strategy": "uuid"}) == GeneratedIdValue("uuid")
    assert parse_value_expression({"kind": "webhookResponse", "field": "ref"}) == WebhookResponseValue("ref")


def test_evaluate_dispatches_each_node_to_the_context() -> None:
    context = _Context(params={"qty": 5})
    assert evaluate_value(LiteralValue("x"), context) == "x"
    assert evaluate_value(ParameterValue("qty"), context) == 5
    assert evaluate_value(ObjectPropertyValue("order", "status"), context) == "order.status"
    assert evaluate_value(PriorRuleOutputValue("r1", "pk"), context) == "r1#pk"
    assert evaluate_value(CurrentUserValue(None), context) == "user-1"
    assert evaluate_value(CurrentUserValue("org"), context) == "attr:org"
    assert evaluate_value(CurrentTimeValue("date"), context) == "now:date"
    assert evaluate_value(GeneratedIdValue("uuid"), context) == "gen:uuid"
    assert evaluate_value(WebhookResponseValue("ref"), context) == "hook:ref"


def test_parse_rejects_unknown_kind_and_missing_fields() -> None:
    with pytest.raises(ValidationFailed, match="unsupported value expression kind"):
        parse_value_expression({"kind": "eval", "expr": "1+1"})
    with pytest.raises(ValidationFailed, match="required"):
        parse_value_expression({"kind": "parameter"})
    with pytest.raises(ValidationFailed, match="unsupported current-time unit"):
        parse_value_expression({"kind": "currentTime", "unit": "epoch"})
