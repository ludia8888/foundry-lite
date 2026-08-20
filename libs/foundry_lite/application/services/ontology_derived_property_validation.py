"""Validation for link-derived Ontology properties."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.services.ontology_yaml import (
    YamlObject,
    mapping_sequence,
    optional_bool,
    optional_str,
    required_str,
)
from foundry_lite.domain.errors import ValidationFailed

DERIVED_AGGREGATIONS = frozenset({"count", "sum", "avg", "min", "max", "approximateCardinality"})
NUMERIC_DERIVED_AGGREGATIONS = frozenset({"sum", "avg", "min", "max"})
NUMERIC_PROPERTY_TYPES = frozenset({"integer", "float"})
COUNTING_DERIVED_AGGREGATIONS = frozenset({"count", "approximateCardinality"})
DERIVATION_FIELDS = frozenset({"expression", "link", "aggregation", "property"})
DERIVATION_LINK_FIELDS = frozenset({"link", "aggregation", "property"})
MANY_FROM_CARDINALITIES = frozenset({"one_to_many", "many_to_many"})


def validate_derived_properties(
    object_def: YamlObject,
    object_defs: Mapping[str, YamlObject],
    link_defs: Mapping[str, YamlObject],
) -> None:
    """Validate provenance-only and read-time link-derived property declarations."""
    object_api_name = required_str(object_def, "apiName")
    primary_key = required_str(object_def, "primaryKey")
    for prop in mapping_sequence(object_def, "properties"):
        derivation = prop.get("derivation")
        if derivation is None:
            continue
        details: dict[str, object] = {"objectType": object_api_name, "property": required_str(prop, "apiName")}
        if not isinstance(derivation, Mapping):
            raise ValidationFailed("property derivation must be a mapping", details=details)
        _validate_derived_property(prop, derivation, details, primary_key, object_defs, link_defs)


def _validate_derived_property(
    prop: YamlObject,
    derivation: Mapping[str, object],
    details: Mapping[str, object],
    primary_key: str,
    object_defs: Mapping[str, YamlObject],
    link_defs: Mapping[str, YamlObject],
) -> None:
    if _derivation_kind(derivation, details) == "expression":
        return
    _validate_link_shape(prop, details, primary_key)
    _validate_link_derived_property(prop, derivation, details, object_defs, link_defs)


def _derivation_kind(derivation: Mapping[str, object], details: Mapping[str, object]) -> str:
    unknown = sorted(set(derivation) - DERIVATION_FIELDS)
    if unknown:
        raise ValidationFailed("property derivation contains unknown fields", details={**details, "fields": unknown})
    if set(derivation) == {"expression"}:
        required_str(derivation, "expression")
        return "expression"
    if "expression" in derivation:
        raise ValidationFailed("expression and link derivations must be declared separately", details=dict(details))
    if not set(derivation) & DERIVATION_LINK_FIELDS or "link" not in derivation:
        raise ValidationFailed("link derivation requires a link type", details=dict(details))
    return "link"


def _validate_link_shape(prop: YamlObject, details: Mapping[str, object], primary_key: str) -> None:
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
    _require_link_from_current_object(link_def, link_api, details)
    aggregation = optional_str(derivation, "aggregation")
    target_property = optional_str(derivation, "property")
    target_prop = _derived_target_property(link_def, target_property, object_defs, details)
    if aggregation is None:
        _validate_single_link_derivation(prop, link_def, target_property, target_prop, details)
        return
    _validate_link_aggregation(prop, aggregation, target_property, target_prop, details)


def _require_link_from_current_object(
    link_def: YamlObject,
    link_api: str,
    details: Mapping[str, object],
) -> None:
    source_type = required_str(link_def, "from")
    if source_type != details["objectType"]:
        raise ValidationFailed(
            "derived property must follow a link that starts at its own object type",
            details={**details, "linkType": link_api, "from": source_type},
        )


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
    target = _properties_by_api(target_def).get(target_property)
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


def _properties_by_api(object_def: YamlObject) -> dict[str, YamlObject]:
    return {required_str(item, "apiName"): item for item in mapping_sequence(object_def, "properties")}


def _validate_single_link_derivation(
    prop: YamlObject,
    link_def: YamlObject,
    target_property: str | None,
    target_prop: YamlObject | None,
    details: Mapping[str, object],
) -> None:
    cardinality = optional_str(link_def, "cardinality", "many_to_one") or "many_to_one"
    if cardinality in MANY_FROM_CARDINALITIES:
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
    _require_aggregation_target(aggregation, target_property, target_prop, details)
    if aggregation in COUNTING_DERIVED_AGGREGATIONS:
        _require_derived_output_type(prop, NUMERIC_PROPERTY_TYPES - {"float"}, aggregation, details)
        return
    if aggregation in NUMERIC_DERIVED_AGGREGATIONS:
        _require_derived_output_type(prop, frozenset({"float"}), aggregation, details)
        _require_numeric_target(target_prop, aggregation, details)


def _require_aggregation_target(
    aggregation: str,
    target_property: str | None,
    target_prop: YamlObject | None,
    details: Mapping[str, object],
) -> None:
    if aggregation == "count" and target_property is not None:
        raise ValidationFailed("count derived property must not name a target property", details=dict(details))
    if aggregation != "count" and (target_property is None or target_prop is None):
        raise ValidationFailed(
            "derived property requires the linked property it aggregates",
            details={**details, "aggregation": aggregation},
        )


def _require_numeric_target(
    target_prop: YamlObject | None,
    aggregation: str,
    details: Mapping[str, object],
) -> None:
    if target_prop is not None and required_str(target_prop, "type") not in NUMERIC_PROPERTY_TYPES:
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


def _require_allowed(value: str, allowed: frozenset[str], label: str, details: Mapping[str, object]) -> None:
    if value not in allowed:
        raise ValidationFailed(
            f"unsupported {label}",
            details={**details, "value": value, "allowed": sorted(allowed)},
        )
