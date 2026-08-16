"""Application service helpers for ontology yaml workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

import yaml

from foundry_lite.application.ports.ontology_definitions import ObjectTypeDatasourceBacking
from foundry_lite.application.ports.ontology_repository import (
    ActionMutationDefinition,
    ActionParameterDefinition,
    ActionParameterSchema,
    ActionTypeDefinition,
    LinkTypeBacking,
    ObjectTypeBacking,
    ObjectTypeCdcBacking,
    OntologyJsonObject,
    PropertyDerivation,
)
from foundry_lite.application.services.materialization_types import OBJECT_SNAPSHOT_MODE
from foundry_lite.application.services.runtime_error_payloads import scrub_error_text
from foundry_lite.domain.errors import ValidationFailed

type YamlObject = Mapping[str, object]

# Ontology YAML arrives over HTTP as request-body text; bounding it keeps a
# single oversized payload from stalling parse/validate work for everyone else.
MAX_ONTOLOGY_YAML_BYTES = 1_048_576


def require_yaml_text_within_limit(yaml_text: str) -> None:
    """Reject ontology YAML text larger than the sanity bound."""
    byte_size = len(yaml_text.encode("utf-8"))
    if byte_size > MAX_ONTOLOGY_YAML_BYTES:
        raise ValidationFailed(
            "ontology yaml text exceeds size limit",
            details={"byteSize": byte_size, "maxBytes": MAX_ONTOLOGY_YAML_BYTES},
        )


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
    if backing.get("datasources") is not None:
        return _multi_datasource_backing(backing)
    payload: ObjectTypeBacking = {"dataset": required_str(backing, "dataset")}
    mode = optional_str(backing, "mode")
    if mode is not None:
        payload["mode"] = mode
    if "primaryKeyColumns" in backing:
        payload["primaryKeyColumns"] = string_sequence(backing["primaryKeyColumns"], "primaryKeyColumns")
    cdc = optional_mapping(backing, "cdc")
    if cdc is not None:
        payload["cdc"] = object_type_cdc_backing(cdc)
    return payload


def _multi_datasource_backing(backing: YamlObject) -> ObjectTypeBacking:
    """Build a multi-datasource backing payload from YAML.

    ``cdc`` is single-datasource only (there is no per-segment changelog
    contract), and mixing top-level ``dataset``/``primaryKeyColumns`` with
    ``datasources`` would leave two competing declarations, so both fail here
    instead of surfacing as ambiguous behavior later.
    """
    if "cdc" in backing and backing["cdc"] is not None:
        raise ValidationFailed("cdc backing requires a single-datasource object type")
    for forbidden in ("dataset", "primaryKeyColumns"):
        if forbidden in backing:
            raise ValidationFailed(
                "backing cannot mix datasources with a top-level dataset declaration",
                details={"field": forbidden},
            )
    datasources = tuple(
        _backing_datasource(entry, index) for index, entry in enumerate(mapping_sequence(backing, "datasources"))
    )
    if not datasources:
        raise ValidationFailed("backing datasources must declare at least one datasource")
    payload: ObjectTypeBacking = {"datasources": datasources}
    mode = optional_str(backing, "mode")
    if mode is not None:
        payload["mode"] = mode
    return payload


def _backing_datasource(entry: YamlObject, index: int) -> ObjectTypeDatasourceBacking:
    payload: ObjectTypeDatasourceBacking = {
        "name": required_str(entry, "name"),
        "dataset": required_str(entry, "dataset"),
    }
    if "primaryKeyColumns" in entry:
        columns = string_sequence(entry["primaryKeyColumns"], f"datasources[{index}].primaryKeyColumns")
        if len(columns) != 1:
            raise ValidationFailed(
                "datasource primaryKeyColumns must declare exactly one column",
                details={"datasource": payload["name"]},
            )
        payload["primaryKeyColumns"] = columns
    required_role = optional_str(entry, "requiredRole")
    if required_role is not None:
        if not required_role:
            raise ValidationFailed("datasource requiredRole must be a non-empty string")
        payload["requiredRole"] = required_role
    return payload


def object_type_cdc_backing(cdc: YamlObject) -> ObjectTypeCdcBacking:
    payload: ObjectTypeCdcBacking = {"dataset": required_str(cdc, "dataset")}
    if "primaryKeyColumns" in cdc:
        payload["primaryKeyColumns"] = string_sequence(cdc["primaryKeyColumns"], "cdc.primaryKeyColumns")
    delete_policy = optional_str(cdc, "deletePolicy")
    if delete_policy is not None:
        payload["deletePolicy"] = delete_policy
    return payload


def object_type_materialization_config(item: YamlObject) -> OntologyJsonObject | None:
    """Build the optional per-object-type materialization declaration from YAML.

    The mode is normalized to an explicit value here so persisted config never
    depends on YAML defaults; run-time spec resolution then reads a stable
    shape regardless of which ontology version wrote it.
    """
    declared = optional_mapping(item, "materialization")
    if declared is None:
        return None
    return {
        "dataset": required_str(declared, "dataset"),
        "mode": optional_str(declared, "mode", OBJECT_SNAPSHOT_MODE) or OBJECT_SNAPSHOT_MODE,
    }


def object_type_row_policies(item: YamlObject) -> tuple[OntologyJsonObject, ...]:
    """Build the optional per-object-type row policy declarations from YAML.

    Each policy grants one role visibility over rows matching a query-filter
    AST. The normalized ``{role, filter}`` shape is persisted in the object
    type's config JSON (like titleProperty/materialization) so row security
    needs no schema migration and survives snapshot/rollback replay.
    """
    policies: list[OntologyJsonObject] = []
    for policy in mapping_sequence(item, "rowPolicies"):
        policies.append(
            {
                "role": required_str(policy, "role"),
                "filter": dict(required_mapping(policy, "filter")),
            }
        )
    return tuple(policies)


def object_type_media_properties(item: YamlObject) -> OntologyJsonObject:
    """Persist media property contracts in object config so rollback cannot drop them."""
    result: dict[str, object] = {}
    for prop in mapping_sequence(item, "properties"):
        property_type = required_str(prop, "type")
        if property_type not in {"media_reference", "attachment"}:
            continue
        result[required_str(prop, "apiName")] = {
            "mediaSet": required_str(prop, "mediaSet"),
            "referenceKind": "attachment" if property_type == "attachment" else "media",
            "allowMultiple": optional_bool(prop, "allowMultiple", False),
        }
    return result


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
    """Build the compatibility parameter schema stored beside the definition."""
    from foundry_lite.domain.action_runtime.action_contract import action_parameter_json_schema, compile_action_contract

    definition = {
        "apiName": "__schema__",
        "target": "__target__",
        "parameters": [dict(parameter) for parameter in parameters],
    }
    return cast(ActionParameterSchema, action_parameter_json_schema(compile_action_contract(definition)))


def action_type_parameter_schema(item: YamlObject) -> ActionParameterSchema:
    """Generate the consumer schema from the complete canonical Action contract."""
    from foundry_lite.domain.action_runtime.action_contract import action_parameter_json_schema, compile_action_contract

    definition = action_type_definition(item)
    return cast(ActionParameterSchema, action_parameter_json_schema(compile_action_contract(definition)))


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
    description = optional_str(item, "description")
    if description is not None:
        definition["description"] = description
    _copy_action_contract_fields(definition, item)
    parameters = tuple(action_parameter(parameter) for parameter in mapping_sequence(item, "parameters"))
    if parameters:
        definition["parameters"] = parameters
    permissions = action_permissions(item)
    if permissions is not None:
        definition["permissions"] = permissions
    _copy_sequence_fields(definition, item)


def action_permissions(item: YamlObject) -> OntologyJsonObject | None:
    """Validate the optional action permissions block before it is persisted.

    ``allowedRoles`` drives runtime RBAC (see ``PolicyService``), so a malformed
    declaration must fail activation instead of surfacing later as an
    unenforceable permission.
    """
    permissions = optional_mapping(item, "permissions")
    if permissions is None:
        return None
    for field in ("allowedRoles", "viewRoles", "editRoles", "applyRoles"):
        if permissions.get(field) is not None:
            string_sequence(permissions[field], f"permissions.{field}")
    return permissions


def action_allowed_roles(definition: Mapping[str, object]) -> tuple[str, ...] | None:
    """Return ``permissions.allowedRoles`` from a persisted action definition.

    ``None`` means the definition declares no role restriction, so the policy
    falls back to its static permission map; a malformed persisted value raises
    the same typed error as YAML ingestion (fail closed, never silently open).
    """
    permissions = definition.get("permissions")
    if not isinstance(permissions, Mapping):
        return None
    value = permissions.get("applyRoles", permissions.get("allowedRoles"))
    if value is None:
        return None
    return string_sequence(value, "permissions.applyRoles")


def _copy_sequence_fields(definition: ActionTypeDefinition, item: YamlObject) -> None:
    _copy_legacy_action_sequences(definition, item)
    _copy_action_rule_sequences(definition, item)
    _copy_effect_fields(definition, item)


def _copy_legacy_action_sequences(definition: ActionTypeDefinition, item: YamlObject) -> None:
    preconditions = tuple(yaml_object(row, "preconditions[]") for row in mapping_sequence(item, "preconditions"))
    if preconditions:
        definition["preconditions"] = preconditions
    mutations = tuple(action_mutation(mutation) for mutation in mapping_sequence(item, "mutations"))
    if mutations or "mutations" in item:
        definition["mutations"] = mutations


def _copy_action_rule_sequences(definition: ActionTypeDefinition, item: YamlObject) -> None:
    rules_v2 = tuple(yaml_object(row, "rulesV2[]") for row in mapping_sequence(item, "rulesV2"))
    if rules_v2 or "rulesV2" in item:
        definition["rulesV2"] = rules_v2
    rules = tuple(yaml_object(row, "rules[]") for row in mapping_sequence(item, "rules"))
    if rules or "rules" in item:
        definition["rules"] = rules


def _copy_effect_fields(definition: ActionTypeDefinition, item: YamlObject) -> None:
    effects = tuple(yaml_object(row, "effects[]") for row in mapping_sequence(item, "effects"))
    if effects or "effects" in item:
        definition["effects"] = effects
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
    description = optional_str(parameter, "description")
    if description is not None:
        result["description"] = description
    _copy_action_parameter_config(result, parameter)
    return result


def _copy_action_contract_fields(definition: ActionTypeDefinition, item: YamlObject) -> None:
    version = item.get("contractVersion")
    if version is not None:
        if not isinstance(version, int):
            raise ValidationFailed("contractVersion must be an integer")
        definition["contractVersion"] = version
    for key in ("targetKind", "riskLevel", "agentExecutionPolicy", "agentToolDescription"):
        text_value = optional_str(item, key)
        if text_value is not None:
            definition[key] = text_value
    for key in ("submissionCriteria", "function", "actionLog", "revert", "branchPolicy", "formLayout"):
        mapping_value = optional_mapping(item, key)
        if mapping_value is not None:
            definition[key] = mapping_value  # type: ignore[literal-required]


def _copy_action_parameter_config(result: ActionParameterDefinition, parameter: YamlObject) -> None:
    _copy_action_parameter_mappings(result, parameter)
    _copy_action_parameter_overrides(result, parameter)
    _copy_action_parameter_text(result, parameter)
    _copy_action_parameter_media_limits(result, parameter)
    _copy_action_parameter_fields(result, parameter)


def _copy_action_parameter_mappings(result: ActionParameterDefinition, parameter: YamlObject) -> None:
    for key in ("default", "constraints"):
        mapping_value = optional_mapping(parameter, key)
        if mapping_value is not None:
            result[key] = mapping_value


def _copy_action_parameter_overrides(result: ActionParameterDefinition, parameter: YamlObject) -> None:
    overrides = tuple(yaml_object(row, "overrides[]") for row in mapping_sequence(parameter, "overrides"))
    if overrides:
        result["overrides"] = overrides


def _copy_action_parameter_text(result: ActionParameterDefinition, parameter: YamlObject) -> None:
    for key in ("objectType", "interfaceType", "itemType", "mediaSet", "render"):
        text_value = optional_str(parameter, key)
        if text_value is not None:
            result[key] = text_value


def _copy_action_parameter_media_limits(result: ActionParameterDefinition, parameter: YamlObject) -> None:
    if "allowedMimeTypes" in parameter:
        result["allowedMimeTypes"] = string_sequence(parameter["allowedMimeTypes"], "allowedMimeTypes")
    if "maxBytes" in parameter:
        maximum = parameter["maxBytes"]
        if not isinstance(maximum, int) or isinstance(maximum, bool):
            raise ValidationFailed("maxBytes must be an integer")
        result["maxBytes"] = maximum


def _copy_action_parameter_fields(result: ActionParameterDefinition, parameter: YamlObject) -> None:
    fields = tuple(yaml_object(row, "fields[]") for row in mapping_sequence(parameter, "fields"))
    if fields:
        result["fields"] = fields


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


def yaml_parse_error_details(exc: yaml.YAMLError) -> dict[str, object]:
    """Extract the parse failure location so validate callers can point at the broken line."""
    details: dict[str, object] = {}
    problem = getattr(exc, "problem", None)
    if isinstance(problem, str) and problem:
        details["problem"] = scrub_error_text(problem)
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    if mark is not None:
        details["line"] = int(mark.line) + 1
        details["column"] = int(mark.column) + 1
    context = getattr(exc, "context", None)
    if isinstance(context, str) and context:
        details["context"] = scrub_error_text(context)
    if not details:
        details["problem"] = scrub_error_text(str(exc))
    return details
