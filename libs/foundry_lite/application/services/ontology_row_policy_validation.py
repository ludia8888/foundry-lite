"""Apply-time validation for per-object-type ``rowPolicies`` declarations.

Row policies gate row visibility at read time, so a dangling property or
unsupported operator must fail activation instead of failing open (or
erroring) on every later query. This lives beside — not inside —
``ontology_validation`` purely to keep that module within the size budget.
"""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.query_filters import validate_filter_ast
from foundry_lite.application.services.ontology_yaml import (
    YamlObject,
    mapping_sequence,
    object_type_row_policies,
    optional_str,
    required_str,
    yaml_object,
)
from foundry_lite.domain.errors import ValidationFailed


def validate_yaml_row_policies(
    object_def: YamlObject,
    property_defs: Mapping[str, YamlObject],
) -> None:
    """Validate optional rowPolicies declarations against declared properties."""
    for policy in object_type_row_policies(object_def):
        details: dict[str, object] = {
            "objectType": required_str(object_def, "apiName"),
            "role": str(policy["role"]),
        }
        policy_filter = yaml_object(policy["filter"], "rowPolicies[].filter")
        validate_filter_ast(policy_filter)
        _validate_row_policy_filter_properties(policy_filter, property_defs, details)
        validate_filter_ast(
            policy_filter,
            property_data_types={name: required_str(prop, "type") for name, prop in property_defs.items()},
        )


def _validate_row_policy_filter_properties(
    filter_ast: YamlObject,
    property_defs: Mapping[str, YamlObject],
    details: Mapping[str, object],
) -> None:
    """Anchor policies to declared, dataset-backed properties.

    Edit-layer properties are rejected because edits would silently move rows
    in and out of a caller's visibility; row security stays anchored to
    dataset-backed values.
    """
    for group_key in ("and", "or"):
        if group_key in filter_ast:
            for item in mapping_sequence(filter_ast, group_key):
                _validate_row_policy_filter_properties(item, property_defs, details)
            return
    property_name = str(filter_ast["property"])
    if property_name not in property_defs:
        raise ValidationFailed(
            "row policy references missing property",
            details={**details, "property": property_name},
        )
    prop = property_defs[property_name]
    source = optional_str(prop, "source", "dataset" if "column" in prop else "edit_layer")
    if source != "dataset":
        raise ValidationFailed(
            "row policy property must be dataset-backed",
            details={**details, "property": property_name},
        )
