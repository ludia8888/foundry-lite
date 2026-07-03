"""Pure column-wise multi-datasource (MDO) backing rules.

Palantir semantics implemented here: one object type may be backed by N
datasets (datasources); every NON-PK dataset property maps to exactly ONE
datasource, the primary key column(s) exist in EVERY datasource, and the
object universe is the UNION of primary keys across datasources. A legacy
single-dataset ``backing: {dataset: ...}`` normalizes to one datasource named
``primary`` so every consumer reasons over the same segment shape.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from foundry_lite.domain.errors import ValidationFailed

PRIMARY_DATASOURCE_NAME = "primary"
#: Joins per-segment dataset version ids into one deterministic source id so
#: no-op detection and run evidence keep working without any schema change.
COMPOSITE_VERSION_SEPARATOR = "+"


@dataclass(frozen=True)
class DatasourceSegment:
    """One normalized datasource segment of an object type backing."""

    name: str
    dataset_ref: str
    primary_key_columns: tuple[str, ...]
    required_role: str | None


def is_multi_datasource_backing(backing: Mapping[str, object]) -> bool:
    """Return whether the backing declares explicit ``datasources``."""
    value = backing.get("datasources")
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return False
    return len(cast("Sequence[object]", value)) > 0


def backing_datasources(backing: Mapping[str, object]) -> tuple[DatasourceSegment, ...]:
    """Normalize a persisted/YAML backing into ordered datasource segments."""
    if not is_multi_datasource_backing(backing):
        return (_single_dataset_segment(backing),)
    segments: list[DatasourceSegment] = []
    for item in cast(Sequence[object], backing["datasources"]):
        segments.append(_declared_segment(item))
    return tuple(segments)


def primary_dataset_ref(backing: Mapping[str, object]) -> str:
    """Return the first (primary) segment's dataset reference."""
    return backing_datasources(backing)[0].dataset_ref


def property_datasource_name(
    prop: Mapping[str, object],
    segments: Sequence[DatasourceSegment],
    *,
    datasource_key: str = "datasource",
) -> str | None:
    """Resolve one property declaration's datasource name (None for edit layer).

    A missing ``datasource`` defaults to the FIRST declared datasource. A
    non-string value is rejected: mapping one property to several datasources
    would be row-wise multi-datasource, which is not supported.
    """
    if not _is_dataset_property(prop):
        return None
    declared = prop.get(datasource_key)
    if declared is None:
        return segments[0].name
    if not isinstance(declared, str) or not declared:
        raise ValidationFailed(
            "property datasource must be a single datasource name (row-wise multi-datasource is not supported)",
            details={"property": str(prop.get("apiName", "")), "datasource": str(declared)},
        )
    return declared


def datasource_required_roles(segments: Sequence[DatasourceSegment]) -> dict[str, str]:
    """Return required access role by datasource name (segments without a role omitted)."""
    return {segment.name: segment.required_role for segment in segments if segment.required_role is not None}


def segment_masked_property_names(
    property_datasources: Mapping[str, str],
    roles_by_datasource: Mapping[str, str],
    caller_roles: Sequence[str],
) -> set[str]:
    """Properties whose datasource segment the caller may not view (admin implicit)."""
    if "admin" in caller_roles:
        return set()
    held = set(caller_roles)
    return {
        property_api_name
        for property_api_name, datasource_name in property_datasources.items()
        if datasource_name in roles_by_datasource and roles_by_datasource[datasource_name] not in held
    }


def composite_source_dataset_version_id(version_ids: Sequence[str]) -> str:
    """Join per-segment dataset version ids in datasource-declaration order."""
    if not version_ids:
        raise ValidationFailed("at least one dataset version id is required")
    return COMPOSITE_VERSION_SEPARATOR.join(version_ids)


def split_source_dataset_version_id(value: str) -> tuple[str, ...]:
    """Split a stored source version id back into per-segment version ids."""
    return tuple(part for part in value.split(COMPOSITE_VERSION_SEPARATOR) if part)


def segment_primary_key_column(segment: DatasourceSegment, default_column: str) -> str:
    """Return the segment's primary key column, defaulting to the object's PK column."""
    if segment.primary_key_columns:
        return segment.primary_key_columns[0]
    return default_column


def property_datasource_rows(
    rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Derive per-property segment access-role rows for the security policy.

    Each input row carries one active property joined with its object type:
    ``object_type_api_name``, ``property_api_name``, ``source``, ``column_name``,
    ``backing`` and ``config``. The output mirrors the classification-provider
    row shape, adding ``segment_required_role`` so the policy can mask segment
    values without parsing backing JSON itself.
    """
    result: list[dict[str, object]] = []
    for row in rows:
        role = _segment_required_role_for_row(row)
        if role is None:
            continue
        result.append(
            {
                "object_type_api_name": row.get("object_type_api_name"),
                "property_api_name": row.get("property_api_name"),
                "column_name": row.get("column_name"),
                "classification": None,
                "segment_required_role": role,
            }
        )
    return result


def _segment_required_role_for_row(row: Mapping[str, object]) -> str | None:
    if row.get("source") != "dataset":
        return None
    raw_backing = row.get("backing")
    if not isinstance(raw_backing, Mapping):
        return None
    backing = cast("Mapping[str, object]", raw_backing)
    if not is_multi_datasource_backing(backing):
        return None
    segments = backing_datasources(backing)
    roles = datasource_required_roles(segments)
    if not roles:
        return None
    datasource_name = _persisted_property_datasource(row, segments)
    return roles.get(datasource_name)


def _persisted_property_datasource(row: Mapping[str, object], segments: Sequence[DatasourceSegment]) -> str:
    config = row.get("config")
    property_api_name = str(row.get("property_api_name", ""))
    if isinstance(config, Mapping):
        declared = cast(Mapping[str, object], config).get("propertyDatasources")
        if isinstance(declared, Mapping):
            value = cast(Mapping[str, object], declared).get(property_api_name)
            if isinstance(value, str) and value:
                return value
    return segments[0].name


def _is_dataset_property(prop: Mapping[str, object]) -> bool:
    source = prop.get("source")
    if source is not None:
        return source == "dataset"
    return "column" in prop


def _single_dataset_segment(backing: Mapping[str, object]) -> DatasourceSegment:
    dataset = backing.get("dataset")
    if not isinstance(dataset, str) or not dataset:
        raise ValidationFailed("object backing must declare a dataset or datasources")
    return DatasourceSegment(
        name=PRIMARY_DATASOURCE_NAME,
        dataset_ref=dataset,
        primary_key_columns=_string_tuple(backing.get("primaryKeyColumns"), "primaryKeyColumns"),
        required_role=None,
    )


def _declared_segment(item: object) -> DatasourceSegment:
    if not isinstance(item, Mapping):
        raise ValidationFailed("backing datasources entries must be mappings")
    entry = cast(Mapping[str, object], item)
    name = entry.get("name")
    dataset = entry.get("dataset")
    if not isinstance(name, str) or not name:
        raise ValidationFailed("datasource name must be a non-empty string")
    if not isinstance(dataset, str) or not dataset:
        raise ValidationFailed("datasource dataset must be a non-empty string", details={"datasource": name})
    required_role = entry.get("requiredRole")
    if required_role is not None and (not isinstance(required_role, str) or not required_role):
        raise ValidationFailed("datasource requiredRole must be a non-empty string", details={"datasource": name})
    return DatasourceSegment(
        name=name,
        dataset_ref=dataset,
        primary_key_columns=_string_tuple(entry.get("primaryKeyColumns"), "primaryKeyColumns"),
        required_role=required_role,
    )


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValidationFailed(f"{field} must be a list of strings")
    items: list[str] = []
    for item in cast(Sequence[object], value):
        if not isinstance(item, str) or not item:
            raise ValidationFailed(f"{field} entries must be non-empty strings")
        items.append(item)
    return tuple(items)
