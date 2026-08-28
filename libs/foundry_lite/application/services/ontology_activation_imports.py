"""Persistence helpers for object, property, and link ontology definitions."""

from __future__ import annotations

from foundry_lite.application.ports import (
    LinkTypeRecord,
    OntologyRepository,
    PropertyTypeRecord,
    TransactionContext,
)
from foundry_lite.application.primitives import _new_id
from foundry_lite.application.services.ontology_activation_contracts import object_type_implements
from foundry_lite.application.services.ontology_datasource_validation import yaml_property_datasources
from foundry_lite.application.services.ontology_migration import OntologyMigrationPlan
from foundry_lite.application.services.ontology_migration_types import object_type_serving_config
from foundry_lite.application.services.ontology_yaml import (
    YamlObject,
    link_type_backing,
    mapping_sequence,
    object_type_materialization_config,
    object_type_media_properties,
    object_type_row_policies,
    optional_bool,
    optional_str,
    property_derivation,
    required_str,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


def object_type_config(
    item: YamlObject,
    migration_plan: OntologyMigrationPlan,
    api_name: str,
) -> dict[str, object]:
    config = object_type_serving_config(migration_plan, api_name)
    optional_values = {
        "titleProperty": optional_str(item, "titleProperty"),
        "materialization": object_type_materialization_config(item),
        "propertyDatasources": yaml_property_datasources(item),
    }
    config.update({key: value for key, value in optional_values.items() if value is not None})
    sequence_values = {"rowPolicies": object_type_row_policies(item), "implements": object_type_implements(item)}
    config.update({key: list(value) for key, value in sequence_values.items() if value})
    media_properties = object_type_media_properties(item)
    if media_properties:
        config["mediaProperties"] = media_properties
    return config


def import_properties_for_object_type(
    repository: OntologyRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    object_id: str,
    item: YamlObject,
) -> None:
    seen_properties: set[str] = set()
    for prop in mapping_sequence(item, "properties"):
        prop_api = required_str(prop, "apiName")
        if prop_api in seen_properties:
            raise ValidationFailed("duplicate property apiName", details={"property": prop_api})
        seen_properties.add(prop_api)
        insert_property_type(repository, conn, ctx, object_id, prop)


def insert_property_type(
    repository: OntologyRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    object_id: str,
    prop: YamlObject,
) -> None:
    prop_api = required_str(prop, "apiName")
    source = optional_str(prop, "source", "dataset" if "column" in prop else "edit_layer")
    if source is None:
        raise ValidationFailed("property source must be set", details={"property": prop_api})
    repository.insert_property_type(
        transaction=conn,
        record=_property_type_record(ctx, object_id, prop, prop_api, source),
    )


def _property_type_record(
    ctx: RequestContext,
    object_id: str,
    prop: YamlObject,
    prop_api: str,
    source: str,
) -> PropertyTypeRecord:
    return PropertyTypeRecord(
        property_type_id=_new_id("ptype"),
        tenant_id=ctx.tenant_id,
        object_type_id=object_id,
        api_name=prop_api,
        display_name=optional_str(prop, "displayName", prop_api) or prop_api,
        data_type=required_str(prop, "type"),
        nullable=optional_bool(prop, "nullable", True),
        indexed=optional_bool(prop, "indexed", False),
        searchable=optional_bool(prop, "searchable", False),
        editable=optional_bool(prop, "editable", False),
        classification=optional_str(prop, "classification"),
        source=source,
        column_name=optional_str(prop, "column"),
        edit_policy=optional_str(prop, "editPolicy", "edit_only" if source == "edit_layer" else "source_wins")
        or "source_wins",
        derivation=property_derivation(prop),
    )


def import_link_types(
    repository: OntologyRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    ontology_version_id: str,
    definition: YamlObject,
    object_map: dict[str, str],
) -> None:
    for item in mapping_sequence(definition, "linkTypes"):
        insert_link_type(repository, conn, ctx, ontology_version_id, item, object_map)


def insert_link_type(
    repository: OntologyRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    ontology_version_id: str,
    item: YamlObject,
    object_map: dict[str, str],
) -> None:
    from_api = required_str(item, "from")
    to_api = required_str(item, "to")
    if from_api not in object_map or to_api not in object_map:
        raise ValidationFailed("link references unknown object type", details=dict(item))
    api_name = required_str(item, "apiName")
    repository.insert_link_type(
        transaction=conn,
        record=LinkTypeRecord(
            link_type_id=_new_id("ltype"),
            tenant_id=ctx.tenant_id,
            ontology_version_id=ontology_version_id,
            api_name=api_name,
            display_name=optional_str(item, "displayName", api_name) or api_name,
            from_object_type_id=object_map[from_api],
            from_api_name=from_api,
            to_object_type_id=object_map[to_api],
            to_api_name=to_api,
            cardinality=optional_str(item, "cardinality", "many_to_one") or "many_to_one",
            backing=link_type_backing(item),
        ),
    )
