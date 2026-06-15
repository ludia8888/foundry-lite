from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.ontology_repository import (
    ActionMutationDefinition,
    ActionTypeRow,
    LinkTypeRow,
    ObjectTypeRow,
    OntologyValidationResult,
    PropertyTypeRow,
)
from foundry_lite.application.services.ontology_yaml import (
    YamlObject,
    action_type_definition,
    link_type_backing,
    mapping_sequence,
    object_type_backing,
    optional_bool,
    optional_str,
    required_str,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

DatasetColumnsLookup = Callable[
    [TransactionContext, RequestContext, str],
    Mapping[str, Mapping[str, object]],
]


def validate_persisted_object_type(
    object_type: ObjectTypeRow,
    properties: Iterable[PropertyTypeRow],
    actions: Iterable[ActionTypeRow],
    columns: Mapping[str, Mapping[str, object]],
) -> None:
    property_by_api = {prop["api_name"]: prop for prop in properties}
    _validate_persisted_primary_key_property(object_type, property_by_api, columns)
    _validate_persisted_dataset_properties(object_type, property_by_api.values(), columns)
    for action in actions:
        for mutation in action["definition"].get("mutations", []):
            _validate_persisted_action_mutation_property(mutation, property_by_api)


def validate_persisted_link(
    link: LinkTypeRow,
    object_by_api: Mapping[str, ObjectTypeRow],
    columns: Mapping[str, Mapping[str, object]],
) -> None:
    if link["from_api_name"] not in object_by_api or link["to_api_name"] not in object_by_api:
        raise ValidationFailed("link object type missing", details={"linkType": link["api_name"]})
    missing_keys = [key for key in (link["backing"]["fromKey"], link["backing"]["toKey"]) if key not in columns]
    if missing_keys:
        raise ValidationFailed(
            "link backing key missing",
            details={"linkType": link["api_name"], "missing": missing_keys},
        )


def ontology_validation_result(definition: YamlObject) -> OntologyValidationResult:
    return {
        "status": "valid",
        "object_type_count": len(mapping_sequence(definition, "objectTypes")),
        "link_type_count": len(mapping_sequence(definition, "linkTypes")),
        "action_type_count": len(mapping_sequence(definition, "actionTypes")),
    }


def validate_ontology_definition(
    conn: TransactionContext,
    ctx: RequestContext,
    definition: YamlObject,
    dataset_columns_for_ref: DatasetColumnsLookup,
) -> None:
    object_defs = _object_definitions_by_api(definition)
    for object_def in object_defs.values():
        _validate_yaml_object_type(conn, ctx, definition, object_def, dataset_columns_for_ref)
    for link_def in mapping_sequence(definition, "linkTypes"):
        _validate_yaml_link(conn, ctx, link_def, object_defs, dataset_columns_for_ref)


def _object_definitions_by_api(definition: YamlObject) -> dict[str, YamlObject]:
    object_defs: dict[str, YamlObject] = {}
    for item in mapping_sequence(definition, "objectTypes"):
        api_name = required_str(item, "apiName")
        if api_name in object_defs:
            raise ValidationFailed("duplicate object apiName", details={"objectType": api_name})
        object_defs[api_name] = item
    return object_defs


def _validate_persisted_primary_key_property(
    object_type: ObjectTypeRow,
    property_by_api: Mapping[str, PropertyTypeRow],
    columns: Mapping[str, Mapping[str, object]],
) -> None:
    pk_prop = object_type["primary_key_property"]
    if pk_prop not in property_by_api:
        raise ValidationFailed("primary key property missing", details={"objectType": object_type["api_name"]})
    pk_column = property_by_api[pk_prop]["column_name"]
    if pk_column is None or pk_column not in columns:
        raise ValidationFailed("primary key column missing", details={"column": pk_column})
    if bool(columns[pk_column].get("nullable")):
        raise ValidationFailed("primary key column must be non-null", details={"column": pk_column})


def _validate_persisted_dataset_properties(
    object_type: ObjectTypeRow,
    properties: Iterable[PropertyTypeRow],
    columns: Mapping[str, Mapping[str, object]],
) -> None:
    for prop in properties:
        if prop["source"] == "dataset" and prop["column_name"] not in columns:
            raise ValidationFailed(
                "property column missing",
                details={"objectType": object_type["api_name"], "property": prop["api_name"]},
            )


def _validate_persisted_action_mutation_property(
    mutation: object,
    property_by_api: Mapping[str, PropertyTypeRow],
) -> None:
    if not isinstance(mutation, Mapping):
        raise ValidationFailed("action mutation must be a mapping")
    prop = mutation.get("property")
    if not isinstance(prop, str) or prop not in property_by_api:
        raise ValidationFailed("action mutation property missing", details=dict(mutation))
    if not property_by_api[prop]["editable"]:
        raise ValidationFailed("action mutation property must be editable", details=dict(mutation))


def _validate_yaml_object_type(
    conn: TransactionContext,
    ctx: RequestContext,
    definition: YamlObject,
    object_def: YamlObject,
    dataset_columns_for_ref: DatasetColumnsLookup,
) -> None:
    object_api_name = required_str(object_def, "apiName")
    columns = dataset_columns_for_ref(conn, ctx, object_type_backing(object_def)["dataset"])
    property_defs = _property_definitions_by_api(object_def)
    _validate_yaml_primary_key(object_def, property_defs, columns)
    _validate_yaml_dataset_properties(object_api_name, property_defs.values(), columns)
    _validate_yaml_action_mutations(definition, object_api_name, property_defs)


def _property_definitions_by_api(object_def: YamlObject) -> dict[str, YamlObject]:
    property_defs: dict[str, YamlObject] = {}
    for prop in mapping_sequence(object_def, "properties"):
        prop_api = required_str(prop, "apiName")
        if prop_api in property_defs:
            raise ValidationFailed("duplicate property apiName", details={"property": prop_api})
        property_defs[prop_api] = prop
    return property_defs


def _validate_yaml_primary_key(
    object_def: YamlObject,
    property_defs: Mapping[str, YamlObject],
    columns: Mapping[str, Mapping[str, object]],
) -> None:
    pk_prop = required_str(object_def, "primaryKey")
    if pk_prop not in property_defs:
        raise ValidationFailed(
            "primary key property missing",
            details={"objectType": required_str(object_def, "apiName")},
        )
    pk_column = optional_str(property_defs[pk_prop], "column")
    if pk_column is None or pk_column not in columns:
        raise ValidationFailed("primary key column missing", details={"column": pk_column})
    if bool(columns[pk_column].get("nullable")):
        raise ValidationFailed("primary key column must be non-null", details={"column": pk_column})


def _validate_yaml_dataset_properties(
    object_api_name: str,
    properties: Iterable[YamlObject],
    columns: Mapping[str, Mapping[str, object]],
) -> None:
    for prop in properties:
        source = optional_str(prop, "source", "dataset" if "column" in prop else "edit_layer")
        if source == "dataset" and optional_str(prop, "column") not in columns:
            raise ValidationFailed(
                "property column missing",
                details={"objectType": object_api_name, "property": required_str(prop, "apiName")},
            )


def _validate_yaml_action_mutations(
    definition: YamlObject,
    object_api_name: str,
    property_defs: Mapping[str, YamlObject],
) -> None:
    for action_def in mapping_sequence(definition, "actionTypes"):
        if required_str(action_def, "target") != object_api_name:
            continue
        for mutation in action_type_definition(action_def).get("mutations", ()):
            _validate_yaml_action_mutation_property(mutation, property_defs)


def _validate_yaml_action_mutation_property(
    mutation: ActionMutationDefinition,
    property_defs: Mapping[str, YamlObject],
) -> None:
    prop = mutation.get("property")
    if not isinstance(prop, str) or prop not in property_defs:
        raise ValidationFailed("action mutation property missing", details=dict(mutation))
    if not optional_bool(property_defs[prop], "editable", False):
        raise ValidationFailed("action mutation property must be editable", details=dict(mutation))


def _validate_yaml_link(
    conn: TransactionContext,
    ctx: RequestContext,
    link_def: YamlObject,
    object_defs: Mapping[str, YamlObject],
    dataset_columns_for_ref: DatasetColumnsLookup,
) -> None:
    from_api = required_str(link_def, "from")
    to_api = required_str(link_def, "to")
    if from_api not in object_defs or to_api not in object_defs:
        raise ValidationFailed("link references unknown object type", details=dict(link_def))
    backing = link_type_backing(link_def)
    columns = set(dataset_columns_for_ref(conn, ctx, backing["dataset"]))
    missing_keys = [key for key in (backing["fromKey"], backing["toKey"]) if key not in columns]
    if missing_keys:
        raise ValidationFailed(
            "link backing key missing",
            details={
                "linkType": required_str(link_def, "apiName"),
                "missing": missing_keys,
            },
        )
