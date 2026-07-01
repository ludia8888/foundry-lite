"""Application service helpers for ontology catalog workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports.ontology_repository import (
    ActionTypeRow,
    LinkTypeRow,
    ObjectTypeRow,
    OntologyCatalogAction,
    OntologyCatalogLink,
    OntologyCatalogObject,
    OntologyCatalogProperty,
    OntologyCatalogResult,
    OntologyVersionRow,
    PropertyTypeRow,
)


def build_ontology_catalog(
    *,
    active: OntologyVersionRow,
    object_rows: Sequence[ObjectTypeRow],
    link_rows: Sequence[LinkTypeRow],
    properties_by_object_id: Mapping[str, Sequence[PropertyTypeRow]],
    actions_by_object_id: Mapping[str, Sequence[ActionTypeRow]],
) -> OntologyCatalogResult:
    return {
        "ontologyVersionId": active["id"],
        "versionNumber": active["version_number"],
        "status": active["status"],
        "createdAt": active["created_at"],
        "activatedAt": active["activated_at"],
        "objectTypes": [
            _catalog_object(
                item,
                properties=properties_by_object_id.get(item["id"], ()),
                actions=actions_by_object_id.get(item["id"], ()),
            )
            for item in object_rows
        ],
        "linkTypes": [_catalog_link(item) for item in link_rows],
    }


def _catalog_object(
    row: ObjectTypeRow,
    *,
    properties: Sequence[PropertyTypeRow],
    actions: Sequence[ActionTypeRow],
) -> OntologyCatalogObject:
    return {
        "apiName": row["api_name"],
        "displayName": row["display_name"],
        "description": row["description"],
        "primaryKeyProperty": row["primary_key_property"],
        "backing": row["backing"],
        "properties": [_catalog_property(item) for item in properties],
        "actions": [_catalog_action(item) for item in actions],
        "config": row["config"],
    }


def _catalog_property(row: PropertyTypeRow) -> OntologyCatalogProperty:
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
    }


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
