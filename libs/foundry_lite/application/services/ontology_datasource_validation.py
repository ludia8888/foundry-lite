"""Apply-time validation of object-type datasource backings (single and multi).

Column-wise multi-datasource object types (MDO) validate per segment: every
declared datasource must expose the primary key column (non-null), every
dataset-backed property's column must exist in ITS datasource's dataset, and
each property maps to exactly one declared datasource. Single-dataset types
route through the same checks against their one normalized ``primary``
segment, so error messages stay identical to the legacy validation. This
lives beside — not inside — ``ontology_validation`` to keep that module
within the size budget.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.ontology_repository import (
    ObjectTypeBacking,
    ObjectTypeRow,
    PropertyTypeRow,
)
from foundry_lite.application.services.ontology_row_policy_validation import validate_yaml_row_policies
from foundry_lite.application.services.ontology_yaml import (
    YamlObject,
    mapping_sequence,
    object_type_backing,
    optional_str,
    required_str,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.ontology.datasources import (
    DatasourceSegment,
    backing_datasources,
    is_multi_datasource_backing,
    property_datasource_name,
    segment_primary_key_column,
)

DatasetColumnsLookup = Callable[
    [TransactionContext, RequestContext, str],
    Mapping[str, Mapping[str, object]],
]


def yaml_property_datasources(item: YamlObject) -> dict[str, str] | None:
    """Resolved property→datasource map for one YAML object type (None for single-dataset).

    Persisting the RESOLVED map (defaults applied) in the object's config JSON
    means read paths, migration planning, and rollback replay never depend on
    which ``datasource`` keys the YAML author omitted.
    """
    backing = object_type_backing(item)
    if not is_multi_datasource_backing(backing):
        return None
    property_defs = {required_str(prop, "apiName"): prop for prop in mapping_sequence(item, "properties")}
    return resolved_property_datasources(backing, property_defs)


def resolved_property_datasources(
    backing: ObjectTypeBacking,
    property_defs: Mapping[str, YamlObject],
) -> dict[str, str]:
    """Resolve every dataset-backed property to its datasource name.

    The resolved map is persisted in the object type's config JSON so read
    paths, migration planning, and rollback replay never re-derive defaults
    from YAML omissions.
    """
    segments = backing_datasources(backing)
    resolved: dict[str, str] = {}
    for property_api_name, prop in property_defs.items():
        name = property_datasource_name(prop, segments)
        if name is not None:
            resolved[property_api_name] = name
    return resolved


def validate_yaml_object_datasources(
    conn: TransactionContext,
    ctx: RequestContext,
    object_def: YamlObject,
    property_defs: Mapping[str, YamlObject],
    dataset_columns_for_ref: DatasetColumnsLookup,
) -> None:
    """Validate one YAML object type's datasource declarations and columns."""
    object_api_name = required_str(object_def, "apiName")
    backing = object_type_backing(object_def)
    segments = backing_datasources(backing)
    _require_unique_datasource_names(object_api_name, segments)
    assignments = _property_segment_assignments(object_api_name, backing, segments, property_defs)
    pk_column = _yaml_primary_key_column(object_def, property_defs)
    for segment in segments:
        columns = dataset_columns_for_ref(conn, ctx, segment.dataset_ref)
        _validate_segment_primary_key(backing, segment, pk_column, columns)
        _validate_segment_property_columns(object_api_name, segment, assignments, columns)


def validate_yaml_object_data_contracts(
    conn: TransactionContext,
    ctx: RequestContext,
    object_def: YamlObject,
    property_defs: Mapping[str, YamlObject],
    dataset_columns_for_ref: DatasetColumnsLookup,
) -> None:
    """Validate datasource mappings and row policies against one property catalog."""
    validate_yaml_object_datasources(conn, ctx, object_def, property_defs, dataset_columns_for_ref)
    validate_yaml_row_policies(object_def, property_defs)


def validate_persisted_object_datasources(
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    properties: Iterable[PropertyTypeRow],
    dataset_columns_for_ref: DatasetColumnsLookup,
) -> None:
    """Validate a persisted multi-datasource object type against dataset schemas."""
    backing = object_type["backing"]
    segments = backing_datasources(backing)
    declared = _persisted_property_datasources(object_type)
    property_by_api = {prop["api_name"]: prop for prop in properties}
    pk_column = _persisted_primary_key_column(object_type, property_by_api)
    for segment in segments:
        columns = dataset_columns_for_ref(conn, ctx, segment.dataset_ref)
        _validate_segment_primary_key(backing, segment, pk_column, columns)
        _validate_persisted_segment_properties(object_type, segment, declared, property_by_api, columns, segments)


def _require_unique_datasource_names(object_api_name: str, segments: Sequence[DatasourceSegment]) -> None:
    seen: set[str] = set()
    for segment in segments:
        if segment.name in seen:
            raise ValidationFailed(
                "duplicate datasource name",
                details={"objectType": object_api_name, "datasource": segment.name},
            )
        seen.add(segment.name)


def _property_segment_assignments(
    object_api_name: str,
    backing: ObjectTypeBacking,
    segments: Sequence[DatasourceSegment],
    property_defs: Mapping[str, YamlObject],
) -> dict[str, list[tuple[str, str]]]:
    """Return (property, column) pairs grouped by datasource name."""
    known = {segment.name for segment in segments}
    assignments: dict[str, list[tuple[str, str]]] = {segment.name: [] for segment in segments}
    for property_api_name, prop in property_defs.items():
        _require_dataset_backed_datasource(object_api_name, property_api_name, prop)
        name = property_datasource_name(prop, segments)
        if name is None:
            continue
        if name not in known:
            raise ValidationFailed(
                "property references unknown datasource",
                details={"objectType": object_api_name, "property": property_api_name, "datasource": name},
            )
        column = optional_str(prop, "column")
        if column is None:
            raise ValidationFailed(
                "property column missing",
                details={"objectType": object_api_name, "property": property_api_name},
            )
        assignments[name].append((property_api_name, column))
    return assignments


def _require_dataset_backed_datasource(object_api_name: str, property_api_name: str, prop: YamlObject) -> None:
    """An explicit datasource on a non-dataset property is a dangling declaration."""
    if "datasource" not in prop or prop["datasource"] is None:
        return
    source = optional_str(prop, "source", "dataset" if "column" in prop else "edit_layer")
    if source != "dataset":
        raise ValidationFailed(
            "datasource requires a dataset-backed property",
            details={"objectType": object_api_name, "property": property_api_name},
        )


def _yaml_primary_key_column(object_def: YamlObject, property_defs: Mapping[str, YamlObject]) -> str:
    """Resolve the object's canonical primary key column from YAML properties."""
    pk_prop = required_str(object_def, "primaryKey")
    if pk_prop not in property_defs:
        raise ValidationFailed(
            "primary key property missing",
            details={"objectType": required_str(object_def, "apiName")},
        )
    pk_column = optional_str(property_defs[pk_prop], "column")
    if pk_column is None:
        raise ValidationFailed("primary key column missing", details={"column": pk_column})
    return pk_column


def _persisted_primary_key_column(
    object_type: ObjectTypeRow,
    property_by_api: Mapping[str, PropertyTypeRow],
) -> str:
    pk_prop = object_type["primary_key_property"]
    if pk_prop not in property_by_api:
        raise ValidationFailed("primary key property missing", details={"objectType": object_type["api_name"]})
    pk_column = property_by_api[pk_prop]["column_name"]
    if pk_column is None:
        raise ValidationFailed("primary key column missing", details={"column": pk_column})
    return pk_column


def _validate_segment_primary_key(
    backing: ObjectTypeBacking,
    segment: DatasourceSegment,
    pk_column: str,
    columns: Mapping[str, Mapping[str, object]],
) -> None:
    """Palantir rule: the primary key column must exist (non-null) in EVERY datasource."""
    segment_pk = segment_primary_key_column(segment, pk_column)
    details = _segment_details({"column": segment_pk}, backing, segment)
    if segment_pk not in columns:
        raise ValidationFailed("primary key column missing", details=details)
    if bool(columns[segment_pk].get("nullable")):
        raise ValidationFailed("primary key column must be non-null", details=details)


def _validate_segment_property_columns(
    object_api_name: str,
    segment: DatasourceSegment,
    assignments: Mapping[str, Sequence[tuple[str, str]]],
    columns: Mapping[str, Mapping[str, object]],
) -> None:
    for property_api_name, column in assignments.get(segment.name, ()):
        if column not in columns:
            raise ValidationFailed(
                "property column missing",
                details={"objectType": object_api_name, "property": property_api_name},
            )


def _validate_persisted_segment_properties(
    object_type: ObjectTypeRow,
    segment: DatasourceSegment,
    declared: Mapping[str, str],
    property_by_api: Mapping[str, PropertyTypeRow],
    columns: Mapping[str, Mapping[str, object]],
    segments: Sequence[DatasourceSegment],
) -> None:
    default_name = segments[0].name
    for prop in property_by_api.values():
        if prop["source"] != "dataset":
            continue
        if declared.get(prop["api_name"], default_name) != segment.name:
            continue
        if prop["column_name"] not in columns:
            raise ValidationFailed(
                "property column missing",
                details={"objectType": object_type["api_name"], "property": prop["api_name"]},
            )


def _persisted_property_datasources(object_type: ObjectTypeRow) -> dict[str, str]:
    declared = object_type["config"].get("propertyDatasources")
    if not isinstance(declared, Mapping):
        return {}
    return {str(key): str(value) for key, value in declared.items() if isinstance(value, str)}


def _segment_details(
    details: dict[str, object],
    backing: ObjectTypeBacking,
    segment: DatasourceSegment,
) -> dict[str, object]:
    """Add the datasource to error details only for explicit multi-datasource backings.

    Single-dataset error payloads must stay byte-identical to the legacy
    validation so existing consumers (and tests) keep matching.
    """
    if is_multi_datasource_backing(backing):
        return {**details, "datasource": segment.name}
    return details
