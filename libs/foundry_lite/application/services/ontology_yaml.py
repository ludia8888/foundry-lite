from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports.ontology_repository import (
    ActionMutationDefinition,
    ActionParameterDefinition,
    ActionParameterSchema,
    ActionTypeDefinition,
    LinkTypeBacking,
    ObjectTypeBacking,
    OntologyJsonObject,
    PropertyDerivation,
)
from foundry_lite.domain.errors import ValidationFailed

type YamlObject = Mapping[str, object]


def yaml_object(value: object, field: str) -> dict[str, object]:
    """Return a string-keyed mapping from YAML input."""
    if not isinstance(value, Mapping):
        raise ValidationFailed(f"{field} must be a mapping")
    result: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str):
            raise ValidationFailed(f"{field} keys must be strings", details={"key": str(raw_key)})
        result[raw_key] = raw_value
    return result


def mapping_sequence(container: YamlObject, key: str) -> tuple[YamlObject, ...]:
    """Return a tuple of string-keyed mappings from a YAML list field."""
    value = container.get(key, ())
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValidationFailed(f"{key} must be a list")
    items: list[YamlObject] = []
    for index, item in enumerate(value):
        items.append(yaml_object(item, f"{key}[{index}]"))
    return tuple(items)


def required_mapping(container: YamlObject, key: str) -> YamlObject:
    """Return a required child YAML mapping."""
    if key not in container:
        raise ValidationFailed(f"{key} is required")
    return yaml_object(container[key], key)


def optional_mapping(container: YamlObject, key: str) -> OntologyJsonObject | None:
    """Return an optional child YAML mapping as a JSON object."""
    if key not in container or container[key] is None:
        return None
    return yaml_object(container[key], key)


def required_str(container: YamlObject, key: str) -> str:
    """Return a required string field from YAML input."""
    value = container.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationFailed(f"{key} must be a non-empty string")
    return value


def optional_str(container: YamlObject, key: str, default: str | None = None) -> str | None:
    """Return an optional string field from YAML input."""
    value = container.get(key, default)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationFailed(f"{key} must be a string")
    return value


def optional_bool(container: YamlObject, key: str, is_default: bool) -> bool:
    """Return an optional boolean field from YAML input."""
    value = container.get(key, is_default)
    if not isinstance(value, bool):
        raise ValidationFailed(f"{key} must be a boolean")
    return value


def object_type_backing(item: YamlObject) -> ObjectTypeBacking:
    """Build an object-type backing payload from YAML."""
    backing = required_mapping(item, "backing")
    payload: ObjectTypeBacking = {"dataset": required_str(backing, "dataset")}
    mode = optional_str(backing, "mode")
    if mode is not None:
        payload["mode"] = mode
    if "primaryKeyColumns" in backing:
        payload["primaryKeyColumns"] = string_sequence(backing["primaryKeyColumns"], "primaryKeyColumns")
    return payload


def link_type_backing(item: YamlObject) -> LinkTypeBacking:
    """Build a link-type backing payload from YAML."""
    backing = required_mapping(item, "backing")
    return {
        "dataset": required_str(backing, "dataset"),
        "fromKey": required_str(backing, "fromKey"),
        "toKey": required_str(backing, "toKey"),
    }


def property_derivation(prop: YamlObject) -> PropertyDerivation | None:
    """Build optional property derivation metadata from YAML."""
    if "derivation" not in prop or prop["derivation"] is None:
        return None
    derivation = required_mapping(prop, "derivation")
    result: PropertyDerivation = {}
    expression = optional_str(derivation, "expression")
    if expression is not None:
        result["expression"] = expression
    return result


def action_parameter_schema(parameters: Sequence[YamlObject]) -> ActionParameterSchema:
    """Build the JSON-schema subset used for action request validation."""
    return {
        "type": "object",
        "required": [
            required_str(parameter, "apiName") for parameter in parameters if parameter.get("required") is True
        ],
        "properties": {
            required_str(parameter, "apiName"): {"type": required_str(parameter, "type")} for parameter in parameters
        },
    }


def action_type_definition(item: YamlObject) -> ActionTypeDefinition:
    """Build a persisted action definition without losing known YAML fields."""
    definition: ActionTypeDefinition = {
        "apiName": required_str(item, "apiName"),
        "target": required_str(item, "target"),
    }
    _copy_optional_action_fields(definition, item)
    return definition


def schema_columns(schema: YamlObject, dataset_ref: str) -> dict[str, YamlObject]:
    """Return dataset schema columns keyed by column name."""
    value = schema.get("columns")
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValidationFailed("dataset schema columns must be a list", details={"dataset_ref": dataset_ref})
    columns: dict[str, YamlObject] = {}
    for index, column in enumerate(value):
        column_map = yaml_object(column, f"columns[{index}]")
        columns[required_str(column_map, "name")] = column_map
    return columns


def string_sequence(value: object, field: str) -> tuple[str, ...]:
    """Return a tuple of strings from a YAML sequence."""
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValidationFailed(f"{field} must be a list of strings")
    strings: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValidationFailed(f"{field} entries must be strings", details={"index": index})
        strings.append(item)
    return tuple(strings)


def _copy_optional_action_fields(definition: ActionTypeDefinition, item: YamlObject) -> None:
    display_name = optional_str(item, "displayName")
    if display_name is not None:
        definition["displayName"] = display_name
    parameters = tuple(action_parameter(parameter) for parameter in mapping_sequence(item, "parameters"))
    if parameters:
        definition["parameters"] = parameters
    permissions = optional_mapping(item, "permissions")
    if permissions is not None:
        definition["permissions"] = permissions
    _copy_sequence_fields(definition, item)


def _copy_sequence_fields(definition: ActionTypeDefinition, item: YamlObject) -> None:
    preconditions = tuple(yaml_object(row, "preconditions[]") for row in mapping_sequence(item, "preconditions"))
    if preconditions:
        definition["preconditions"] = preconditions
    mutations = tuple(action_mutation(mutation) for mutation in mapping_sequence(item, "mutations"))
    if mutations or "mutations" in item:
        definition["mutations"] = mutations
    writebacks = tuple(yaml_object(row, "writebacks[]") for row in mapping_sequence(item, "writebacks"))
    if writebacks:
        definition["writebacks"] = writebacks
    side_effects = tuple(yaml_object(row, "sideEffects[]") for row in mapping_sequence(item, "sideEffects"))
    if side_effects:
        definition["sideEffects"] = side_effects


def action_parameter(parameter: YamlObject) -> ActionParameterDefinition:
    """Build one action parameter declaration from YAML."""
    result: ActionParameterDefinition = {
        "apiName": required_str(parameter, "apiName"),
        "type": required_str(parameter, "type"),
    }
    if "required" in parameter:
        result["required"] = optional_bool(parameter, "required", False)
    return result


def action_mutation(mutation: YamlObject) -> ActionMutationDefinition:
    """Build one action mutation declaration from YAML."""
    result: ActionMutationDefinition = {
        "type": required_str(mutation, "type"),
        "property": required_str(mutation, "property"),
    }
    if "value" in mutation:
        result["value"] = mutation["value"]
    value_from = optional_str(mutation, "valueFrom")
    if value_from is not None:
        result["valueFrom"] = value_from
    return result
