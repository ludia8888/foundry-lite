"""Change constructors for pure ontology migration planning."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.domain.ontology.migration_types import ActionParameterMap, OntologyMigrationChange


def blocked_object_removed(api_name: str) -> OntologyMigrationChange:
    """Return a blocking change for removing an existing object type."""
    return _blocked("object_removed", f"objectTypes.{api_name}", "removing an object type breaks object APIs", True)


def blocked_primary_key_changed(
    api_name: str,
    previous: str,
    candidate: Mapping[str, object],
) -> OntologyMigrationChange:
    """Return a blocking change for changing an object primary key."""
    return _blocked(
        "object_primary_key_changed",
        f"objectTypes.{api_name}.primaryKey",
        "changing an object primary key requires a new object migration plan",
        True,
        details={"previous": previous, "next": _required_str(candidate, "primaryKey")},
    )


def warning_object_reindex(api_name: str, field: str) -> OntologyMigrationChange:
    """Return a warning change when an object mapping needs reindexing."""
    return _warning(
        "object_reindex_required",
        f"objectTypes.{api_name}.{field}",
        "object backing changed and the active object index must be rebuilt before serving the new mapping",
        has_object_reindex_requirement=True,
    )


def blocked_property_rename(object_api_name: str, previous: str, next_name: str) -> OntologyMigrationChange:
    """Return a blocking change for a property rename without migration execution."""
    return _blocked(
        "property_renamed",
        f"objectTypes.{object_api_name}.properties.{next_name}",
        "property renames require explicit consumer mapping and SDK versioning",
        True,
        details={"renamedFrom": previous, "apiName": next_name},
    )


def blocked_property_removed(object_api_name: str, api_name: str) -> OntologyMigrationChange:
    """Return a blocking change for removing a property from an existing object."""
    return _blocked(
        "property_removed",
        f"objectTypes.{object_api_name}.properties.{api_name}",
        "removing a property breaks existing object consumers",
        True,
    )


def blocked_property_type_change(
    object_api_name: str,
    api_name: str,
    current: Mapping[str, object],
    candidate: Mapping[str, object],
) -> OntologyMigrationChange:
    """Return a blocking change for changing a property's SDK-visible type."""
    return _blocked(
        "property_type_changed",
        f"objectTypes.{object_api_name}.properties.{api_name}.type",
        "changing a property type changes the generated SDK contract",
        True,
        details={"previous": current["data_type"], "next": _required_str(candidate, "type")},
    )


def warning_property_deprecated(object_api_name: str, api_name: str) -> OntologyMigrationChange:
    """Return a warning change for a property that remains readable but deprecated."""
    return _warning(
        "property_deprecated",
        f"objectTypes.{object_api_name}.properties.{api_name}",
        "deprecated property remains readable but consumers should migrate",
    )


def warning_property_reindex(
    object_api_name: str,
    api_name: str,
    current: Mapping[str, object],
    candidate: Mapping[str, object],
) -> OntologyMigrationChange:
    """Return a warning change for a property mapping that needs reindexing."""
    return _warning(
        "property_mapping_changed",
        f"objectTypes.{object_api_name}.properties.{api_name}.column",
        "property backing changed and the active object index needs a deterministic rebuild",
        has_object_reindex_requirement=True,
        details={
            "previousColumn": current["column_name"],
            "nextColumn": _optional_str(candidate, "column"),
        },
    )


def warning_datasource_added(object_api_name: str, datasource_name: str) -> OntologyMigrationChange:
    """Return a warning change for adding a datasource to an object backing."""
    return _warning(
        "object_datasource_added",
        f"objectTypes.{object_api_name}.backing.datasources.{datasource_name}",
        "adding a datasource widens the object mapping and the active object index must be rebuilt",
        has_object_reindex_requirement=True,
    )


def blocked_datasource_removed(object_api_name: str, datasource_name: str) -> OntologyMigrationChange:
    """Return a blocking change for removing a datasource from an object backing."""
    return _blocked(
        "object_datasource_removed",
        f"objectTypes.{object_api_name}.backing.datasources.{datasource_name}",
        "removing a datasource silently nulls every property it backs for existing consumers",
        True,
    )


def blocked_datasource_dataset_changed(
    object_api_name: str,
    datasource_name: str,
    previous: str,
    next_dataset: str,
) -> OntologyMigrationChange:
    """Return a blocking change for rebinding a datasource to another dataset."""
    return _blocked(
        "object_datasource_dataset_changed",
        f"objectTypes.{object_api_name}.backing.datasources.{datasource_name}.dataset",
        "changing a datasource's dataset rebinds every property of that segment and requires a migration plan",
        True,
        details={"previous": previous, "next": next_dataset},
    )


def blocked_property_datasource_moved(
    object_api_name: str,
    property_api_name: str,
    previous: str,
    next_dataset: str,
) -> OntologyMigrationChange:
    """Return a blocking change for moving a property between datasources."""
    return _blocked(
        "property_datasource_moved",
        f"objectTypes.{object_api_name}.properties.{property_api_name}.datasource",
        "moving a property between datasources changes which dataset and segment role feed its values",
        True,
        details={"previous": previous, "next": next_dataset},
    )


def warning_datasource_required_role_changed(
    object_api_name: str,
    datasource_name: str,
    previous: str | None,
    next_role: str | None,
) -> OntologyMigrationChange:
    """Return a warning change for re-scoping a datasource's required access role."""
    return _warning(
        "object_datasource_required_role_changed",
        f"objectTypes.{object_api_name}.backing.datasources.{datasource_name}.requiredRole",
        "changing a datasource requiredRole re-scopes which callers can read the segment's property values",
        details={"previous": previous, "next": next_role},
    )


def warning_interface_added(api_name: str) -> OntologyMigrationChange:
    """Return a warning change for adding a new interface type."""
    return _warning(
        "interface_added",
        f"interfaces.{api_name}",
        "adding an interface is backward-compatible but changes the polymorphic SDK surface",
    )


def blocked_interface_removed(api_name: str) -> OntologyMigrationChange:
    """Return a blocking change for removing an interface type."""
    return _blocked(
        "interface_removed",
        f"interfaces.{api_name}",
        "removing an interface breaks OSDK apps that query or type against it",
        True,
    )


def blocked_interface_property_removed(interface_api_name: str, api_name: str) -> OntologyMigrationChange:
    """Return a blocking change for removing a shared property contract."""
    return _blocked(
        "interface_property_removed",
        f"interfaces.{interface_api_name}.properties.{api_name}",
        "removing an interface property breaks consumers reading it across implementing types",
        True,
    )


def warning_implements_added(object_api_name: str, interface_api_name: str) -> OntologyMigrationChange:
    """Return a warning change for an object type newly implementing an interface."""
    return _warning(
        "object_implements_added",
        f"objectTypes.{object_api_name}.implements.{interface_api_name}",
        "implementing an interface is backward-compatible but widens interface query results",
    )


def blocked_implements_removed(object_api_name: str, interface_api_name: str) -> OntologyMigrationChange:
    """Return a blocking change for an object type dropping an interface it implements."""
    return _blocked(
        "object_implements_removed",
        f"objectTypes.{object_api_name}.implements.{interface_api_name}",
        "dropping an implements declaration removes the type from interface queries OSDK apps depend on",
        True,
    )


def warning_function_added(api_name: str) -> OntologyMigrationChange:
    """Return a warning change for adding a new function type."""
    return _warning(
        "function_added",
        f"functionTypes.{api_name}",
        "adding a function is backward-compatible but changes the callable SDK surface",
    )


def blocked_function_removed(api_name: str) -> OntologyMigrationChange:
    """Return a blocking change for removing a function type."""
    return _blocked(
        "function_removed",
        f"functionTypes.{api_name}",
        "removing a function breaks OSDK apps and Logic callers executing it",
        True,
    )


def warning_function_definition_changed(api_name: str) -> OntologyMigrationChange:
    """Return a warning change for changing a function's persisted definition."""
    return _warning(
        "function_definition_changed",
        f"functionTypes.{api_name}",
        "changing a function definition changes execution behavior for existing callers",
    )


def blocked_link_removed(api_name: str) -> OntologyMigrationChange:
    """Return a blocking change for removing a link type."""
    return _blocked("link_removed", f"linkTypes.{api_name}", "removing a link type breaks graph traversal APIs", True)


def blocked_link_endpoint_changed(
    api_name: str,
    current: Mapping[str, object],
    candidate: Mapping[str, object],
) -> OntologyMigrationChange:
    """Return a blocking change for changing a link's from/to endpoints."""
    return _blocked(
        "link_endpoint_changed",
        f"linkTypes.{api_name}",
        "changing link endpoints changes the graph API contract",
        True,
        details={
            "previous": {"from": current["from_api_name"], "to": current["to_api_name"]},
            "next": {"from": _required_str(candidate, "from"), "to": _required_str(candidate, "to")},
        },
    )


def blocked_link_cardinality_changed(api_name: str) -> OntologyMigrationChange:
    """Return a blocking change for changing link cardinality."""
    return _blocked(
        "link_cardinality_changed",
        f"linkTypes.{api_name}.cardinality",
        "changing link cardinality changes traversal expectations",
        True,
    )


def blocked_link_backing_changed(
    api_name: str,
    previous: Mapping[str, object],
    next_backing: Mapping[str, object],
) -> OntologyMigrationChange:
    """Return a blocking change for changing link backing keys or dataset."""
    return _blocked(
        "link_backing_changed",
        f"linkTypes.{api_name}.backing",
        "changing link backing keys requires a link migration plan",
        True,
        details={"previous": dict(previous), "next": dict(next_backing)},
    )


def blocked_action_removed(api_name: str) -> OntologyMigrationChange:
    """Return a blocking change for removing an action type."""
    return _blocked("action_removed", f"actionTypes.{api_name}", "removing an action breaks SDK callers", True)


def blocked_action_target_changed(api_name: str, previous: str, next_target: str) -> OntologyMigrationChange:
    """Return a blocking change for moving an action to another object type."""
    return _blocked(
        "action_target_changed",
        f"actionTypes.{api_name}.target",
        "changing an action target changes the generated SDK contract",
        True,
        details={"previous": previous, "next": next_target},
    )


def blocked_parameter_removed(action_api_name: str, api_name: str) -> OntologyMigrationChange:
    """Return a blocking change for removing an existing action parameter."""
    return _blocked(
        "action_parameter_removed",
        f"actionTypes.{action_api_name}.parameters.{api_name}",
        "removing an action parameter breaks generated SDK callers",
        True,
    )


def blocked_parameter_type_changed(
    action_api_name: str,
    api_name: str,
    current: ActionParameterMap,
    candidate: ActionParameterMap,
) -> OntologyMigrationChange:
    """Return a blocking change for changing an action parameter type."""
    return _blocked(
        "action_parameter_type_changed",
        f"actionTypes.{action_api_name}.parameters.{api_name}.type",
        "changing an action parameter type requires a generated SDK major version",
        True,
        details={"previous": _required_str(current, "type"), "next": _required_str(candidate, "type")},
    )


def blocked_parameter_became_required(action_api_name: str, api_name: str) -> OntologyMigrationChange:
    """Return a blocking change for making an optional parameter required."""
    return _blocked(
        "action_parameter_became_required",
        f"actionTypes.{action_api_name}.parameters.{api_name}.required",
        "making an existing optional parameter required breaks old SDK callers",
        True,
    )


def blocked_required_parameter_added(action_api_name: str, api_name: str) -> OntologyMigrationChange:
    """Return a blocking change for adding a new required action parameter."""
    return _blocked(
        "required_action_parameter_added",
        f"actionTypes.{action_api_name}.parameters.{api_name}",
        "adding a required action parameter requires a generated SDK major version",
        True,
    )


def warning_parameter_became_optional(action_api_name: str, api_name: str) -> OntologyMigrationChange:
    """Return a warning change for relaxing a parameter from required to optional."""
    return _warning(
        "action_parameter_became_optional",
        f"actionTypes.{action_api_name}.parameters.{api_name}.required",
        "making an action parameter optional is backward-compatible but should be visible to consumers",
    )


def warning_optional_parameter_added(action_api_name: str, api_name: str) -> OntologyMigrationChange:
    """Return a warning change for adding a compatible optional action parameter."""
    return _warning(
        "optional_action_parameter_added",
        f"actionTypes.{action_api_name}.parameters.{api_name}",
        "adding an optional parameter is compatible but changes the SDK surface",
    )


def _blocked(
    kind: str,
    path: str,
    reason: str,
    has_sdk_major_version_requirement: bool,
    *,
    details: Mapping[str, object] | None = None,
) -> OntologyMigrationChange:
    return OntologyMigrationChange(
        kind,
        path,
        "blocked",
        reason,
        has_sdk_major_version_requirement,
        details=details or {},
    )


def _warning(
    kind: str,
    path: str,
    reason: str,
    *,
    has_object_reindex_requirement: bool = False,
    details: Mapping[str, object] | None = None,
) -> OntologyMigrationChange:
    return OntologyMigrationChange(
        kind,
        path,
        "warning",
        reason,
        has_object_reindex_requirement=has_object_reindex_requirement,
        details=details or {},
    )


def _optional_str(mapping: Mapping[str, object], key: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    return str(value)


def _required_str(mapping: Mapping[str, object], key: str) -> str:
    value = _optional_str(mapping, key)
    if value is None or value == "":
        return ""
    return value
