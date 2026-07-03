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
        "titleProperty": _title_property(row),
        "materialization": _materialization(row),
        "rowPolicies": _row_policies(row),
        "backing": row["backing"],
        "properties": [_catalog_property(item) for item in properties],
        "actions": [_catalog_action(item) for item in actions],
        "config": row["config"],
    }


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
