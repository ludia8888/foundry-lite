"""v1->v2 normalization and native v2 rule parsing."""

from __future__ import annotations

import pytest
from foundry_lite.application.services.action_ir_compiler import (
    V1_TARGET_PARAMETER,
    compile_action_definition,
)
from foundry_lite.domain.action_runtime.action_ir import (
    CreateLinkRule,
    CreateObjectRule,
    DeleteLinkRule,
    DeleteObjectRule,
    FunctionEditRule,
    ModifyObjectRule,
)
from foundry_lite.domain.action_runtime.value_expression import (
    LiteralValue,
    ParameterValue,
    PriorRuleOutputValue,
)
from foundry_lite.domain.errors import ValidationFailed


def test_v1_setproperty_normalizes_to_one_modify_rule() -> None:
    definition = {
        "apiName": "ApproveOrder",
        "target": "Order",
        "mutations": [
            {"type": "setProperty", "property": "status", "value": "APPROVED"},
            {"type": "setProperty", "property": "reason", "valueFrom": "params.reason"},
        ],
    }
    compiled = compile_action_definition(definition)  # type: ignore[arg-type]
    assert len(compiled.rules) == 1
    rule = compiled.rules[0]
    assert isinstance(rule, ModifyObjectRule)
    assert rule.object_type == "Order"
    assert rule.target == ParameterValue(V1_TARGET_PARAMETER)
    assert rule.assignments[0].property == "status"
    assert rule.assignments[0].value == LiteralValue("APPROVED")
    assert rule.assignments[1].value == ParameterValue("reason")


def test_v1_rejects_non_setproperty_and_bad_valuefrom() -> None:
    with pytest.raises(ValidationFailed, match="setProperty only"):
        compile_action_definition(
            {"apiName": "x", "target": "Order", "mutations": [{"type": "createObject", "property": "p"}]}
        )  # type: ignore[arg-type]
    with pytest.raises(ValidationFailed, match="unsupported valueFrom"):
        compile_action_definition(
            {
                "apiName": "x",
                "target": "Order",
                "mutations": [{"type": "setProperty", "property": "p", "valueFrom": "obj.status"}],
            }  # type: ignore[arg-type]
        )


def test_v1_rejects_an_action_without_mutations() -> None:
    with pytest.raises(ValidationFailed, match="no setProperty mutations"):
        compile_action_definition({"apiName": "x", "target": "Order", "mutations": []})  # type: ignore[arg-type]


def test_native_v2_multi_rule_definition_parses_and_validates() -> None:
    definition = {
        "apiName": "FulfillOrder",
        "rulesV2": [
            {
                "kind": "modifyObject",
                "ruleId": "close-order",
                "objectType": "Order",
                "target": {"kind": "parameter", "parameter": "order"},
                "assignments": [{"property": "status", "value": {"kind": "literal", "value": "FULFILLED"}}],
            },
            {
                "kind": "createObject",
                "ruleId": "mk-shipment",
                "objectType": "Shipment",
                "primaryKey": {"kind": "generatedId", "strategy": "uuid"},
                "assignments": [{"property": "carrier", "value": {"kind": "parameter", "parameter": "carrier"}}],
            },
            {
                "kind": "createLink",
                "ruleId": "link",
                "linkType": "OrderShipment",
                "source": {"kind": "parameter", "parameter": "order"},
                "target": {"kind": "priorRuleOutput", "ruleId": "mk-shipment", "output": "objectId"},
            },
        ],
    }
    compiled = compile_action_definition(definition)  # type: ignore[arg-type]
    kinds = [type(rule).__name__ for rule in compiled.rules]
    assert kinds == ["ModifyObjectRule", "CreateObjectRule", "CreateLinkRule"]
    link = compiled.rules[2]
    assert isinstance(link, CreateLinkRule)
    assert link.target == PriorRuleOutputValue("mk-shipment", "objectId")


def test_v2_cardinality_and_create_or_modify_are_kind_derived() -> None:
    definition = {
        "apiName": "Batch",
        "rulesV2": [
            {
                "kind": "modifyObjects",
                "ruleId": "bulk",
                "objectType": "Order",
                "target": {"kind": "parameter", "parameter": "orders"},
                "assignments": [{"property": "status", "value": {"kind": "literal", "value": "X"}}],
            },
            {
                "kind": "createOrModifyObject",
                "ruleId": "upsert",
                "objectType": "Customer",
                "target": {"kind": "parameter", "parameter": "customer"},
                "assignments": [{"property": "tier", "value": {"kind": "literal", "value": "GOLD"}}],
            },
        ],
    }
    compiled = compile_action_definition(definition)  # type: ignore[arg-type]
    bulk, upsert = compiled.rules
    assert isinstance(bulk, ModifyObjectRule) and bulk.cardinality == "many" and bulk.should_create_if_absent is False
    assert (
        isinstance(upsert, ModifyObjectRule) and upsert.cardinality == "one" and upsert.should_create_if_absent is True
    )


def test_v2_ordering_violation_surfaces_from_compile() -> None:
    definition = {
        "apiName": "Bad",
        "rulesV2": [
            {
                "kind": "createLink",
                "ruleId": "link",
                "linkType": "T",
                "source": {"kind": "parameter", "parameter": "a"},
                "target": {"kind": "priorRuleOutput", "ruleId": "later", "output": "objectId"},
            },
            {
                "kind": "createObject",
                "ruleId": "later",
                "objectType": "Shipment",
                "primaryKey": {"kind": "generatedId", "strategy": "uuid"},
                "assignments": [],
            },
        ],
    }
    with pytest.raises(ValidationFailed, match="before it is created"):
        compile_action_definition(definition)  # type: ignore[arg-type]


def test_unsupported_rule_kind_is_rejected() -> None:
    with pytest.raises(ValidationFailed, match="unsupported action rule kind"):
        compile_action_definition({"apiName": "x", "rulesV2": [{"kind": "rawSql", "ruleId": "r"}]})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("rule", "message"),
    [
        ("not-an-object", "action rule must be an object"),
        (
            {
                "kind": "modifyObject",
                "objectType": "Order",
                "target": {"kind": "parameter", "parameter": "order"},
                "assignments": [],
            },
            "requires a ruleId",
        ),
        ({"kind": "createObject", "ruleId": "create", "assignments": []}, "field is required"),
        (
            {
                "kind": "modifyObject",
                "ruleId": "modify",
                "objectType": "Order",
                "target": {"kind": "parameter", "parameter": "order"},
                "assignments": ["not-an-object"],
            },
            "assignment must be an object",
        ),
        (
            {
                "kind": "modifyObject",
                "ruleId": "modify",
                "objectType": "Order",
                "target": {"kind": "parameter", "parameter": "order"},
                "assignments": [{"property": "status"}],
            },
            "requires a value expression",
        ),
        (
            {
                "kind": "modifyObject",
                "ruleId": "modify",
                "objectType": "Order",
                "assignments": [],
            },
            "requires a value expression",
        ),
    ],
)
def test_v2_rejects_malformed_rule_boundaries(rule: object, message: str) -> None:
    with pytest.raises(ValidationFailed, match=message):
        compile_action_definition({"apiName": "x", "rulesV2": [rule]})  # type: ignore[arg-type]


def test_v2_non_sequence_assignments_are_treated_as_empty() -> None:
    compiled = compile_action_definition(
        {  # type: ignore[arg-type]
            "apiName": "Create",
            "rulesV2": [
                {
                    "kind": "createObject",
                    "ruleId": "create",
                    "objectType": "Order",
                    "assignments": 42,
                }
            ],
        }
    )
    rule = compiled.rules[0]
    assert isinstance(rule, CreateObjectRule) and rule.assignments == ()


def test_v2_delete_and_function_rule_kinds_parse() -> None:
    deleted = compile_action_definition(
        {  # type: ignore[arg-type]
            "apiName": "Delete",
            "rulesV2": [
                {
                    "kind": "deleteObject",
                    "ruleId": "one",
                    "objectType": "Order",
                    "target": {"kind": "parameter", "parameter": "order"},
                },
                {
                    "kind": "deleteObjects",
                    "ruleId": "many",
                    "objectType": "Order",
                    "target": {"kind": "parameter", "parameter": "orders"},
                },
                {
                    "kind": "deleteLink",
                    "ruleId": "link",
                    "linkType": "OrderCustomer",
                    "source": {"kind": "parameter", "parameter": "order"},
                    "target": {"kind": "parameter", "parameter": "customer"},
                },
            ],
        }
    )
    assert isinstance(deleted.rules[0], DeleteObjectRule) and deleted.rules[0].cardinality == "one"
    assert isinstance(deleted.rules[1], DeleteObjectRule) and deleted.rules[1].cardinality == "many"
    assert isinstance(deleted.rules[2], DeleteLinkRule)

    function = compile_action_definition(
        {  # type: ignore[arg-type]
            "apiName": "FunctionBacked",
            "rulesV2": [
                {
                    "kind": "functionEdit",
                    "ruleId": "function",
                    "functionApiName": "buildEdits",
                    "functionVersion": "1.0.0",
                }
            ],
        }
    )
    assert isinstance(function.rules[0], FunctionEditRule)


def test_created_object_default_pk_is_allowed() -> None:
    compiled = compile_action_definition(
        {  # type: ignore[arg-type]
            "apiName": "Create",
            "rulesV2": [{"kind": "createObject", "ruleId": "c", "objectType": "Order", "assignments": []}],
        }
    )
    rule = compiled.rules[0]
    assert isinstance(rule, CreateObjectRule) and rule.primary_key is None
