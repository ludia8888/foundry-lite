"""Ontology migration planning before activating a new ontology version."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.ontology_repository import (
    ActionTypeRow,
    LinkTypeRow,
    ObjectTypeRow,
    OntologyRepository,
    PropertyTypeRow,
)
from foundry_lite.application.primitives import _json_hash
from foundry_lite.application.services.ontology_migration_changes import (
    blocked_action_removed,
    blocked_action_target_changed,
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
    warning_object_reindex,
    warning_optional_parameter_added,
    warning_parameter_became_optional,
    warning_property_deprecated,
    warning_property_reindex,
)
from foundry_lite.application.services.ontology_migration_types import (
    ActionParameterMap,
    OntologyMigrationChange,
    OntologyMigrationPlan,
    OntologyReindexOperation,
    reindex_operation,
)
from foundry_lite.application.services.ontology_yaml import (
    YamlObject,
    link_type_backing,
    mapping_sequence,
    object_type_backing,
    optional_bool,
    optional_str,
    required_str,
    yaml_object,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


def plan_ontology_migration(
    *,
    repository: OntologyRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    active_ontology_version_id: str | None,
    definition: YamlObject,
) -> OntologyMigrationPlan:
    """Build a migration plan against the active ontology version, if any."""
    if active_ontology_version_id is None:
        return OntologyMigrationPlan(None, (), ())
    current_objects = _active_object_types(repository, transaction, ctx, active_ontology_version_id)
    current_properties = _active_properties(repository, transaction, current_objects)
    current_links = repository.link_types_for_version(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        ontology_version_id=active_ontology_version_id,
    )
    current_actions = _active_actions(repository, transaction, current_objects)
    return build_ontology_migration_plan(
        source_ontology_version_id=active_ontology_version_id,
        current_objects=current_objects,
        current_properties=current_properties,
        current_links=current_links,
        current_actions=current_actions,
        definition=definition,
    )


def build_ontology_migration_plan(
    *,
    source_ontology_version_id: str | None,
    current_objects: Mapping[str, ObjectTypeRow],
    current_properties: Mapping[str, Sequence[PropertyTypeRow]],
    current_links: Sequence[LinkTypeRow],
    current_actions: Sequence[ActionTypeRow],
    definition: YamlObject,
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
        *_link_migration_changes(_link_rows_by_api(current_links), candidate_links),
        *_action_migration_changes(_action_rows_by_api(current_actions), candidate_actions),
    ]
    return OntologyMigrationPlan(source_ontology_version_id, tuple(changes), tuple(reindex_operations))


def _active_object_types(
    repository: OntologyRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    active_ontology_version_id: str,
) -> dict[str, ObjectTypeRow]:
    rows = repository.object_types_for_version(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        ontology_version_id=active_ontology_version_id,
    )
    return _object_rows_by_api(rows)


def _active_properties(
    repository: OntologyRepository,
    transaction: TransactionContext,
    current_objects: Mapping[str, ObjectTypeRow],
) -> dict[str, Sequence[PropertyTypeRow]]:
    return {
        api_name: repository.properties_for_object_type(transaction=transaction, object_type_id=row["id"])
        for api_name, row in sorted(current_objects.items())
    }


def _active_actions(
    repository: OntologyRepository,
    transaction: TransactionContext,
    current_objects: Mapping[str, ObjectTypeRow],
) -> tuple[ActionTypeRow, ...]:
    rows: list[ActionTypeRow] = []
    for object_type in current_objects.values():
        rows.extend(repository.actions_for_target(transaction=transaction, object_type_id=object_type["id"]))
    return tuple(rows)


def _object_migration_changes(
    current_objects: Mapping[str, ObjectTypeRow],
    current_properties: Mapping[str, Sequence[PropertyTypeRow]],
    candidate_objects: Mapping[str, YamlObject],
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
    current: ObjectTypeRow,
    current_properties: Sequence[PropertyTypeRow],
    candidate: YamlObject,
) -> tuple[list[OntologyMigrationChange], OntologyReindexOperation | None]:
    changes = _object_shape_changes(api_name, current, candidate)
    property_changes, reindex_fields = _property_migration_changes(api_name, current_properties, candidate)
    changes.extend(property_changes)
    if _mapping_changed(current["backing"], object_type_backing(candidate)):
        reindex_fields.append("backing")
    operation = reindex_operation(api_name, reindex_fields)
    return changes, operation


def _object_shape_changes(
    api_name: str,
    current: ObjectTypeRow,
    candidate: YamlObject,
) -> list[OntologyMigrationChange]:
    changes: list[OntologyMigrationChange] = []
    if required_str(candidate, "primaryKey") != current["primary_key_property"]:
        changes.append(blocked_primary_key_changed(api_name, current["primary_key_property"], candidate))
    if _mapping_changed(current["backing"], object_type_backing(candidate)):
        changes.append(warning_object_reindex(api_name, "backing"))
    return changes


def _property_migration_changes(
    object_api_name: str,
    current_properties: Sequence[PropertyTypeRow],
    candidate: YamlObject,
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
    current_property: PropertyTypeRow | None,
    candidate_property: YamlObject,
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
    current_properties: Sequence[PropertyTypeRow],
    candidate_properties: Mapping[str, YamlObject],
    rename_sources: Mapping[str, str],
) -> list[OntologyMigrationChange]:
    return [
        blocked_property_removed(object_api_name, current["api_name"])
        for current in current_properties
        if current["api_name"] not in candidate_properties and current["api_name"] not in rename_sources
    ]


def _link_migration_changes(
    current_links: Mapping[str, LinkTypeRow],
    candidate_links: Mapping[str, YamlObject],
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
    current: LinkTypeRow,
    candidate: YamlObject,
) -> list[OntologyMigrationChange]:
    changes: list[OntologyMigrationChange] = []
    if (
        required_str(candidate, "from") != current["from_api_name"]
        or required_str(candidate, "to") != current["to_api_name"]
    ):
        changes.append(blocked_link_endpoint_changed(api_name, current, candidate))
    if optional_str(candidate, "cardinality", "many_to_one") != current["cardinality"]:
        changes.append(blocked_link_cardinality_changed(api_name))
    if _mapping_changed(current["backing"], link_type_backing(candidate)):
        changes.append(blocked_link_backing_changed(api_name, current["backing"], link_type_backing(candidate)))
    return changes


def _action_migration_changes(
    current_actions: Mapping[str, ActionTypeRow],
    candidate_actions: Mapping[str, YamlObject],
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
    current: ActionTypeRow,
    candidate: YamlObject,
) -> list[OntologyMigrationChange]:
    changes: list[OntologyMigrationChange] = []
    if required_str(candidate, "target") != current["target_api_name"]:
        changes.append(
            blocked_action_target_changed(api_name, current["target_api_name"], required_str(candidate, "target"))
        )
    changes.extend(_parameter_migration_changes(api_name, current, candidate))
    return changes


def _parameter_migration_changes(
    action_api_name: str,
    current: ActionTypeRow,
    candidate: YamlObject,
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


def _object_rows_by_api(rows: Sequence[ObjectTypeRow]) -> dict[str, ObjectTypeRow]:
    result: dict[str, ObjectTypeRow] = {}
    for row in rows:
        api_name = row["api_name"]
        if api_name in result:
            raise ValidationFailed("duplicate persisted ontology apiName", details={"apiName": api_name})
        result[api_name] = row
    return result


def _link_rows_by_api(rows: Sequence[LinkTypeRow]) -> dict[str, LinkTypeRow]:
    result: dict[str, LinkTypeRow] = {}
    for row in rows:
        api_name = row["api_name"]
        if api_name in result:
            raise ValidationFailed("duplicate persisted ontology apiName", details={"apiName": api_name})
        result[api_name] = row
    return result


def _action_rows_by_api(rows: Sequence[ActionTypeRow]) -> dict[str, ActionTypeRow]:
    result: dict[str, ActionTypeRow] = {}
    for row in rows:
        api_name = row["api_name"]
        if api_name in result:
            raise ValidationFailed("duplicate persisted action apiName", details={"apiName": api_name})
        result[api_name] = row
    return result


def _object_definitions_by_api(definition: YamlObject) -> dict[str, YamlObject]:
    return _yaml_rows_by_api(mapping_sequence(definition, "objectTypes"), "duplicate object apiName", "objectType")


def _property_definitions_by_api(object_definition: YamlObject) -> dict[str, YamlObject]:
    return _yaml_rows_by_api(
        mapping_sequence(object_definition, "properties"),
        "duplicate property apiName",
        "property",
    )


def _link_definitions_by_api(definition: YamlObject) -> dict[str, YamlObject]:
    return _yaml_rows_by_api(mapping_sequence(definition, "linkTypes"), "duplicate link apiName", "linkType")


def _action_definitions_by_api(definition: YamlObject) -> dict[str, YamlObject]:
    return _yaml_rows_by_api(mapping_sequence(definition, "actionTypes"), "duplicate action apiName", "actionType")


def _yaml_rows_by_api(rows: Sequence[YamlObject], message: str, detail_key: str) -> dict[str, YamlObject]:
    result: dict[str, YamlObject] = {}
    for row in rows:
        api_name = required_str(row, "apiName")
        if api_name in result:
            raise ValidationFailed(message, details={detail_key: api_name})
        result[api_name] = row
    return result


def _property_by_api(rows: Sequence[PropertyTypeRow]) -> dict[str, PropertyTypeRow]:
    return {row["api_name"]: row for row in rows}


def _property_rename_sources(properties: Mapping[str, YamlObject]) -> dict[str, str]:
    result: dict[str, str] = {}
    for api_name, prop in properties.items():
        rename_source = _rename_source(prop)
        if rename_source is not None:
            result[rename_source] = api_name
    return result


def _rename_source(prop: YamlObject) -> str | None:
    for key in ("renamedFrom", "previousName", "previousApiName"):
        value = optional_str(prop, key)
        if value is not None:
            return value
    return None


def _property_requires_reindex(
    current_property: PropertyTypeRow | None,
    candidate_property: YamlObject,
) -> bool:
    if current_property is None:
        return _property_source(candidate_property) == "dataset"
    return (
        _property_source(candidate_property) != current_property["source"]
        or optional_str(candidate_property, "column") != current_property["column_name"]
    )


def _mapping_changed(previous: Mapping[str, object], next_mapping: Mapping[str, object]) -> bool:
    return _json_hash({"value": previous}) != _json_hash({"value": next_mapping})


def _property_source(prop: YamlObject) -> str:
    source = optional_str(prop, "source", "dataset" if "column" in prop else "edit_layer")
    if source is None:
        raise ValidationFailed("property source must be set", details={"property": required_str(prop, "apiName")})
    return source


def _current_action_parameters(action: ActionTypeRow) -> tuple[ActionParameterMap, ...]:
    value = action["definition"].get("parameters", ())
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValidationFailed("persisted action parameters must be a list", details={"actionType": action["api_name"]})
    return tuple(yaml_object(parameter, "parameters[]") for parameter in value)


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
