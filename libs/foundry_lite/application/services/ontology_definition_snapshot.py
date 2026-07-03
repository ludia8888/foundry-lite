"""Rebuild a YAML-shaped ontology definition from persisted version rows.

Rollback restores an archived version by replaying its persisted shape through
the one activation path (validate -> plan -> import -> activate). Reconstructing
the YAML-equivalent definition here means rollback reuses that machinery instead
of forking a second import/activation flow.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports.ontology_repository import (
    ActionTypeRow,
    FunctionTypeRow,
    InterfaceTypeRow,
    LinkTypeRow,
    ObjectTypeRow,
    PropertyTypeRow,
)
from foundry_lite.application.services.object_store.row_policies import persisted_row_policies
from foundry_lite.application.services.ontology_interface_validation import persisted_implements

JsonObject = dict[str, object]


def ontology_definition_snapshot(
    *,
    object_rows: Sequence[ObjectTypeRow],
    properties_by_object_id: dict[str, Sequence[PropertyTypeRow]],
    link_rows: Sequence[LinkTypeRow],
    action_rows: Sequence[ActionTypeRow],
    interface_rows: Sequence[InterfaceTypeRow] = (),
    function_rows: Sequence[FunctionTypeRow] = (),
) -> JsonObject:
    """Return the YAML-shaped definition equivalent to a persisted ontology version."""
    definition: JsonObject = {
        "objectTypes": [
            _object_type_definition(row, properties_by_object_id.get(row["id"], ())) for row in object_rows
        ],
        "linkTypes": [_link_type_definition(row) for row in link_rows],
        "actionTypes": [dict(row["definition"]) for row in action_rows],
    }
    # Interfaces must survive rollback: the restored version replays through
    # the same YAML-shaped import path (parse -> conformance -> persist).
    if interface_rows:
        definition["interfaces"] = [dict(row["definition"]) for row in interface_rows]
    # Functions must survive rollback for the same reason — the persisted
    # definition is already the normalized YAML shape, so replay re-validates
    # and re-persists it byte-identically (no spurious definition-change).
    if function_rows:
        definition["functionTypes"] = [dict(row["definition"]) for row in function_rows]
    return definition


def _object_type_definition(row: ObjectTypeRow, properties: Sequence[PropertyTypeRow]) -> JsonObject:
    # Multi-datasource property assignments must survive rollback: the
    # resolved map lives in config JSON, so each dataset property re-emits its
    # ``datasource`` and replay re-resolves the identical mapping.
    property_datasources = _persisted_property_datasources(row)
    definition: JsonObject = {
        "apiName": row["api_name"],
        "displayName": row["display_name"],
        "primaryKey": row["primary_key_property"],
        "backing": dict(row["backing"]),
        "properties": [_property_definition(prop, property_datasources.get(prop["api_name"])) for prop in properties],
    }
    if row["description"] is not None:
        definition["description"] = row["description"]
    title_property = row["config"].get("titleProperty")
    if isinstance(title_property, str):
        definition["titleProperty"] = title_property
    # Materialization declarations must survive rollback: the restored version
    # replays through the same YAML-shaped import path as a fresh apply.
    materialization = row["config"].get("materialization")
    if isinstance(materialization, Mapping):
        definition["materialization"] = dict(materialization)
    # Row policies must survive rollback for the same reason — dropping (or
    # silently skipping) one on restore would fail open on a security control,
    # so malformed persisted entries raise via persisted_row_policies instead.
    policies = persisted_row_policies(row["config"])
    if policies:
        definition["rowPolicies"] = [{"role": role, "filter": dict(policy_filter)} for role, policy_filter in policies]
    # Implements must survive rollback too: dropping one on restore would both
    # shrink interface query results and later read as a blocked drop.
    implements = persisted_implements(row["config"])
    if implements:
        definition["implements"] = list(implements)
    return definition


def _property_definition(row: PropertyTypeRow, datasource: str | None = None) -> JsonObject:
    definition: JsonObject = {
        "apiName": row["api_name"],
        "displayName": row["display_name"],
        "type": row["data_type"],
        "nullable": row["nullable"],
        "indexed": row["indexed"],
        "searchable": row["searchable"],
        "editable": row["editable"],
        "source": row["source"],
        "editPolicy": row["edit_policy"],
    }
    if row["column_name"] is not None:
        definition["column"] = row["column_name"]
    if row["classification"] is not None:
        definition["classification"] = row["classification"]
    if row["derivation"]:
        definition["derivation"] = dict(row["derivation"])
    if datasource is not None and row["source"] == "dataset":
        definition["datasource"] = datasource
    return definition


def _persisted_property_datasources(row: ObjectTypeRow) -> dict[str, str]:
    declared = row["config"].get("propertyDatasources")
    if not isinstance(declared, Mapping):
        return {}
    return {str(key): str(value) for key, value in declared.items() if isinstance(value, str)}


def _link_type_definition(row: LinkTypeRow) -> JsonObject:
    return {
        "apiName": row["api_name"],
        "displayName": row["display_name"],
        "from": row["from_api_name"],
        "to": row["to_api_name"],
        "cardinality": row["cardinality"],
        "backing": dict(row["backing"]),
    }
