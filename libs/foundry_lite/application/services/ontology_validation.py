"""Validation rules shared by ontology activation and preview checks."""

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
from foundry_lite.application.services.ontology_catalog import build_ontology_catalog as build_ontology_catalog
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

__all__ = [
    "build_ontology_catalog",
    "ontology_validation_result",
    "validate_ontology_definition",
    "validate_persisted_link",
    "validate_persisted_object_type",
]

DatasetColumnsLookup = Callable[
    [TransactionContext, RequestContext, str],
    Mapping[str, Mapping[str, object]],
]

PROPERTY_DATA_TYPES = frozenset({"boolean", "float", "integer", "string"})
# Object properties may additionally hold an immutable media reference (doc §1.6): the
# Ontology pins a media_item_version via a media_reference property, never a media-backed
# object. Action *parameters* stay scalar — media is bound via an explicit edit, not a param.
OBJECT_PROPERTY_DATA_TYPES = PROPERTY_DATA_TYPES | frozenset({"media_reference"})
PROPERTY_SOURCES = frozenset({"dataset", "edit_layer"})
PROPERTY_EDIT_POLICIES = frozenset({"conflict_requires_review", "edit_only", "edit_wins", "source_wins"})
PROPERTY_CLASSIFICATIONS = frozenset({"finance", "pii", "public"})
LINK_CARDINALITIES = frozenset({"many_to_many", "many_to_one", "one_to_many", "one_to_one"})
OBJECT_BACKING_MODES = frozenset({"snapshot"})
CDC_DELETE_POLICIES = frozenset({"tombstone"})
ACTION_MUTATION_TYPES = frozenset({"setProperty"})


def validate_persisted_object_type(
    object_type: ObjectTypeRow,
    properties: Iterable[PropertyTypeRow],
    actions: Iterable[ActionTypeRow],
    columns: Mapping[str, Mapping[str, object]],
) -> None:
    """Validate an already-persisted object type against its dataset schema."""
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
    """Validate a persisted link type against object references and keys."""
    if link["from_api_name"] not in object_by_api or link["to_api_name"] not in object_by_api:
        raise ValidationFailed("link object type missing", details={"linkType": link["api_name"]})
    missing_keys = [key for key in (link["backing"]["fromKey"], link["backing"]["toKey"]) if key not in columns]
    if missing_keys:
        raise ValidationFailed(
            "link backing key missing",
            details={"linkType": link["api_name"], "missing": missing_keys},
        )


def ontology_validation_result(definition: YamlObject) -> OntologyValidationResult:
    """Build the success payload returned by ontology preview validation."""
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
    """Validate a YAML ontology definition without persisting it."""
    object_defs = _object_definitions_by_api(definition)
    link_defs = _link_definitions_by_api(definition)
    _action_definitions_by_api(definition)
    for object_def in object_defs.values():
        _validate_yaml_object_type(conn, ctx, definition, object_def, dataset_columns_for_ref)
    for link_def in link_defs.values():
        _validate_yaml_link(conn, ctx, link_def, object_defs, dataset_columns_for_ref)


def _object_definitions_by_api(definition: YamlObject) -> dict[str, YamlObject]:
    """Return object definitions keyed by API name, rejecting duplicates."""
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
    """Ensure the persisted primary key property maps to a non-null column."""
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
    """Ensure persisted dataset-backed properties still map to columns."""
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
    """Ensure a persisted action mutation targets an editable property."""
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
    """Validate one YAML object type against the referenced dataset."""
    object_api_name = required_str(object_def, "apiName")
    _validate_yaml_object_backing(object_def)
    columns = dataset_columns_for_ref(conn, ctx, object_type_backing(object_def)["dataset"])
    property_defs = _property_definitions_by_api(object_def)
    _validate_yaml_property_contracts(object_api_name, property_defs.values())
    _validate_yaml_primary_key(object_def, property_defs, columns)
    _validate_yaml_dataset_properties(object_api_name, property_defs.values(), columns)
    _validate_yaml_action_mutations(definition, object_api_name, property_defs)


def _validate_yaml_object_backing(object_def: YamlObject) -> None:
    backing = object_type_backing(object_def)
    details = {"objectType": required_str(object_def, "apiName")}
    _require_allowed_optional(backing.get("mode"), OBJECT_BACKING_MODES, "object backing mode", details)
    cdc = backing.get("cdc")
    if cdc is not None:
        _require_allowed_optional(cdc.get("deletePolicy"), CDC_DELETE_POLICIES, "cdc delete policy", details)


def _property_definitions_by_api(object_def: YamlObject) -> dict[str, YamlObject]:
    """Return property definitions keyed by API name, rejecting duplicates."""
    property_defs: dict[str, YamlObject] = {}
    for prop in mapping_sequence(object_def, "properties"):
        prop_api = required_str(prop, "apiName")
        if prop_api in property_defs:
            raise ValidationFailed("duplicate property apiName", details={"property": prop_api})
        property_defs[prop_api] = prop
    return property_defs


def _link_definitions_by_api(definition: YamlObject) -> dict[str, YamlObject]:
    link_defs: dict[str, YamlObject] = {}
    for item in mapping_sequence(definition, "linkTypes"):
        api_name = required_str(item, "apiName")
        if api_name in link_defs:
            raise ValidationFailed("duplicate link apiName", details={"linkType": api_name})
        link_defs[api_name] = item
    return link_defs


def _action_definitions_by_api(definition: YamlObject) -> dict[str, YamlObject]:
    action_defs: dict[str, YamlObject] = {}
    for item in mapping_sequence(definition, "actionTypes"):
        api_name = required_str(item, "apiName")
        if api_name in action_defs:
            raise ValidationFailed("duplicate action apiName", details={"actionType": api_name})
        action_defs[api_name] = item
    return action_defs


def _validate_yaml_primary_key(
    object_def: YamlObject,
    property_defs: Mapping[str, YamlObject],
    columns: Mapping[str, Mapping[str, object]],
) -> None:
    """Ensure a YAML object primary key maps to a non-null dataset column."""
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
    """Ensure YAML dataset-backed properties refer to existing columns."""
    for prop in properties:
        source = optional_str(prop, "source", "dataset" if "column" in prop else "edit_layer")
        if source == "dataset" and optional_str(prop, "column") not in columns:
            raise ValidationFailed(
                "property column missing",
                details={"objectType": object_api_name, "property": required_str(prop, "apiName")},
            )


def _validate_yaml_property_contracts(object_api_name: str, properties: Iterable[YamlObject]) -> None:
    for prop in properties:
        prop_api = required_str(prop, "apiName")
        details = {"objectType": object_api_name, "property": prop_api}
        source = optional_str(prop, "source", "dataset" if "column" in prop else "edit_layer")
        edit_default = "edit_only" if source == "edit_layer" else "source_wins"
        prop_type = required_str(prop, "type")
        _require_allowed(prop_type, OBJECT_PROPERTY_DATA_TYPES, "property type", details)
        if prop_type == "media_reference":
            _validate_media_reference_property(prop, source, details)
        _require_allowed_optional(source, PROPERTY_SOURCES, "property source", details)
        _require_allowed_optional(
            optional_str(prop, "editPolicy", edit_default),
            PROPERTY_EDIT_POLICIES,
            "edit policy",
            details,
        )
        _require_allowed_optional(
            optional_str(prop, "classification"),
            PROPERTY_CLASSIFICATIONS,
            "classification",
            details,
        )


def _validate_media_reference_property(prop: YamlObject, source: str | None, details: Mapping[str, object]) -> None:
    """A media_reference property pins an edit-layer media set binding (doc §1.6)."""
    if source == "dataset":
        raise ValidationFailed("media_reference property must be edit-layer, not dataset-backed", details=dict(details))
    media_set = required_str(prop, "mediaSet")
    parts = media_set.split(".")
    if len(parts) != 2 or not all(parts):
        raise ValidationFailed(
            "media_reference property requires a 'mediaSet' of the form 'namespace.name'",
            details={**details, "mediaSet": media_set},
        )


def _validate_yaml_action_mutations(
    definition: YamlObject,
    object_api_name: str,
    property_defs: Mapping[str, YamlObject],
) -> None:
    """Validate YAML action mutations for one target object type."""
    for action_def in mapping_sequence(definition, "actionTypes"):
        if required_str(action_def, "target") != object_api_name:
            continue
        _validate_yaml_action_parameters(action_def)
        for mutation in action_type_definition(action_def).get("mutations", ()):
            _validate_yaml_action_mutation_property(mutation, property_defs)


def _validate_yaml_action_parameters(action_def: YamlObject) -> None:
    action_api = required_str(action_def, "apiName")
    for parameter in mapping_sequence(action_def, "parameters"):
        details = {"actionType": action_api, "parameter": required_str(parameter, "apiName")}
        _require_allowed(required_str(parameter, "type"), PROPERTY_DATA_TYPES, "action parameter type", details)


def _validate_yaml_action_mutation_property(
    mutation: ActionMutationDefinition,
    property_defs: Mapping[str, YamlObject],
) -> None:
    """Ensure a YAML action mutation targets an editable property."""
    _require_allowed(required_str(mutation, "type"), ACTION_MUTATION_TYPES, "action mutation type", dict(mutation))
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
    """Validate a YAML link type against object references and backing keys."""
    from_api = required_str(link_def, "from")
    to_api = required_str(link_def, "to")
    _require_allowed(
        optional_str(link_def, "cardinality", "many_to_one") or "many_to_one",
        LINK_CARDINALITIES,
        "link cardinality",
        {"linkType": required_str(link_def, "apiName")},
    )
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


def _require_allowed(value: str, allowed: frozenset[str], label: str, details: Mapping[str, object]) -> None:
    if value in allowed:
        return
    payload = dict(details)
    payload["value"] = value
    payload["allowed"] = sorted(allowed)
    raise ValidationFailed(f"unsupported {label}", details=payload)


def _require_allowed_optional(
    value: object,
    allowed: frozenset[str],
    label: str,
    details: Mapping[str, object],
) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise ValidationFailed(f"{label} must be a string", details=dict(details))
    _require_allowed(value, allowed, label, details)
