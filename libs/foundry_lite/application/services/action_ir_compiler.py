"""Compile an action type definition into validated Action IR v2.

Dual-read: a definition may declare rules natively under ``rulesV2`` (the v2
shape), or only the legacy ``mutations`` (v1 setProperty). A v1 definition is
normalized into a single ModifyObjectRule whose target is the apply-request
object (bound by the executor under the reserved ``__target__`` parameter), so
existing setProperty actions keep working unchanged while gaining a v2 plan.

This lives in the application layer because it reads the application-owned
``ActionTypeDefinition`` port type; the produced IR is pure domain.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports.ontology_definitions import ActionMutationDefinition, ActionTypeDefinition
from foundry_lite.application.services.action_interface_resolution import (
    require_interface_create_plan_target as require_interface_create_plan_target,
)
from foundry_lite.application.services.action_interface_resolution import (
    resolve_interface_action_definition as resolve_interface_action_definition,
)
from foundry_lite.domain.action_runtime.action_contract import (
    ActionDefinitionV3,
    action_contract_payload,
    compile_action_contract,
)
from foundry_lite.domain.action_runtime.action_ir import (
    ActionDefinitionV2,
    ActionRule,
    CreateLinkRule,
    CreateObjectRule,
    DeleteLinkRule,
    DeleteObjectRule,
    FunctionEditRule,
    ModifyObjectRule,
    PropertyAssignment,
    validate_action_definition,
)
from foundry_lite.domain.action_runtime.action_risk import require_declared_risk_floor, structural_action_risk
from foundry_lite.domain.action_runtime.value_expression import (
    LiteralValue,
    ParameterValue,
    ValueExpression,
    parse_value_expression,
)
from foundry_lite.domain.errors import ValidationFailed

#: Reserved parameter under which the executor binds a normalized v1 action's
#: single apply-request target object reference.
V1_TARGET_PARAMETER = "__target__"


def compile_action_definition(definition: ActionTypeDefinition) -> ActionDefinitionV2:
    """Compile a persisted action definition into validated Action IR v2."""
    contract = compile_action_contract(definition)
    require_declared_risk_floor(structural_action_risk(contract))
    api_name = contract.api_name
    rules: tuple[ActionRule, ...]
    if contract.function is not None:
        rules = (
            FunctionEditRule(
                rule_id="function:edit",
                function_api_name=contract.function.api_name,
                function_version=contract.function.version,
            ),
        )
    else:
        rules = _parse_v2_rules(contract.rules) if contract.rules else _normalize_v1(definition)
    compiled = ActionDefinitionV2(api_name=api_name, rules=rules)
    validate_action_definition(compiled)
    return compiled


def canonical_action_contract(definition: ActionTypeDefinition) -> ActionDefinitionV3:
    """Compile one persisted definition into the canonical v3 contract."""
    return compile_action_contract(definition)


def compile_canonical_action_definition(contract: ActionDefinitionV3) -> ActionDefinitionV2:
    """Compile an already canonical contract into executable Action IR."""
    return compile_action_definition(cast(ActionTypeDefinition, action_contract_payload(contract)))


def _normalize_v1(definition: ActionTypeDefinition) -> tuple[ActionRule, ...]:
    """Fold the v1 setProperty mutation list into one ModifyObjectRule."""
    target_type = str(definition.get("target") or "")
    assignments = tuple(_v1_assignment(mutation) for mutation in definition.get("mutations", ()))
    if not assignments:
        raise ValidationFailed("v1 action has no setProperty mutations", details={"apiName": definition.get("apiName")})
    rule = ModifyObjectRule(
        rule_id="v1:modify",
        object_type=target_type,
        target=ParameterValue(parameter=V1_TARGET_PARAMETER),
        assignments=assignments,
        cardinality="one",
    )
    return (rule,)


def _v1_assignment(mutation: ActionMutationDefinition) -> PropertyAssignment:
    if mutation["type"] != "setProperty":
        raise ValidationFailed("v1 action supports setProperty only", details=dict(mutation))
    return PropertyAssignment(property=mutation["property"], value=_v1_value(mutation))


def _v1_value(mutation: ActionMutationDefinition) -> ValueExpression:
    if "valueFrom" in mutation:
        expression = mutation["valueFrom"]
        if not expression.startswith("params."):
            raise ValidationFailed("unsupported valueFrom expression", details={"expression": expression})
        return ParameterValue(parameter=expression.split(".", 1)[1])
    return LiteralValue(value=mutation.get("value"))


def _parse_v2_rules(rules: Sequence[object]) -> tuple[ActionRule, ...]:
    return tuple(_parse_v2_rule(rule) for rule in rules)


def _parse_v2_rule(payload: object) -> ActionRule:
    if not isinstance(payload, Mapping):
        raise ValidationFailed("action rule must be an object", details={"payload": type(payload).__name__})
    kind = payload.get("kind")
    parser = _RULE_PARSERS.get(str(kind))
    if parser is None:
        raise ValidationFailed("unsupported action rule kind", details={"kind": kind})
    return parser(payload)


def _rule_id(payload: Mapping[str, object]) -> str:
    value = payload.get("ruleId")
    if not isinstance(value, str) or not value:
        raise ValidationFailed("action rule requires a ruleId", details={"kind": payload.get("kind")})
    return value


def _text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationFailed("action rule field is required", details={"field": key})
    return value


def _assignments(payload: Mapping[str, object]) -> tuple[PropertyAssignment, ...]:
    raw = payload.get("assignments")
    if not isinstance(raw, Sequence):
        return ()
    return tuple(_assignment(item) for item in raw)


def _assignment(item: object) -> PropertyAssignment:
    if not isinstance(item, Mapping):
        raise ValidationFailed("assignment must be an object", details={"payload": type(item).__name__})
    value = item.get("value")
    if not isinstance(value, Mapping):
        raise ValidationFailed("assignment requires a value expression", details={"property": item.get("property")})
    return PropertyAssignment(property=_text(item, "property"), value=parse_value_expression(value))


def _on_interface(payload: Mapping[str, object]) -> str | None:
    value = payload.get("onInterface")
    return value if isinstance(value, str) and value else None


def _interface_link_constraint(payload: Mapping[str, object]) -> str | None:
    value = payload.get("interfaceLinkConstraint")
    return value if isinstance(value, str) and value else None


def _link_type(payload: Mapping[str, object]) -> str:
    value = payload.get("linkType")
    if isinstance(value, str) and value:
        return value
    constraint = _interface_link_constraint(payload)
    if constraint is not None:
        return f"__interface__:{constraint}"
    raise ValidationFailed("action rule requires text", details={"field": "linkType"})


def _value_expr(payload: Mapping[str, object], key: str) -> ValueExpression:
    raw = payload.get(key)
    if not isinstance(raw, Mapping):
        raise ValidationFailed("action rule requires a value expression", details={"field": key})
    return parse_value_expression(raw)


def _parse_create_object(payload: Mapping[str, object]) -> ActionRule:
    primary_key_raw = payload.get("primaryKey")
    primary_key = parse_value_expression(primary_key_raw) if isinstance(primary_key_raw, Mapping) else None
    return CreateObjectRule(
        rule_id=_rule_id(payload),
        object_type=_text(payload, "objectType"),
        primary_key=primary_key,
        assignments=_assignments(payload),
        on_interface=_on_interface(payload),
    )


def _modify_object(payload: Mapping[str, object], *, cardinality: str, should_create_if_absent: bool) -> ActionRule:
    return ModifyObjectRule(
        rule_id=_rule_id(payload),
        object_type=_text(payload, "objectType"),
        target=_value_expr(payload, "target"),
        assignments=_assignments(payload),
        cardinality=cardinality,
        should_create_if_absent=should_create_if_absent,
        on_interface=_on_interface(payload),
    )


def _parse_modify_object(payload: Mapping[str, object]) -> ActionRule:
    return _modify_object(payload, cardinality="one", should_create_if_absent=False)


def _parse_modify_objects(payload: Mapping[str, object]) -> ActionRule:
    return _modify_object(payload, cardinality="many", should_create_if_absent=False)


def _parse_create_or_modify_object(payload: Mapping[str, object]) -> ActionRule:
    return _modify_object(payload, cardinality="one", should_create_if_absent=True)


def _delete_object(payload: Mapping[str, object], *, cardinality: str) -> ActionRule:
    return DeleteObjectRule(
        rule_id=_rule_id(payload),
        object_type=_text(payload, "objectType"),
        target=_value_expr(payload, "target"),
        cardinality=cardinality,
        on_interface=_on_interface(payload),
    )


def _parse_delete_object(payload: Mapping[str, object]) -> ActionRule:
    return _delete_object(payload, cardinality="one")


def _parse_delete_objects(payload: Mapping[str, object]) -> ActionRule:
    return _delete_object(payload, cardinality="many")


def _parse_create_link(payload: Mapping[str, object]) -> ActionRule:
    return CreateLinkRule(
        rule_id=_rule_id(payload),
        link_type=_link_type(payload),
        source=_value_expr(payload, "source"),
        target=_value_expr(payload, "target"),
        on_interface=_on_interface(payload),
        interface_link_constraint=_interface_link_constraint(payload),
    )


def _parse_delete_link(payload: Mapping[str, object]) -> ActionRule:
    return DeleteLinkRule(
        rule_id=_rule_id(payload),
        link_type=_link_type(payload),
        source=_value_expr(payload, "source"),
        target=_value_expr(payload, "target"),
        on_interface=_on_interface(payload),
        interface_link_constraint=_interface_link_constraint(payload),
    )


def _parse_function_edit(payload: Mapping[str, object]) -> ActionRule:
    return FunctionEditRule(
        rule_id=_rule_id(payload),
        function_api_name=_text(payload, "functionApiName"),
        function_version=_text(payload, "functionVersion"),
    )


_RULE_PARSERS = {
    "createObject": _parse_create_object,
    "modifyObject": _parse_modify_object,
    "modifyObjects": _parse_modify_objects,
    "createOrModifyObject": _parse_create_or_modify_object,
    "deleteObject": _parse_delete_object,
    "deleteObjects": _parse_delete_objects,
    "createLink": _parse_create_link,
    "deleteLink": _parse_delete_link,
    "functionEdit": _parse_function_edit,
}
