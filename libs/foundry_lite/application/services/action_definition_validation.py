"""Cross-resource validation for canonical Action definitions during activation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.services.action_function_contract_validation import (
    require_action_function_contract,
)
from foundry_lite.application.services.action_ir_compiler import compile_action_definition
from foundry_lite.application.services.ontology_yaml import (
    YamlObject,
    action_type_definition,
    mapping_sequence,
    optional_bool,
    required_str,
)
from foundry_lite.domain.action_runtime.action_conditions import (
    LinkedObjectPropertyReference,
    referenced_condition_object_properties,
    referenced_linked_object_properties,
)
from foundry_lite.domain.action_runtime.action_contract import (
    ActionDefinitionV3,
    ActionParameterV3,
    compile_action_contract,
)
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.scalar_values import matches_scalar_type


def validate_yaml_action_definitions(
    definition: YamlObject,
    object_defs: Mapping[str, YamlObject],
    link_defs: Mapping[str, YamlObject],
) -> None:
    """Validate references, editable properties, simple value types, and sensitive risk."""
    functions = {required_str(item, "apiName"): item for item in mapping_sequence(definition, "functionTypes")}
    interfaces = {required_str(item, "apiName") for item in mapping_sequence(definition, "interfaces")}
    actions = mapping_sequence(definition, "actionTypes")
    action_names = {required_str(item, "apiName") for item in actions}
    for action in actions:
        persisted = action_type_definition(action)
        contract = compile_action_contract(persisted)
        if contract.rules or contract.function is not None:
            compile_action_definition(persisted)
        _require_target(contract, object_defs, interfaces)
        require_action_function_contract(contract, functions)
        _validate_revert_policy(contract, action_names)
        _validate_submission_criteria(contract, object_defs, link_defs)
        for rule in contract.rules:
            _validate_rule(contract, rule, object_defs, link_defs, functions, definition)


def _validate_revert_policy(contract: ActionDefinitionV3, action_names: set[str]) -> None:
    compensation = contract.revert_policy.get("compensationAction")
    if compensation is None:
        return
    if not isinstance(compensation, str) or not compensation.strip():
        raise ValidationFailed(
            "action compensation reference must be a non-empty Action API name",
            details={"actionType": contract.api_name},
        )
    if contract.revert_policy.get("enabled") is not True:
        raise ValidationFailed(
            "action compensation requires revert to be enabled",
            details={"actionType": contract.api_name, "compensationAction": compensation},
        )
    if compensation == contract.api_name or compensation not in action_names:
        raise ValidationFailed(
            "action compensation reference was not found or references itself",
            details={"actionType": contract.api_name, "compensationAction": compensation},
        )


def _validate_submission_criteria(
    contract: ActionDefinitionV3,
    object_defs: Mapping[str, YamlObject],
    link_defs: Mapping[str, YamlObject],
) -> None:
    criteria = contract.submission_criteria
    if criteria is None:
        return
    target_properties = _target_property_names(contract, object_defs)
    if missing := referenced_condition_object_properties(criteria) - target_properties:
        raise ValidationFailed(
            "action submission criteria target property was not found",
            details={"actionType": contract.api_name, "properties": sorted(missing)},
        )
    for reference in sorted(referenced_linked_object_properties(criteria)):
        _validate_linked_criteria_reference(contract, reference, object_defs, link_defs)


def _target_property_names(contract: ActionDefinitionV3, object_defs: Mapping[str, YamlObject]) -> set[str]:
    if contract.target.kind == "object":
        return _object_property_names(object_defs[contract.target.api_name])
    implementations = [
        item for item in object_defs.values() if contract.target.api_name in _strings(item.get("implements"))
    ]
    if not implementations:
        return set()
    shared = [_object_property_names(item) for item in implementations]
    return set.intersection(*shared)


def _validate_linked_criteria_reference(
    contract: ActionDefinitionV3,
    reference: LinkedObjectPropertyReference,
    object_defs: Mapping[str, YamlObject],
    link_defs: Mapping[str, YamlObject],
) -> None:
    link = link_defs.get(reference.link_type)
    if link is None:
        raise ValidationFailed(
            "action submission criteria link type was not found",
            details={"actionType": contract.api_name, "linkType": reference.link_type},
        )
    anchor_field, linked_field = ("from", "to") if reference.direction == "outgoing" else ("to", "from")
    anchor_type = required_str(link, anchor_field)
    linked_type = required_str(link, linked_field)
    if anchor_type not in object_defs or linked_type not in object_defs:
        raise ValidationFailed(
            "action submission criteria link endpoint was not found",
            details={"linkType": reference.link_type, "anchorType": anchor_type, "linkedType": linked_type},
        )
    _require_criteria_anchor(contract, anchor_type, object_defs, reference)
    properties = _object_property_names(object_defs[linked_type])
    if reference.property_name not in properties:
        raise ValidationFailed(
            "action submission criteria linked property was not found",
            details={
                "actionType": contract.api_name,
                "linkType": reference.link_type,
                "objectType": linked_type,
                "property": reference.property_name,
            },
        )


def _require_criteria_anchor(
    contract: ActionDefinitionV3,
    anchor_type: str,
    object_defs: Mapping[str, YamlObject],
    reference: LinkedObjectPropertyReference,
) -> None:
    is_target = contract.target.kind == "object" and anchor_type == contract.target.api_name
    is_interface_target = contract.target.kind == "interface" and contract.target.api_name in _strings(
        object_defs[anchor_type].get("implements")
    )
    if not is_target and not is_interface_target:
        raise ValidationFailed(
            "action submission criteria link is not anchored at the Action target",
            details={
                "actionType": contract.api_name,
                "linkType": reference.link_type,
                "direction": reference.direction,
                "anchorObjectType": anchor_type,
            },
        )


def _object_property_names(definition: YamlObject) -> set[str]:
    return {required_str(item, "apiName") for item in mapping_sequence(definition, "properties")}


def _require_target(
    contract: ActionDefinitionV3,
    object_defs: Mapping[str, YamlObject],
    interfaces: set[str],
) -> None:
    allowed = set(object_defs) if contract.target.kind == "object" else interfaces
    if contract.target.api_name not in allowed:
        raise ValidationFailed(
            "action target reference was not found",
            details={"actionType": contract.api_name, "target": contract.target.api_name},
        )


def _validate_rule(
    contract: ActionDefinitionV3,
    rule: Mapping[str, object],
    object_defs: Mapping[str, YamlObject],
    link_defs: Mapping[str, YamlObject],
    functions: Mapping[str, YamlObject],
    definition: YamlObject,
) -> None:
    kind = str(rule.get("kind") or "")
    if kind == "functionEdit":
        function_name = rule.get("functionApiName")
        if isinstance(function_name, str) and function_name in functions:
            return
        raise ValidationFailed(
            "action function rule reference was not found",
            details={"actionType": contract.api_name, "function": function_name},
        )
    if kind in {"createLink", "deleteLink"}:
        _require_link(contract, rule, link_defs, definition)
        return
    object_type = rule.get("objectType")
    if contract.target.kind == "interface" and object_type == contract.target.api_name:
        _require_generic_interface_object_rule(contract, kind, rule, definition)
        return
    if not isinstance(object_type, str) or object_type not in object_defs:
        raise ValidationFailed(
            "action rule object type reference was not found",
            details={"actionType": contract.api_name, "objectType": object_type},
        )
    _require_rule_interface(contract, rule, object_type, object_defs[object_type], definition)
    _validate_assignments(contract, kind, rule, object_defs[object_type])


def _require_generic_interface_object_rule(
    contract: ActionDefinitionV3,
    kind: str,
    rule: Mapping[str, object],
    definition: YamlObject,
) -> None:
    if rule.get("onInterface") != contract.target.api_name:
        raise ValidationFailed(
            "interface Action object rules must declare the target interface",
            details={"actionType": contract.api_name, "ruleInterface": rule.get("onInterface")},
        )
    if kind == "createObject" and not isinstance(rule.get("primaryKey"), Mapping):
        raise ValidationFailed(
            "interface create rule requires an explicit primary key value",
            details={"actionType": contract.api_name, "ruleId": rule.get("ruleId")},
        )
    shared = _interface_properties(definition, contract.target.api_name)
    assigned = {str(item.get("property")) for item in _sequence(rule.get("assignments")) if isinstance(item, Mapping)}
    if invalid := assigned - shared:
        raise ValidationFailed(
            "interface Action may edit shared interface properties only",
            details={"interface": contract.target.api_name, "properties": sorted(invalid)},
        )


def _require_rule_interface(
    contract: ActionDefinitionV3,
    rule: Mapping[str, object],
    object_type: str,
    object_def: YamlObject,
    definition: YamlObject,
) -> None:
    interface = rule.get("onInterface")
    if contract.target.kind == "interface" and interface != contract.target.api_name:
        raise ValidationFailed(
            "interface Action object rules must declare the target interface",
            details={"actionType": contract.api_name, "ruleInterface": interface},
        )
    if interface is None:
        return
    declared = set(_strings(object_def.get("implements")))
    if not isinstance(interface, str) or interface not in declared:
        raise ValidationFailed(
            "action rule concrete object type does not implement its interface",
            details={"objectType": object_type, "interface": interface},
        )
    shared = _interface_properties(definition, interface)
    assigned = {str(item.get("property")) for item in _sequence(rule.get("assignments")) if isinstance(item, Mapping)}
    if invalid := assigned - shared:
        raise ValidationFailed(
            "interface Action may edit shared interface properties only",
            details={"interface": interface, "properties": sorted(invalid)},
        )


def _interface_properties(definition: YamlObject, interface: str) -> set[str]:
    for item in mapping_sequence(definition, "interfaces"):
        if required_str(item, "apiName") == interface:
            return {required_str(prop, "apiName") for prop in mapping_sequence(item, "properties")}
    raise ValidationFailed("action rule interface reference was not found", details={"interface": interface})


def _strings(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        return ()
    return tuple(item for item in raw if isinstance(item, str))


def _require_link(
    contract: ActionDefinitionV3,
    rule: Mapping[str, object],
    link_defs: Mapping[str, YamlObject],
    definition: YamlObject,
) -> None:
    constraint = rule.get("interfaceLinkConstraint")
    if constraint is not None:
        _require_interface_link_rule(contract, rule, constraint, definition)
        return
    link_type = rule.get("linkType")
    if isinstance(link_type, str) and link_type in link_defs:
        return
    raise ValidationFailed(
        "action rule link type reference was not found",
        details={"actionType": contract.api_name, "linkType": link_type},
    )


def _require_interface_link_rule(
    contract: ActionDefinitionV3,
    rule: Mapping[str, object],
    constraint: object,
    definition: YamlObject,
) -> None:
    if contract.target.kind != "interface" or rule.get("onInterface") != contract.target.api_name:
        raise ValidationFailed(
            "interface link rule must bind the Action target interface",
            details={"actionType": contract.api_name, "interface": rule.get("onInterface")},
        )
    if "linkType" in rule:
        raise ValidationFailed(
            "interface link rule cannot also declare a concrete link type",
            details={"actionType": contract.api_name, "ruleId": rule.get("ruleId")},
        )
    interface = _interface_definition(definition, contract.target.api_name)
    names = {required_str(item, "apiName") for item in mapping_sequence(interface, "linkConstraints")}
    if not isinstance(constraint, str) or constraint not in names:
        raise ValidationFailed(
            "interface link constraint reference was not found",
            details={"interface": contract.target.api_name, "constraint": constraint},
        )


def _interface_definition(definition: YamlObject, api_name: str) -> YamlObject:
    for interface in mapping_sequence(definition, "interfaces"):
        if required_str(interface, "apiName") == api_name:
            return interface
    raise ValidationFailed("action rule interface reference was not found", details={"interface": api_name})


def _validate_assignments(
    contract: ActionDefinitionV3,
    kind: str,
    rule: Mapping[str, object],
    object_def: YamlObject,
) -> None:
    properties = {required_str(item, "apiName"): item for item in mapping_sequence(object_def, "properties")}
    parameters = {item.api_name: item for item in contract.parameters}
    response_fields = _before_effect_response_fields(contract)
    for raw in _sequence(rule.get("assignments")):
        assignment = _mapping(raw, "action assignment")
        name = assignment.get("property")
        if not isinstance(name, str) or name not in properties:
            raise ValidationFailed("action assignment property was not found", details={"property": name})
        prop = properties[name]
        if kind != "createObject" and not optional_bool(prop, "editable", False):
            raise ValidationFailed("action assignment property must be editable", details={"property": name})
        _require_assignment_type(name, prop, assignment.get("value"), parameters, response_fields)
        if _is_sensitive(prop) and contract.risk_level != "high":
            raise ValidationFailed(
                "sensitive property edits require high action risk",
                details={"actionType": contract.api_name, "property": name},
            )


def _require_assignment_type(
    property_name: str,
    property_definition: YamlObject,
    raw_value: object,
    parameters: Mapping[str, ActionParameterV3],
    response_fields: Mapping[str, str],
) -> None:
    if not isinstance(raw_value, Mapping):
        return
    kind = raw_value.get("kind")
    if kind == "parameter":
        _require_parameter_assignment_type(property_name, property_definition, raw_value, parameters)
    elif kind == "literal":
        _require_literal_assignment_type(property_name, property_definition, raw_value)
    elif kind == "webhookResponse":
        _require_webhook_assignment_type(property_name, property_definition, raw_value, response_fields)


def _require_parameter_assignment_type(
    property_name: str,
    property_definition: YamlObject,
    raw_value: Mapping[object, object],
    parameters: Mapping[str, ActionParameterV3],
) -> None:
    actual = parameters.get(str(raw_value.get("parameter")))
    actual_type = _parameter_effective_type(actual)
    property_type = required_str(property_definition, "type")
    if actual_type is not None and not _compatible_types(property_type, actual_type):
        raise ValidationFailed(
            "action assignment parameter type does not match property",
            details={"property": property_name, "propertyType": property_type, "parameterType": actual_type},
        )
    _require_media_assignment(property_name, property_definition, actual)


def _require_literal_assignment_type(
    property_name: str,
    property_definition: YamlObject,
    raw_value: Mapping[object, object],
) -> None:
    property_type = required_str(property_definition, "type")
    value = raw_value.get("value")
    is_nullable = optional_bool(property_definition, "nullable", True)
    if not _literal_matches(property_type, value, is_nullable=is_nullable):
        raise ValidationFailed(
            "action assignment literal type does not match property",
            details={"property": property_name, "propertyType": property_type},
        )


def _require_webhook_assignment_type(
    property_name: str,
    property_definition: YamlObject,
    raw_value: Mapping[object, object],
    response_fields: Mapping[str, str],
) -> None:
    actual_type = response_fields.get(str(raw_value.get("field") or ""))
    property_type = required_str(property_definition, "type")
    if actual_type is not None and not _compatible_types(property_type, actual_type):
        raise ValidationFailed(
            "action webhook response type does not match property",
            details={"property": property_name, "propertyType": property_type, "responseType": actual_type},
        )


def _before_effect_response_fields(contract: ActionDefinitionV3) -> Mapping[str, str]:
    before = next((effect for effect in contract.effects if effect.phase == "before_commit"), None)
    return before.response_fields if before is not None else {}


def _parameter_effective_type(parameter: ActionParameterV3 | None) -> str | None:
    if parameter is None:
        return None
    data_type = str(parameter.data_type)
    if data_type in {"array", "objectSet"}:
        return str(parameter.metadata.get("itemType") or data_type)
    return data_type


def _require_media_assignment(
    property_name: str,
    prop: YamlObject,
    parameter: ActionParameterV3 | None,
) -> None:
    property_type = required_str(prop, "type")
    if property_type not in {"media_reference", "attachment"} or parameter is None:
        return
    expected_media_set = required_str(prop, "mediaSet")
    actual_media_set = parameter.metadata.get("mediaSet")
    if actual_media_set != expected_media_set:
        raise ValidationFailed(
            "action media parameter must use the property Media Set",
            details={
                "property": property_name,
                "expectedMediaSet": expected_media_set,
                "actualMediaSet": actual_media_set,
            },
        )
    is_collection = str(parameter.data_type) in {"array", "objectSet"}
    if property_type == "media_reference" and is_collection:
        raise ValidationFailed(
            "media_reference property accepts one media parameter",
            details={"property": property_name},
        )
    if property_type == "attachment" and optional_bool(prop, "allowMultiple", False) != is_collection:
        raise ValidationFailed(
            "attachment parameter multiplicity must match its property",
            details={"property": property_name, "allowMultiple": optional_bool(prop, "allowMultiple", False)},
        )


def _compatible_types(property_type: str, parameter_type: str) -> bool:
    aliases = {
        "integer": {"integer", "long"},
        "float": {"integer", "long", "float"},
        "string": {"string", "date", "timestamp"},
        "date": {"date"},
        "timestamp": {"timestamp"},
        "media_reference": {"media"},
        "attachment": {"attachment"},
        "boolean": {"boolean"},
    }
    return parameter_type in aliases.get(property_type, {property_type})


def _literal_matches(property_type: str, value: object, *, is_nullable: bool) -> bool:
    if value is None:
        return is_nullable
    return matches_scalar_type(property_type, value)


def _is_sensitive(prop: YamlObject) -> bool:
    return prop.get("classification") in {"finance", "pii"}


def _sequence(raw: object) -> Sequence[object]:
    return raw if isinstance(raw, Sequence) and not isinstance(raw, str | bytes) else ()


def _mapping(raw: object, label: str) -> Mapping[str, object]:
    if isinstance(raw, Mapping):
        return raw
    raise ValidationFailed(f"{label} must be an object")
