"""Cross-resource validation for canonical Action definitions during activation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.services.action_ir_compiler import compile_action_definition
from foundry_lite.application.services.ontology_yaml import (
    YamlObject,
    action_type_definition,
    mapping_sequence,
    optional_bool,
    required_str,
)
from foundry_lite.domain.action_runtime.action_contract import ActionDefinitionV3, compile_action_contract
from foundry_lite.domain.errors import ValidationFailed


def validate_yaml_action_definitions(
    definition: YamlObject,
    object_defs: Mapping[str, YamlObject],
    link_defs: Mapping[str, YamlObject],
) -> None:
    """Validate references, editable properties, simple value types, and sensitive risk."""
    functions = {
        required_str(item, "apiName"): str(item.get("version") or "v1")
        for item in mapping_sequence(definition, "functionTypes")
    }
    interfaces = {required_str(item, "apiName") for item in mapping_sequence(definition, "interfaceTypes")}
    for action in mapping_sequence(definition, "actionTypes"):
        persisted = action_type_definition(action)
        contract = compile_action_contract(persisted)
        if contract.rules or contract.function is not None:
            compile_action_definition(persisted)
        _require_target(contract, object_defs, interfaces)
        _require_function(contract, functions)
        for rule in contract.rules:
            _validate_rule(contract, rule, object_defs, link_defs, functions)


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


def _require_function(contract: ActionDefinitionV3, functions: Mapping[str, str]) -> None:
    if contract.function is None:
        return
    actual_version = functions.get(contract.function.api_name)
    if actual_version == contract.function.version:
        return
    raise ValidationFailed(
        "action function reference or pinned version was not found",
        details={
            "actionType": contract.api_name,
            "function": contract.function.api_name,
            "expectedVersion": contract.function.version,
            "actualVersion": actual_version,
        },
    )


def _validate_rule(
    contract: ActionDefinitionV3,
    rule: Mapping[str, object],
    object_defs: Mapping[str, YamlObject],
    link_defs: Mapping[str, YamlObject],
    functions: Mapping[str, str],
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
        _require_link(contract, rule, link_defs)
        return
    object_type = rule.get("objectType")
    if not isinstance(object_type, str) or object_type not in object_defs:
        raise ValidationFailed(
            "action rule object type reference was not found",
            details={"actionType": contract.api_name, "objectType": object_type},
        )
    _validate_assignments(contract, kind, rule, object_defs[object_type])


def _require_link(
    contract: ActionDefinitionV3,
    rule: Mapping[str, object],
    link_defs: Mapping[str, YamlObject],
) -> None:
    link_type = rule.get("linkType")
    if isinstance(link_type, str) and link_type in link_defs:
        return
    raise ValidationFailed(
        "action rule link type reference was not found",
        details={"actionType": contract.api_name, "linkType": link_type},
    )


def _validate_assignments(
    contract: ActionDefinitionV3,
    kind: str,
    rule: Mapping[str, object],
    object_def: YamlObject,
) -> None:
    properties = {required_str(item, "apiName"): item for item in mapping_sequence(object_def, "properties")}
    parameters = {item.api_name: item.data_type for item in contract.parameters}
    for raw in _sequence(rule.get("assignments")):
        assignment = _mapping(raw, "action assignment")
        name = assignment.get("property")
        if not isinstance(name, str) or name not in properties:
            raise ValidationFailed("action assignment property was not found", details={"property": name})
        prop = properties[name]
        if kind != "createObject" and not optional_bool(prop, "editable", False):
            raise ValidationFailed("action assignment property must be editable", details={"property": name})
        _require_assignment_type(name, required_str(prop, "type"), assignment.get("value"), parameters)
        if _is_sensitive(prop) and contract.risk_level != "high":
            raise ValidationFailed(
                "sensitive property edits require high action risk",
                details={"actionType": contract.api_name, "property": name},
            )


def _require_assignment_type(
    property_name: str,
    property_type: str,
    raw_value: object,
    parameters: Mapping[str, str],
) -> None:
    if not isinstance(raw_value, Mapping):
        return
    kind = raw_value.get("kind")
    if kind == "parameter":
        parameter = raw_value.get("parameter")
        actual = parameters.get(str(parameter))
        if actual is not None and not _compatible_types(property_type, actual):
            raise ValidationFailed(
                "action assignment parameter type does not match property",
                details={"property": property_name, "propertyType": property_type, "parameterType": actual},
            )
    if kind == "literal" and not _literal_matches(property_type, raw_value.get("value")):
        raise ValidationFailed(
            "action assignment literal type does not match property",
            details={"property": property_name, "propertyType": property_type},
        )


def _compatible_types(property_type: str, parameter_type: str) -> bool:
    aliases = {
        "integer": {"integer", "long"},
        "float": {"integer", "long", "float", "decimal"},
        "string": {"string", "date", "timestamp", "object", "interface", "media", "attachment"},
        "media_reference": {"media"},
        "boolean": {"boolean"},
    }
    return parameter_type in aliases.get(property_type, {property_type})


def _literal_matches(property_type: str, value: object) -> bool:
    if value is None:
        return True
    if property_type == "boolean":
        return isinstance(value, bool)
    if property_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if property_type == "float":
        return isinstance(value, int | float) and not isinstance(value, bool)
    return isinstance(value, str)


def _is_sensitive(prop: YamlObject) -> bool:
    return prop.get("classification") in {"finance", "pii"}


def _sequence(raw: object) -> Sequence[object]:
    return raw if isinstance(raw, Sequence) and not isinstance(raw, str | bytes) else ()


def _mapping(raw: object, label: str) -> Mapping[str, object]:
    if isinstance(raw, Mapping):
        return raw
    raise ValidationFailed(f"{label} must be an object")
