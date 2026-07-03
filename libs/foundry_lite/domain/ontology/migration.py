"""Pure ontology migration compatibility planning rules."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.ontology.function_types import normalized_function_definition
from foundry_lite.domain.ontology.migration_changes import (
    blocked_action_removed,
    blocked_action_target_changed,
    blocked_function_removed,
    blocked_implements_removed,
    blocked_interface_property_removed,
    blocked_interface_removed,
    blocked_link_backing_changed,
    blocked_link_cardinality_changed,
    blocked_link_endpoint_changed,
    blocked_link_removed,
    blocked_object_removed,
    blocked_parameter_became_required,
    blocked_parameter_removed,
    blocked_parameter_type_changed,
    blocked_primary_key_changed,
    blocked_property_removed,
    blocked_property_rename,
    blocked_property_type_change,
    blocked_required_parameter_added,
    warning_function_added,
    warning_function_definition_changed,
    warning_implements_added,
    warning_interface_added,
    warning_object_reindex,
    warning_optional_parameter_added,
    warning_parameter_became_optional,
    warning_property_deprecated,
    warning_property_reindex,
)
from foundry_lite.domain.ontology.migration_datasources import datasource_migration_changes
from foundry_lite.domain.ontology.migration_types import (
    ActionParameterMap,
    OntologyMigrationChange,
    OntologyMigrationPlan,
    OntologyReindexOperation,
    reindex_operation,
)

OntologyRow = Mapping[str, object]
OntologyDefinition = Mapping[str, object]


def build_ontology_migration_plan(
    *,
    source_ontology_version_id: str | None,
    current_objects: Mapping[str, OntologyRow],
    current_properties: Mapping[str, Sequence[OntologyRow]],
    current_links: Sequence[OntologyRow],
    current_actions: Sequence[OntologyRow],
    definition: OntologyDefinition,
    current_interfaces: Sequence[OntologyRow] = (),
    current_functions: Sequence[OntologyRow] = (),
) -> OntologyMigrationPlan:
    """Classify a candidate ontology definition without touching persistence."""
    candidate_objects = _object_definitions_by_api(definition)
    candidate_links = _link_definitions_by_api(definition)
    candidate_actions = _action_definitions_by_api(definition)
    object_changes, reindex_operations = _object_migration_changes(
        current_objects,
        current_properties,
        candidate_objects,
    )
    changes = [
        *object_changes,
        *_interface_migration_changes(_interface_rows_by_api(current_interfaces), definition),
        *_implements_migration_changes(current_objects, candidate_objects),
        *_function_migration_changes(_function_rows_by_api(current_functions), definition),
        *_link_migration_changes(_link_rows_by_api(current_links), candidate_links),
        *_action_migration_changes(_action_rows_by_api(current_actions), candidate_actions),
    ]
    return OntologyMigrationPlan(source_ontology_version_id, tuple(changes), tuple(reindex_operations))


def _object_migration_changes(
    current_objects: Mapping[str, OntologyRow],
    current_properties: Mapping[str, Sequence[OntologyRow]],
    candidate_objects: Mapping[str, OntologyDefinition],
) -> tuple[list[OntologyMigrationChange], list[OntologyReindexOperation]]:
    changes: list[OntologyMigrationChange] = []
    reindex_operations: list[OntologyReindexOperation] = []
    for api_name, current in sorted(current_objects.items()):
        candidate = candidate_objects.get(api_name)
        if candidate is None:
            changes.append(blocked_object_removed(api_name))
            continue
        object_changes, operation = _single_object_migration_changes(
            api_name,
            current,
            current_properties.get(api_name, ()),
            candidate,
        )
        changes.extend(object_changes)
        if operation is not None:
            reindex_operations.append(operation)
    return changes, reindex_operations


def _single_object_migration_changes(
    api_name: str,
    current: OntologyRow,
    current_properties: Sequence[OntologyRow],
    candidate: OntologyDefinition,
) -> tuple[list[OntologyMigrationChange], OntologyReindexOperation | None]:
    changes = _object_shape_changes(api_name, current, candidate)
    changes.extend(
        datasource_migration_changes(
            api_name,
            current,
            current_properties,
            candidate,
            _property_definitions_by_api(candidate),
        )
    )
    property_changes, reindex_fields = _property_migration_changes(api_name, current_properties, candidate)
    changes.extend(property_changes)
    if _mapping_changed(_mapping(current["backing"]), object_type_backing(candidate)):
        reindex_fields.append("backing")
    operation = reindex_operation(api_name, reindex_fields)
    return changes, operation


def _object_shape_changes(
    api_name: str,
    current: OntologyRow,
    candidate: OntologyDefinition,
) -> list[OntologyMigrationChange]:
    changes: list[OntologyMigrationChange] = []
    if required_str(candidate, "primaryKey") != current["primary_key_property"]:
        changes.append(blocked_primary_key_changed(api_name, str(current["primary_key_property"]), candidate))
    if _mapping_changed(_mapping(current["backing"]), object_type_backing(candidate)):
        changes.append(warning_object_reindex(api_name, "backing"))
    return changes


def _property_migration_changes(
    object_api_name: str,
    current_properties: Sequence[OntologyRow],
    candidate: OntologyDefinition,
) -> tuple[list[OntologyMigrationChange], list[str]]:
    candidate_properties = _property_definitions_by_api(candidate)
    rename_sources = _property_rename_sources(candidate_properties)
    changes = _removed_property_changes(object_api_name, current_properties, candidate_properties, rename_sources)
    reindex_fields: list[str] = []
    for api_name, candidate_property in sorted(candidate_properties.items()):
        current_property = _property_by_api(current_properties).get(api_name)
        changes.extend(_candidate_property_changes(object_api_name, api_name, current_property, candidate_property))
        if _property_requires_reindex(current_property, candidate_property):
            reindex_fields.append(f"properties.{api_name}")
    return changes, reindex_fields


def _candidate_property_changes(
    object_api_name: str,
    api_name: str,
    current_property: OntologyRow | None,
    candidate_property: OntologyDefinition,
) -> list[OntologyMigrationChange]:
    changes: list[OntologyMigrationChange] = []
    rename_source = _rename_source(candidate_property)
    if rename_source is not None:
        changes.append(blocked_property_rename(object_api_name, rename_source, api_name))
    if current_property is None:
        return changes
    if required_str(candidate_property, "type") != current_property["data_type"]:
        changes.append(blocked_property_type_change(object_api_name, api_name, current_property, candidate_property))
    if optional_bool(candidate_property, "deprecated", False):
        changes.append(warning_property_deprecated(object_api_name, api_name))
    if _property_requires_reindex(current_property, candidate_property):
        changes.append(warning_property_reindex(object_api_name, api_name, current_property, candidate_property))
    return changes


def _removed_property_changes(
    object_api_name: str,
    current_properties: Sequence[OntologyRow],
    candidate_properties: Mapping[str, OntologyDefinition],
    rename_sources: Mapping[str, str],
) -> list[OntologyMigrationChange]:
    return [
        blocked_property_removed(object_api_name, str(current["api_name"]))
        for current in current_properties
        if current["api_name"] not in candidate_properties and current["api_name"] not in rename_sources
    ]


def _interface_migration_changes(
    current_interfaces: Mapping[str, OntologyRow],
    definition: OntologyDefinition,
) -> list[OntologyMigrationChange]:
    """Classify interface additions (warning) and removals (blocked).

    Removing a whole interface or one of its shared property contracts breaks
    OSDK apps typed against it, so both block; additions only widen the
    surface and warn.
    """
    candidate_interfaces = _interface_definitions_by_api(definition)
    changes: list[OntologyMigrationChange] = []
    for api_name, current in sorted(current_interfaces.items()):
        candidate = candidate_interfaces.get(api_name)
        if candidate is None:
            changes.append(blocked_interface_removed(api_name))
            continue
        candidate_properties = {required_str(prop, "apiName") for prop in mapping_sequence(candidate, "properties")}
        for property_api_name in _interface_row_property_names(current):
            if property_api_name not in candidate_properties:
                changes.append(blocked_interface_property_removed(api_name, property_api_name))
    for api_name in sorted(candidate_interfaces):
        if api_name not in current_interfaces:
            changes.append(warning_interface_added(api_name))
    return changes


def _interface_row_property_names(row: OntologyRow) -> tuple[str, ...]:
    properties = mapping_sequence(_mapping(row["definition"]), "properties")
    return tuple(required_str(prop, "apiName") for prop in properties)


def _implements_migration_changes(
    current_objects: Mapping[str, OntologyRow],
    candidate_objects: Mapping[str, OntologyDefinition],
) -> list[OntologyMigrationChange]:
    """Classify per-object implements changes; drops block, additions warn."""
    changes: list[OntologyMigrationChange] = []
    for api_name, current in sorted(current_objects.items()):
        candidate = candidate_objects.get(api_name)
        if candidate is None:
            continue  # object removal itself is already a blocking change
        current_implements = set(_string_items(_mapping(current["config"]).get("implements")))
        candidate_implements = set(_string_items(candidate.get("implements")))
        for interface_api_name in sorted(current_implements - candidate_implements):
            changes.append(blocked_implements_removed(api_name, interface_api_name))
        for interface_api_name in sorted(candidate_implements - current_implements):
            changes.append(warning_implements_added(api_name, interface_api_name))
    return changes


def _string_items(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValidationFailed("implements must be a list of strings")
    return tuple(str(item) for item in cast(Sequence[object], value))


def _function_migration_changes(
    current_functions: Mapping[str, OntologyRow],
    definition: OntologyDefinition,
) -> list[OntologyMigrationChange]:
    """Classify function additions (warning), removals (blocked), and edits (warning).

    Removing a function breaks callers executing it by apiName, so it blocks;
    a new function or a changed definition only alters behavior going forward
    and stays visible as a warning.
    """
    raw_candidates = _yaml_rows_by_api(
        mapping_sequence(definition, "functionTypes"), "duplicate function apiName", "functionType"
    )
    candidates = {api_name: normalized_function_definition(item) for api_name, item in raw_candidates.items()}
    changes: list[OntologyMigrationChange] = []
    for api_name, current in sorted(current_functions.items()):
        candidate = candidates.get(api_name)
        if candidate is None:
            changes.append(blocked_function_removed(api_name))
        elif _mapping_changed(_mapping(current["definition"]), candidate):
            changes.append(warning_function_definition_changed(api_name))
    for api_name in sorted(candidates):
        if api_name not in current_functions:
            changes.append(warning_function_added(api_name))
    return changes


def _link_migration_changes(
    current_links: Mapping[str, OntologyRow],
    candidate_links: Mapping[str, OntologyDefinition],
) -> list[OntologyMigrationChange]:
    changes: list[OntologyMigrationChange] = []
    for api_name, current in sorted(current_links.items()):
        candidate = candidate_links.get(api_name)
        if candidate is None:
            changes.append(blocked_link_removed(api_name))
            continue
        changes.extend(_single_link_migration_changes(api_name, current, candidate))
    return changes


def _single_link_migration_changes(
    api_name: str,
    current: OntologyRow,
    candidate: OntologyDefinition,
) -> list[OntologyMigrationChange]:
    changes: list[OntologyMigrationChange] = []
    if (
        required_str(candidate, "from") != current["from_api_name"]
        or required_str(candidate, "to") != current["to_api_name"]
    ):
        changes.append(blocked_link_endpoint_changed(api_name, current, candidate))
    if optional_str(candidate, "cardinality", "many_to_one") != current["cardinality"]:
        changes.append(blocked_link_cardinality_changed(api_name))
    if _mapping_changed(_mapping(current["backing"]), link_type_backing(candidate)):
        changes.append(
            blocked_link_backing_changed(
                api_name,
                _mapping(current["backing"]),
                link_type_backing(candidate),
            )
        )
    return changes


def _action_migration_changes(
    current_actions: Mapping[str, OntologyRow],
    candidate_actions: Mapping[str, OntologyDefinition],
) -> list[OntologyMigrationChange]:
    changes: list[OntologyMigrationChange] = []
    for api_name, current in sorted(current_actions.items()):
        candidate = candidate_actions.get(api_name)
        if candidate is None:
            changes.append(blocked_action_removed(api_name))
            continue
        changes.extend(_single_action_migration_changes(api_name, current, candidate))
    return changes


def _single_action_migration_changes(
    api_name: str,
    current: OntologyRow,
    candidate: OntologyDefinition,
) -> list[OntologyMigrationChange]:
    changes: list[OntologyMigrationChange] = []
    if required_str(candidate, "target") != current["target_api_name"]:
        changes.append(
            blocked_action_target_changed(api_name, str(current["target_api_name"]), required_str(candidate, "target"))
        )
    changes.extend(_parameter_migration_changes(api_name, current, candidate))
    return changes


def _parameter_migration_changes(
    action_api_name: str,
    current: OntologyRow,
    candidate: OntologyDefinition,
) -> list[OntologyMigrationChange]:
    current_params = _parameter_definitions_by_api(_current_action_parameters(current))
    candidate_params = _parameter_definitions_by_api(mapping_sequence(candidate, "parameters"))
    changes = _removed_or_changed_parameter_changes(action_api_name, current_params, candidate_params)
    changes.extend(_added_parameter_changes(action_api_name, current_params, candidate_params))
    return changes


def _removed_or_changed_parameter_changes(
    action_api_name: str,
    current_params: Mapping[str, ActionParameterMap],
    candidate_params: Mapping[str, ActionParameterMap],
) -> list[OntologyMigrationChange]:
    changes: list[OntologyMigrationChange] = []
    for api_name, current in sorted(current_params.items()):
        candidate = candidate_params.get(api_name)
        if candidate is None:
            changes.append(blocked_parameter_removed(action_api_name, api_name))
            continue
        changes.extend(_changed_parameter_changes(action_api_name, api_name, current, candidate))
    return changes


def _changed_parameter_changes(
    action_api_name: str,
    api_name: str,
    current: ActionParameterMap,
    candidate: ActionParameterMap,
) -> list[OntologyMigrationChange]:
    changes: list[OntologyMigrationChange] = []
    if required_str(candidate, "type") != required_str(current, "type"):
        changes.append(blocked_parameter_type_changed(action_api_name, api_name, current, candidate))
    if not _parameter_required(current) and _parameter_required(candidate):
        changes.append(blocked_parameter_became_required(action_api_name, api_name))
    if _parameter_required(current) and not _parameter_required(candidate):
        changes.append(warning_parameter_became_optional(action_api_name, api_name))
    return changes


def _added_parameter_changes(
    action_api_name: str,
    current_params: Mapping[str, ActionParameterMap],
    candidate_params: Mapping[str, ActionParameterMap],
) -> list[OntologyMigrationChange]:
    changes: list[OntologyMigrationChange] = []
    for api_name, candidate in sorted(candidate_params.items()):
        if api_name in current_params:
            continue
        if _parameter_required(candidate):
            changes.append(blocked_required_parameter_added(action_api_name, api_name))
        else:
            changes.append(warning_optional_parameter_added(action_api_name, api_name))
    return changes


def _interface_rows_by_api(rows: Sequence[OntologyRow]) -> dict[str, OntologyRow]:
    return _rows_by_api(rows, "duplicate persisted interface apiName")


def _function_rows_by_api(rows: Sequence[OntologyRow]) -> dict[str, OntologyRow]:
    return _rows_by_api(rows, "duplicate persisted function apiName")


def _link_rows_by_api(rows: Sequence[OntologyRow]) -> dict[str, OntologyRow]:
    return _rows_by_api(rows, "duplicate persisted ontology apiName")


def _action_rows_by_api(rows: Sequence[OntologyRow]) -> dict[str, OntologyRow]:
    return _rows_by_api(rows, "duplicate persisted action apiName")


def _rows_by_api(rows: Sequence[OntologyRow], message: str) -> dict[str, OntologyRow]:
    result: dict[str, OntologyRow] = {}
    for row in rows:
        api_name = str(row["api_name"])
        if api_name in result:
            raise ValidationFailed(message, details={"apiName": api_name})
        result[api_name] = row
    return result


def _object_definitions_by_api(definition: OntologyDefinition) -> dict[str, OntologyDefinition]:
    return _yaml_rows_by_api(mapping_sequence(definition, "objectTypes"), "duplicate object apiName", "objectType")


def _property_definitions_by_api(object_definition: OntologyDefinition) -> dict[str, OntologyDefinition]:
    return _yaml_rows_by_api(
        mapping_sequence(object_definition, "properties"),
        "duplicate property apiName",
        "property",
    )


def _interface_definitions_by_api(definition: OntologyDefinition) -> dict[str, OntologyDefinition]:
    return _yaml_rows_by_api(mapping_sequence(definition, "interfaces"), "duplicate interface apiName", "interface")


def _link_definitions_by_api(definition: OntologyDefinition) -> dict[str, OntologyDefinition]:
    return _yaml_rows_by_api(mapping_sequence(definition, "linkTypes"), "duplicate link apiName", "linkType")


def _action_definitions_by_api(definition: OntologyDefinition) -> dict[str, OntologyDefinition]:
    return _yaml_rows_by_api(mapping_sequence(definition, "actionTypes"), "duplicate action apiName", "actionType")


def _yaml_rows_by_api(
    rows: Sequence[OntologyDefinition],
    message: str,
    detail_key: str,
) -> dict[str, OntologyDefinition]:
    result: dict[str, OntologyDefinition] = {}
    for row in rows:
        api_name = required_str(row, "apiName")
        if api_name in result:
            raise ValidationFailed(message, details={detail_key: api_name})
        result[api_name] = row
    return result


def _property_by_api(rows: Sequence[OntologyRow]) -> dict[str, OntologyRow]:
    return {str(row["api_name"]): row for row in rows}


def _property_rename_sources(properties: Mapping[str, OntologyDefinition]) -> dict[str, str]:
    result: dict[str, str] = {}
    for api_name, prop in properties.items():
        rename_source = _rename_source(prop)
        if rename_source is not None:
            result[rename_source] = api_name
    return result


def _rename_source(prop: OntologyDefinition) -> str | None:
    for key in ("renamedFrom", "previousName", "previousApiName"):
        value = optional_str(prop, key)
        if value is not None:
            return value
    return None


def _property_requires_reindex(
    current_property: OntologyRow | None,
    candidate_property: OntologyDefinition,
) -> bool:
    if current_property is None:
        return property_source(candidate_property) == "dataset"
    return (
        property_source(candidate_property) != current_property["source"]
        or optional_str(candidate_property, "column") != current_property["column_name"]
    )


def _mapping_changed(previous: Mapping[str, object], next_mapping: Mapping[str, object]) -> bool:
    return _json_hash({"value": previous}) != _json_hash({"value": next_mapping})


def _current_action_parameters(action: OntologyRow) -> tuple[ActionParameterMap, ...]:
    definition = _mapping(action["definition"])
    value = definition.get("parameters", ())
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValidationFailed("persisted action parameters must be a list", details={"actionType": action["api_name"]})
    raw_parameters = cast(Sequence[object], value)
    parameters: list[ActionParameterMap] = []
    for parameter in raw_parameters:
        parameters.append(_mapping(parameter))
    return tuple(parameters)


def _parameter_definitions_by_api(parameters: Sequence[ActionParameterMap]) -> dict[str, ActionParameterMap]:
    result: dict[str, ActionParameterMap] = {}
    for parameter in parameters:
        api_name = required_str(parameter, "apiName")
        if api_name in result:
            raise ValidationFailed("duplicate action parameter apiName", details={"parameter": api_name})
        result[api_name] = parameter
    return result


def _parameter_required(parameter: ActionParameterMap) -> bool:
    return bool(parameter.get("required") is True)


def object_type_backing(item: Mapping[str, object]) -> dict[str, object]:
    value = item.get("backing")
    if isinstance(value, Mapping):
        return dict(cast(Mapping[str, object], value))
    return {"dataset": required_str(item, "dataset")}


def link_type_backing(item: Mapping[str, object]) -> dict[str, object]:
    value = item.get("backing")
    if isinstance(value, Mapping):
        return dict(cast(Mapping[str, object], value))
    return {
        "dataset": required_str(item, "dataset"),
        "fromKey": required_str(item, "fromKey"),
        "toKey": required_str(item, "toKey"),
    }


def mapping_sequence(mapping: Mapping[str, object], key: str) -> tuple[OntologyDefinition, ...]:
    value = mapping.get(key, ())
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValidationFailed(f"{key} must be a list")
    raw_items = cast(Sequence[object], value)
    items: list[OntologyDefinition] = []
    for item in raw_items:
        items.append(_mapping(item))
    return tuple(items)


def property_source(prop: Mapping[str, object]) -> str:
    source = optional_str(prop, "source", "dataset" if "column" in prop else "edit_layer")
    if source is None:
        raise ValidationFailed("property source must be set", details={"property": required_str(prop, "apiName")})
    return source


def optional_bool(mapping: Mapping[str, object], key: str, is_default_value: bool) -> bool:
    value = mapping.get(key, is_default_value)
    if not isinstance(value, bool):
        raise ValidationFailed(f"{key} must be a boolean")
    return value


def optional_str(mapping: Mapping[str, object], key: str, default: str | None = None) -> str | None:
    value = mapping.get(key, default)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationFailed(f"{key} must be a string")
    return value


def required_str(mapping: Mapping[str, object], key: str) -> str:
    value = optional_str(mapping, key)
    if value is None or value == "":
        raise ValidationFailed(f"{key} must be set")
    return value


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValidationFailed("expected mapping")
    return cast(Mapping[str, object], value)


def _json_hash(payload: Mapping[str, object]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
