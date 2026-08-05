"""Application service helpers for ontology catalog workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports.ontology_repository import (
    ActionTypeRow,
    FunctionInputDefinition,
    FunctionOutputDefinition,
    FunctionTypeRow,
    InterfaceTypeRow,
    LinkTypeRow,
    ObjectTypeRow,
    OntologyCatalogAction,
    OntologyCatalogFunction,
    OntologyCatalogInterface,
    OntologyCatalogLink,
    OntologyCatalogObject,
    OntologyCatalogProperty,
    OntologyCatalogResult,
    OntologyVersionRow,
    PropertyTypeRow,
)
from foundry_lite.application.services.action_log_payloads import (
    action_log_edited_object_link_type_api_name,
    action_log_object_type_api_name,
)
from foundry_lite.application.services.ontology_function_validation import function_allowed_roles
from foundry_lite.application.services.ontology_interface_validation import persisted_implements
from foundry_lite.domain.ontology.datasources import backing_datasources


def build_ontology_catalog(
    *,
    active: OntologyVersionRow,
    object_rows: Sequence[ObjectTypeRow],
    link_rows: Sequence[LinkTypeRow],
    properties_by_object_id: Mapping[str, Sequence[PropertyTypeRow]],
    actions_by_object_id: Mapping[str, Sequence[ActionTypeRow]],
    interface_rows: Sequence[InterfaceTypeRow] = (),
    function_rows: Sequence[FunctionTypeRow] = (),
) -> OntologyCatalogResult:
    actions = _unique_actions(actions_by_object_id)
    return {
        "ontologyVersionId": active["id"],
        "versionNumber": active["version_number"],
        "status": active["status"],
        "createdAt": active["created_at"],
        "activatedAt": active["activated_at"],
        "interfaces": [_catalog_interface(item, object_rows) for item in interface_rows],
        "functionTypes": [_catalog_function(item) for item in function_rows],
        "objectTypes": [
            _catalog_object(
                item,
                properties=properties_by_object_id.get(item["id"], ()),
                actions=actions_by_object_id.get(item["id"], ()),
            )
            for item in object_rows
        ]
        + [_catalog_action_log_object(item) for item in actions],
        "linkTypes": [_catalog_link(item) for item in link_rows] + _catalog_action_log_links(actions, object_rows),
    }


def _unique_actions(actions_by_object_id: Mapping[str, Sequence[ActionTypeRow]]) -> list[ActionTypeRow]:
    rows = {item["id"]: item for values in actions_by_object_id.values() for item in values if item["enabled"]}
    return [rows[key] for key in sorted(rows, key=lambda item: rows[item]["api_name"])]


def _catalog_action_log_object(action: ActionTypeRow) -> OntologyCatalogObject:
    api_name = action_log_object_type_api_name(action["api_name"])
    return {
        "apiName": api_name,
        "displayName": api_name,
        "description": f"Immutable execution log for {action['display_name']}",
        "primaryKeyProperty": "actionRunId",
        "titleProperty": "actionRunId",
        "materialization": None,
        "rowPolicies": [],
        "implements": [],
        "backing": {"mode": "action_log", "actionType": action["api_name"]},
        "properties": [_action_log_catalog_property(name, data_type) for name, data_type in _ACTION_LOG_PROPERTIES],
        "actions": [],
        "config": {"isSystem": True, "actionLogFor": action["api_name"]},
    }


def _catalog_action_log_links(
    actions: Sequence[ActionTypeRow], object_rows: Sequence[ObjectTypeRow]
) -> list[OntologyCatalogLink]:
    object_types = {row["api_name"] for row in object_rows}
    return [
        _catalog_action_log_link(action, object_type)
        for action in actions
        for object_type in _action_edited_object_types(action, object_types)
    ]


def _catalog_action_log_link(action: ActionTypeRow, object_type: str) -> OntologyCatalogLink:
    api_name = action_log_edited_object_link_type_api_name(action["api_name"], object_type)
    return {
        "apiName": api_name,
        "displayName": f"{action['display_name']} edited {object_type}",
        "fromObjectType": action_log_object_type_api_name(action["api_name"]),
        "toObjectType": object_type,
        "cardinality": "many",
        "backing": {"mode": "action_log", "actionType": action["api_name"]},
    }


def _action_edited_object_types(action: ActionTypeRow, available: set[str]) -> list[str]:
    definition = action["definition"]
    values = {action["target_api_name"]}
    rules = definition.get("rules") or definition.get("rulesV2") or ()
    if isinstance(rules, Sequence) and not isinstance(rules, str | bytes):
        for rule in rules:
            if isinstance(rule, Mapping) and isinstance(rule.get("objectType"), str):
                values.add(str(rule["objectType"]))
    return sorted(values & available)


_ACTION_LOG_PROPERTIES = (
    ("actionRunId", "string"),
    ("logEntryId", "string"),
    ("definitionVersion", "string"),
    ("actorUserId", "string"),
    ("status", "string"),
    ("parameters", "struct"),
    ("result", "struct"),
    ("branchId", "string"),
    ("planHash", "string"),
    ("approvalId", "string"),
    ("revertAllowed", "boolean"),
    ("revertStatus", "string"),
    ("revertedByRunId", "string"),
    ("effectReceiptCount", "integer"),
    ("editedObjectCount", "integer"),
    ("editedObjects", "array"),
    ("createdAt", "timestamp"),
    ("completedAt", "timestamp"),
)


def _action_log_catalog_property(api_name: str, data_type: str) -> OntologyCatalogProperty:
    return {
        "apiName": api_name,
        "displayName": api_name,
        "dataType": data_type,
        "nullable": api_name in {"branchId", "planHash", "approvalId", "revertedByRunId"},
        "indexed": api_name not in {"parameters", "result", "editedObjects"},
        "searchable": api_name in {"actionRunId", "actorUserId", "status"},
        "editable": False,
        "classification": None,
        "source": "action_log",
        "columnName": None,
        "editPolicy": "source_only",
        "derivation": None,
        "datasource": None,
    }


def _catalog_interface(row: InterfaceTypeRow, object_rows: Sequence[ObjectTypeRow]) -> OntologyCatalogInterface:
    """Surface one interface plus which object types implement it."""
    return {
        "apiName": row["api_name"],
        "displayName": row["display_name"],
        "properties": list(row["definition"]["properties"]),
        "linkConstraints": list(row["definition"].get("linkConstraints", ())),
        "implementedBy": [
            item["api_name"] for item in object_rows if row["api_name"] in persisted_implements(item["config"])
        ],
    }


def _catalog_function(row: FunctionTypeRow) -> OntologyCatalogFunction:
    """Surface one function's callable signature plus its declared roles.

    The catalog exposes the signature (not the Logic DAG internals) so callers
    and generated SDKs can discover apiName, inputs, output, and who may run it.
    """
    definition = row["definition"]
    allowed_roles = function_allowed_roles(definition)
    return {
        "apiName": row["api_name"],
        "displayName": row["display_name"],
        "version": str(definition["version"]),
        "runtime": definition["runtime"],
        "inputs": [_catalog_function_input(item) for item in definition["inputs"]],
        "output": _catalog_function_output(definition["output"]),
        "allowedRoles": list(allowed_roles) if allowed_roles is not None else None,
    }


def _catalog_function_input(item: FunctionInputDefinition) -> FunctionInputDefinition:
    return {"apiName": item["apiName"], "type": item["type"], "required": item["required"]}


def _catalog_function_output(output: FunctionOutputDefinition) -> FunctionOutputDefinition:
    return {"type": output["type"]}


def _catalog_object(
    row: ObjectTypeRow,
    *,
    properties: Sequence[PropertyTypeRow],
    actions: Sequence[ActionTypeRow],
) -> OntologyCatalogObject:
    datasources = _property_datasource_names(row)
    default_datasource = _default_datasource_name(row)
    return {
        "apiName": row["api_name"],
        "displayName": row["display_name"],
        "description": row["description"],
        "primaryKeyProperty": row["primary_key_property"],
        "titleProperty": _title_property(row),
        "materialization": _materialization(row),
        "rowPolicies": _row_policies(row),
        "implements": list(persisted_implements(row["config"])),
        "backing": row["backing"],
        "properties": [_catalog_property(item, datasources, default_datasource) for item in properties],
        "actions": [_catalog_action(item) for item in actions],
        "config": row["config"],
    }


def _property_datasource_names(row: ObjectTypeRow) -> dict[str, str]:
    """Resolved datasource name per dataset property (persisted for MDO types)."""
    declared = row["config"].get("propertyDatasources")
    if not isinstance(declared, Mapping):
        return {}
    return {str(key): str(value) for key, value in declared.items() if isinstance(value, str)}


def _default_datasource_name(row: ObjectTypeRow) -> str:
    """Default segment for unmapped dataset properties (first declared datasource)."""
    return backing_datasources(row["backing"])[0].name


def _title_property(row: ObjectTypeRow) -> str | None:
    """Surface the per-record display-title property stored in config JSON."""
    value = row["config"].get("titleProperty")
    return value if isinstance(value, str) else None


def _materialization(row: ObjectTypeRow) -> dict[str, object] | None:
    """Surface the declared materialization target stored in config JSON."""
    value = row["config"].get("materialization")
    return dict(value) if isinstance(value, Mapping) else None


def _row_policies(row: ObjectTypeRow) -> list[dict[str, object]]:
    """Surface the declared row policies stored in config JSON.

    Making the declarations visible in the catalog lets operators verify which
    roles can see which rows without reading raw config blobs.
    """
    value = row["config"].get("rowPolicies")
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _catalog_property(
    row: PropertyTypeRow,
    datasources: Mapping[str, str],
    default_datasource: str,
) -> OntologyCatalogProperty:
    return {
        "apiName": row["api_name"],
        "displayName": row["display_name"],
        "dataType": row["data_type"],
        "nullable": row["nullable"],
        "indexed": row["indexed"],
        "searchable": row["searchable"],
        "editable": row["editable"],
        "classification": row["classification"],
        "source": row["source"],
        "columnName": row["column_name"],
        "editPolicy": row["edit_policy"],
        "derivation": row["derivation"],
        "datasource": _catalog_property_datasource(row, datasources, default_datasource),
    }


def _catalog_property_datasource(
    row: PropertyTypeRow,
    datasources: Mapping[str, str],
    default_datasource: str,
) -> str | None:
    """Datasource name for dataset properties: single-dataset types normalize to ``primary``."""
    if row["source"] != "dataset":
        return None
    return datasources.get(row["api_name"], default_datasource)


def _catalog_action(row: ActionTypeRow) -> OntologyCatalogAction:
    return {
        "apiName": row["api_name"],
        "displayName": row["display_name"],
        "target": row["target_api_name"],
        "parameterSchema": row["parameter_schema"],
        "definition": row["definition"],
        "enabled": row["enabled"],
    }


def _catalog_link(row: LinkTypeRow) -> OntologyCatalogLink:
    return {
        "apiName": row["api_name"],
        "displayName": row["display_name"],
        "fromObjectType": row["from_api_name"],
        "toObjectType": row["to_api_name"],
        "cardinality": row["cardinality"],
        "backing": row["backing"],
    }
