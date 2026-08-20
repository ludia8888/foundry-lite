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
from foundry_lite.application.services.action_definition_validation import validate_yaml_action_definitions
from foundry_lite.application.services.action_ir_compiler import compile_action_definition
from foundry_lite.application.services.materialization_types import (
    MATERIALIZATION_MODES,
    materialization_spec_name,
)
from foundry_lite.application.services.ontology_catalog import build_ontology_catalog as build_ontology_catalog
from foundry_lite.application.services.ontology_datasource_validation import validate_yaml_object_datasources
from foundry_lite.application.services.ontology_migration_types import OntologyMigrationPlan
from foundry_lite.application.services.ontology_row_policy_validation import validate_yaml_row_policies
from foundry_lite.application.services.ontology_yaml import (
    YamlObject,
    action_type_definition,
    link_type_backing,
    mapping_sequence,
    object_type_backing,
    object_type_materialization_config,
    optional_bool,
    optional_str,
    required_str,
)
from foundry_lite.domain.action_runtime.action_contract import compile_action_contract
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

__all__ = [
    "build_ontology_catalog",
    "ontology_validation_result",
    "validate_ontology_definition",
    "validate_persisted_action_mutations",
    "validate_persisted_link",
    "validate_persisted_object_type",
]

DatasetColumnsLookup = Callable[
    [TransactionContext, RequestContext, str],
    Mapping[str, Mapping[str, object]],
]

PROPERTY_DATA_TYPES = frozenset({"boolean", "date", "float", "integer", "string", "timestamp"})
# Object properties may additionally hold an immutable media reference (doc §1.6): the
# Ontology pins a media_item_version via a media_reference property, never a media-backed
# object. Action *parameters* stay scalar — media is bound via an explicit edit, not a param.
OBJECT_PROPERTY_DATA_TYPES = PROPERTY_DATA_TYPES | frozenset({"media_reference", "attachment"})
PROPERTY_SOURCES = frozenset({"dataset", "edit_layer"})
PROPERTY_EDIT_POLICIES = frozenset({"conflict_requires_review", "edit_only", "edit_wins", "source_wins"})
PROPERTY_CLASSIFICATIONS = frozenset({"finance", "pii", "public"})
LINK_CARDINALITIES = frozenset({"many_to_many", "many_to_one", "one_to_many", "one_to_one"})
OBJECT_BACKING_MODES = frozenset({"snapshot"})
# Palantir's derived-property aggregations. `approximateCardinality` counts distinct neighbours;
# the rest need a numeric property on the far side of the link.
DERIVED_AGGREGATIONS = frozenset({"count", "sum", "avg", "min", "max", "approximateCardinality"})
_NUMERIC_DERIVED_AGGREGATIONS = frozenset({"sum", "avg", "min", "max"})
_NUMERIC_PROPERTY_TYPES = frozenset({"integer", "float"})
_COUNTING_DERIVED_AGGREGATIONS = frozenset({"count", "approximateCardinality"})
_DERIVATION_FIELDS = frozenset({"expression", "link", "aggregation", "property"})
_DERIVATION_LINK_FIELDS = frozenset({"link", "aggregation", "property"})
_MANY_FROM_CARDINALITIES = frozenset({"one_to_many", "many_to_many"})
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
    validate_persisted_action_mutations(actions, property_by_api.values())


def validate_persisted_action_mutations(
    actions: Iterable[ActionTypeRow],
    properties: Iterable[PropertyTypeRow],
) -> None:
    """Ensure persisted action mutations target declared, editable properties."""
    property_by_api = {prop["api_name"]: prop for prop in properties}
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
    from_key = link["backing"].get("fromKey")
    to_key = link["backing"].get("toKey")
    if not isinstance(from_key, str) or not isinstance(to_key, str):
        raise ValidationFailed("link backing keys missing", details={"linkType": link["api_name"]})
    missing_keys = [key for key in (from_key, to_key) if key not in columns]
    if missing_keys:
        raise ValidationFailed(
            "link backing key missing",
            details={"linkType": link["api_name"], "missing": missing_keys},
        )


def ontology_validation_result(
    definition: YamlObject,
    migration_plan: OntologyMigrationPlan,
) -> OntologyValidationResult:
    """Build the success payload returned by ontology preview validation.

    The migration plan is reported (never enforced) here so operators can see
    blocked changes, warnings, and reindex work before deciding to apply.
    """
    return {
        "status": "valid",
        "object_type_count": len(mapping_sequence(definition, "objectTypes")),
        "link_type_count": len(mapping_sequence(definition, "linkTypes")),
        "action_type_count": len(mapping_sequence(definition, "actionTypes")),
        "migration_plan": migration_plan.to_payload(),
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
    validate_yaml_action_definitions(definition, object_defs, link_defs)
    _validate_yaml_materializations(object_defs.values())
    for object_def in object_defs.values():
        _validate_yaml_object_type(conn, ctx, definition, object_def, dataset_columns_for_ref)
    for link_def in link_defs.values():
        _validate_yaml_link(conn, ctx, link_def, object_defs, dataset_columns_for_ref)
    # Derived properties are checked last: they are the only property shape whose validity
    # depends on other object types and on the link graph, so every type must exist first.
    for object_def in object_defs.values():
        _validate_derived_properties(object_def, object_defs, link_defs)


def _object_definitions_by_api(definition: YamlObject) -> dict[str, YamlObject]:
    """Return object definitions keyed by API name, rejecting duplicates."""
    object_defs: dict[str, YamlObject] = {}
    seen_api_names: dict[str, str] = {}
    for item in mapping_sequence(definition, "objectTypes"):
        api_name = required_str(item, "apiName")
        _ensure_unique_api_name(
            api_name,
            seen_api_names=seen_api_names,
            message="duplicate object apiName",
            details_key="objectType",
        )
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
    property_defs = _property_definitions_by_api(object_def)
    _validate_yaml_property_contracts(object_api_name, property_defs.values())
    # Primary key and property columns validate per datasource segment (a
    # single-dataset backing is one "primary" segment) so multi-datasource
    # declarations check every dataset they reference.
    validate_yaml_object_datasources(conn, ctx, object_def, property_defs, dataset_columns_for_ref)
    _validate_yaml_title_property(object_def, property_defs)
    validate_yaml_row_policies(object_def, property_defs)
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
    seen_api_names: dict[str, str] = {}
    for prop in mapping_sequence(object_def, "properties"):
        prop_api = required_str(prop, "apiName")
        _ensure_unique_api_name(
            prop_api,
            seen_api_names=seen_api_names,
            message="duplicate property apiName",
            details_key="property",
        )
        property_defs[prop_api] = prop
    return property_defs


def _link_definitions_by_api(definition: YamlObject) -> dict[str, YamlObject]:
    link_defs: dict[str, YamlObject] = {}
    seen_api_names: dict[str, str] = {}
    for item in mapping_sequence(definition, "linkTypes"):
        api_name = required_str(item, "apiName")
        _ensure_unique_api_name(
            api_name,
            seen_api_names=seen_api_names,
            message="duplicate link apiName",
            details_key="linkType",
        )
        link_defs[api_name] = item
    return link_defs


def _action_definitions_by_api(definition: YamlObject) -> dict[str, YamlObject]:
    action_defs: dict[str, YamlObject] = {}
    seen_api_names: dict[str, str] = {}
    for item in mapping_sequence(definition, "actionTypes"):
        api_name = required_str(item, "apiName")
        _ensure_unique_api_name(
            api_name,
            seen_api_names=seen_api_names,
            message="duplicate action apiName",
            details_key="actionType",
        )
        action_defs[api_name] = item
    return action_defs


def _ensure_unique_api_name(
    api_name: str,
    *,
    seen_api_names: dict[str, str],
    message: str,
    details_key: str,
) -> None:
    normalized_api_name = api_name.casefold()
    existing_api_name = seen_api_names.get(normalized_api_name)
    if existing_api_name is None:
        seen_api_names[normalized_api_name] = api_name
        return
    details: dict[str, object] = {details_key: api_name}
    if existing_api_name != api_name:
        details["conflictsWith"] = existing_api_name
    raise ValidationFailed(message, details=details)


def _validate_yaml_title_property(
    object_def: YamlObject,
    property_defs: Mapping[str, YamlObject],
) -> None:
    """Ensure an optional titleProperty names a declared property.

    The title property renders per-record display titles in generated SDKs and
    UIs, so a dangling reference would break every consumer at read time.
    """
    title_property = optional_str(object_def, "titleProperty")
    if title_property is None:
        return
    if title_property not in property_defs:
        raise ValidationFailed(
            "titleProperty must reference a declared property",
            details={
                "objectType": required_str(object_def, "apiName"),
                "titleProperty": title_property,
            },
        )


def _validate_yaml_materializations(object_defs: Iterable[YamlObject]) -> None:
    """Validate optional per-object-type materialization declarations.

    Spec names are derived from the target dataset name segment, so a
    malformed dataset ref or a cross-object duplicate would either be
    unrunnable or silently shadow another spec at run time — both must fail
    at apply time instead.
    """
    seen_spec_names: dict[str, str] = {}
    for object_def in object_defs:
        declared = object_type_materialization_config(object_def)
        if declared is None:
            continue
        object_api_name = required_str(object_def, "apiName")
        details: dict[str, object] = {"objectType": object_api_name}
        dataset_ref = str(declared["dataset"])
        _validate_materialization_dataset_ref(dataset_ref, details)
        _require_allowed_optional(declared.get("mode"), MATERIALIZATION_MODES, "materialization mode", details)
        spec_name = materialization_spec_name(dataset_ref)
        existing = seen_spec_names.get(spec_name)
        if existing is not None:
            raise ValidationFailed(
                "duplicate materialization spec name",
                details={**details, "specName": spec_name, "conflictsWith": existing},
            )
        seen_spec_names[spec_name] = object_api_name


def _validate_materialization_dataset_ref(dataset_ref: str, details: Mapping[str, object]) -> None:
    parts = dataset_ref.split(".")
    if len(parts) != 2 or not all(parts):
        raise ValidationFailed(
            "materialization dataset must be of the form 'namespace.name'",
            details={**details, "dataset": dataset_ref},
        )


def _validate_yaml_property_contracts(object_api_name: str, properties: Iterable[YamlObject]) -> None:
    for prop in properties:
        prop_api = required_str(prop, "apiName")
        details = {"objectType": object_api_name, "property": prop_api}
        source = optional_str(prop, "source", "dataset" if "column" in prop else "edit_layer")
        edit_default = "edit_only" if source == "edit_layer" else "source_wins"
        prop_type = required_str(prop, "type")
        _require_allowed(prop_type, OBJECT_PROPERTY_DATA_TYPES, "property type", details)
        if prop_type in {"media_reference", "attachment"}:
            _validate_media_property(prop, source, details, prop_type)
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


def _validate_derived_properties(
    object_def: YamlObject,
    object_defs: Mapping[str, YamlObject],
    link_defs: Mapping[str, YamlObject],
) -> None:
    """Validate provenance-only and link-derived property declarations."""
    object_api_name = required_str(object_def, "apiName")
    primary_key = required_str(object_def, "primaryKey")
    for prop in mapping_sequence(object_def, "properties"):
        derivation = prop.get("derivation")
        if derivation is None:
            continue
        prop_api = required_str(prop, "apiName")
        details: dict[str, object] = {"objectType": object_api_name, "property": prop_api}
        if not isinstance(derivation, Mapping):
            raise ValidationFailed("property derivation must be a mapping", details=details)
        _validate_derived_property_shape(prop, derivation, details, primary_key)
        if "link" in derivation:
            _validate_link_derived_property(prop, derivation, details, object_defs, link_defs)


def _validate_derived_property_shape(
    prop: YamlObject,
    derivation: Mapping[str, object],
    details: Mapping[str, object],
    primary_key: str,
) -> None:
    unknown = sorted(set(derivation) - _DERIVATION_FIELDS)
    if unknown:
        raise ValidationFailed("property derivation contains unknown fields", details={**details, "fields": unknown})
    has_expression = "expression" in derivation
    has_link_shape = bool(set(derivation) & _DERIVATION_LINK_FIELDS)
    if has_expression and has_link_shape:
        raise ValidationFailed("expression and link derivations must be declared separately", details=dict(details))
    if not has_expression and not has_link_shape:
        raise ValidationFailed("property derivation must declare expression or link", details=dict(details))
    if has_expression:
        required_str(derivation, "expression")
        return
    if "link" not in derivation:
        raise ValidationFailed("link derivation requires a link type", details=dict(details))
    if required_str(prop, "apiName") == primary_key:
        raise ValidationFailed("primary key properties cannot be link-derived", details=dict(details))
    if optional_bool(prop, "editable", False):
        raise ValidationFailed("link-derived properties are read-only", details=dict(details))
    if "column" in prop:
        raise ValidationFailed("link-derived properties cannot declare a backing column", details=dict(details))


def _validate_link_derived_property(
    prop: YamlObject,
    derivation: Mapping[str, object],
    details: Mapping[str, object],
    object_defs: Mapping[str, YamlObject],
    link_defs: Mapping[str, YamlObject],
) -> None:
    link_api = optional_str(derivation, "link")
    if link_api is None:
        raise ValidationFailed("link derivation requires a link type", details=dict(details))
    link_def = link_defs.get(link_api)
    if link_def is None:
        raise ValidationFailed("derived property link type not found", details={**details, "linkType": link_api})
    if required_str(link_def, "from") != details["objectType"]:
        raise ValidationFailed(
            "derived property must follow a link that starts at its own object type",
            details={**details, "linkType": link_api, "from": required_str(link_def, "from")},
        )
    aggregation = optional_str(derivation, "aggregation")
    target_property = optional_str(derivation, "property")
    target_prop = _derived_target_property(link_def, target_property, object_defs, details)
    if aggregation is None:
        _validate_single_link_derivation(prop, link_def, target_property, target_prop, details)
        return
    _validate_link_aggregation(prop, aggregation, target_property, target_prop, details)


def _derived_target_property(
    link_def: YamlObject,
    target_property: str | None,
    object_defs: Mapping[str, YamlObject],
    details: Mapping[str, object],
) -> YamlObject | None:
    if target_property is None:
        return None
    target_api = required_str(link_def, "to")
    target_def = object_defs.get(target_api)
    if target_def is None:
        raise ValidationFailed("derived property link target not found", details={**details, "to": target_api})
    properties = {required_str(item, "apiName"): item for item in mapping_sequence(target_def, "properties")}
    target = properties.get(target_property)
    if target is None:
        raise ValidationFailed(
            "derived property target does not exist on the linked object type",
            details={**details, "to": target_api, "targetProperty": target_property},
        )
    target_derivation = target.get("derivation")
    if isinstance(target_derivation, Mapping) and "link" in target_derivation:
        raise ValidationFailed(
            "derived property cannot target another link-derived property",
            details={**details, "to": target_api, "targetProperty": target_property},
        )
    return target


def _validate_single_link_derivation(
    prop: YamlObject,
    link_def: YamlObject,
    target_property: str | None,
    target_prop: YamlObject | None,
    details: Mapping[str, object],
) -> None:
    cardinality = optional_str(link_def, "cardinality", "many_to_one") or "many_to_one"
    if cardinality in _MANY_FROM_CARDINALITIES:
        raise ValidationFailed("derived property over a many link requires an aggregation", details=dict(details))
    if target_property is None or target_prop is None:
        raise ValidationFailed("derived property requires the linked property it derives", details=dict(details))
    if required_str(prop, "type") != required_str(target_prop, "type"):
        raise ValidationFailed(
            "single-link derived property type must match the linked property",
            details={**details, "targetType": required_str(target_prop, "type")},
        )


def _validate_link_aggregation(
    prop: YamlObject,
    aggregation: str,
    target_property: str | None,
    target_prop: YamlObject | None,
    details: Mapping[str, object],
) -> None:
    _require_allowed(aggregation, DERIVED_AGGREGATIONS, "derived aggregation", details)
    if aggregation == "count":
        if target_property is not None:
            raise ValidationFailed("count derived property must not name a target property", details=dict(details))
    elif target_property is None or target_prop is None:
        raise ValidationFailed(
            "derived property requires the linked property it aggregates",
            details={**details, "aggregation": aggregation},
        )
    if aggregation in _COUNTING_DERIVED_AGGREGATIONS:
        _require_derived_output_type(prop, _NUMERIC_PROPERTY_TYPES - {"float"}, aggregation, details)
    elif aggregation in _NUMERIC_DERIVED_AGGREGATIONS:
        _require_derived_output_type(prop, frozenset({"float"}), aggregation, details)
        if target_prop is not None and required_str(target_prop, "type") not in _NUMERIC_PROPERTY_TYPES:
            raise ValidationFailed(
                "derived aggregation needs a numeric linked property",
                details={**details, "aggregation": aggregation, "targetType": required_str(target_prop, "type")},
            )


def _require_derived_output_type(
    prop: YamlObject,
    allowed_types: frozenset[str],
    aggregation: str,
    details: Mapping[str, object],
) -> None:
    data_type = required_str(prop, "type")
    if data_type not in allowed_types:
        raise ValidationFailed(
            "derived aggregation has an incompatible property type",
            details={**details, "aggregation": aggregation, "type": data_type, "allowed": sorted(allowed_types)},
        )


def _validate_media_property(
    prop: YamlObject,
    source: str | None,
    details: Mapping[str, object],
    property_type: str,
) -> None:
    """Media and attachment properties pin edit-layer immutable versions."""
    if source == "dataset":
        raise ValidationFailed("media properties must be edit-layer, not dataset-backed", details=dict(details))
    media_set = required_str(prop, "mediaSet")
    parts = media_set.split(".")
    if len(parts) != 2 or not all(parts):
        raise ValidationFailed(
            "media property requires a 'mediaSet' of the form 'namespace.name'",
            details={**details, "mediaSet": media_set},
        )
    if property_type == "attachment":
        optional_bool(prop, "allowMultiple", False)


def _validate_yaml_action_mutations(
    definition: YamlObject,
    object_api_name: str,
    property_defs: Mapping[str, YamlObject],
) -> None:
    """Validate YAML action mutations for one target object type."""
    for action_def in mapping_sequence(definition, "actionTypes"):
        if required_str(action_def, "target") != object_api_name:
            continue
        persisted_definition = action_type_definition(action_def)
        compile_action_contract(persisted_definition)
        for mutation in persisted_definition.get("mutations", ()):
            _validate_yaml_action_mutation_property(mutation, property_defs)
        compile_action_definition(persisted_definition)


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
    dataset_ref = backing.get("dataset")
    from_key = backing.get("fromKey")
    to_key = backing.get("toKey")
    if not isinstance(dataset_ref, str) or not isinstance(from_key, str) or not isinstance(to_key, str):
        raise ValidationFailed("link backing is incomplete", details={"linkType": required_str(link_def, "apiName")})
    columns = set(dataset_columns_for_ref(conn, ctx, dataset_ref))
    missing_keys = [key for key in (from_key, to_key) if key not in columns]
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
