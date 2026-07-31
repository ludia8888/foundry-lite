"""Pure migration-planning rules for multi-datasource object backings.

These rules only engage when either the active or the candidate object type
declares explicit ``backing.datasources``; pure single-dataset flows keep the
legacy behavior (a backing change is a reindex warning). Column-wise MDO adds
three consumer-visible hazards that must be classified explicitly:

- removing a datasource silently nulls every property it backed → blocked
- moving a property between datasources changes which dataset feeds its
  values (and which segment role gates them) → blocked
- changing a datasource's dataset reference rebinds every property of that
  segment at once → blocked, mirroring the link-backing rule
- adding a datasource only widens the mapping → warning (reindex required)
- changing a segment's requiredRole re-scopes value visibility → warning
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.domain.ontology.datasources import (
    DatasourceSegment,
    backing_datasources,
    is_multi_datasource_backing,
    property_datasource_name,
)
from foundry_lite.domain.ontology.migration_changes import (
    blocked_datasource_dataset_changed,
    blocked_datasource_removed,
    blocked_property_datasource_moved,
    warning_datasource_added,
    warning_datasource_required_role_changed,
)
from foundry_lite.domain.ontology.migration_types import OntologyMigrationChange

OntologyRow = Mapping[str, object]
OntologyDefinition = Mapping[str, object]


def datasource_migration_changes(
    api_name: str,
    current: OntologyRow,
    current_properties: Sequence[OntologyRow],
    candidate: OntologyDefinition,
    candidate_properties: Mapping[str, OntologyDefinition],
) -> list[OntologyMigrationChange]:
    """Classify datasource shape changes for one object type."""
    current_backing = _backing(current)
    candidate_backing = _candidate_backing(candidate)
    if not is_multi_datasource_backing(current_backing) and not is_multi_datasource_backing(candidate_backing):
        return []
    current_segments = backing_datasources(current_backing)
    candidate_segments = backing_datasources(candidate_backing)
    changes = _segment_shape_changes(api_name, current_backing, current_segments, candidate_segments)
    changes.extend(
        _property_move_changes(
            api_name, current, current_properties, current_segments, candidate_properties, candidate_segments
        )
    )
    return changes


def _segment_shape_changes(
    api_name: str,
    current_backing: Mapping[str, object],
    current_segments: Sequence[DatasourceSegment],
    candidate_segments: Sequence[DatasourceSegment],
) -> list[OntologyMigrationChange]:
    current_by_name = {segment.name: segment for segment in current_segments}
    candidate_by_name = {segment.name: segment for segment in candidate_segments}
    renamed = _implicit_rename_pairs(current_backing, current_segments, candidate_by_name, candidate_segments)
    changes: list[OntologyMigrationChange] = []
    for name in sorted(set(current_by_name) - set(candidate_by_name) - set(renamed)):
        changes.append(blocked_datasource_removed(api_name, name))
    for name in sorted(set(candidate_by_name) - set(current_by_name) - set(renamed.values())):
        changes.append(warning_datasource_added(api_name, name))
    for name in sorted(set(current_by_name) & set(candidate_by_name)):
        changes.extend(_common_segment_changes(api_name, current_by_name[name], candidate_by_name[name]))
    # An implicit single->multi rename keeps the same dataset under a new segment
    # name, so it appears in neither the removed, added, nor common-name sets. Its
    # requiredRole change would be silently dropped without comparing the pair here.
    for current_name, candidate_name in sorted(renamed.items()):
        changes.extend(
            _common_segment_changes(api_name, current_by_name[current_name], candidate_by_name[candidate_name])
        )
    return changes


def _common_segment_changes(
    api_name: str,
    current: DatasourceSegment,
    candidate: DatasourceSegment,
) -> list[OntologyMigrationChange]:
    changes: list[OntologyMigrationChange] = []
    if current.dataset_ref != candidate.dataset_ref:
        changes.append(
            blocked_datasource_dataset_changed(api_name, current.name, current.dataset_ref, candidate.dataset_ref)
        )
    if current.required_role != candidate.required_role:
        changes.append(
            warning_datasource_required_role_changed(
                api_name, current.name, current.required_role, candidate.required_role
            )
        )
    return changes


def _implicit_rename_pairs(
    current_backing: Mapping[str, object],
    current_segments: Sequence[DatasourceSegment],
    candidate_by_name: Mapping[str, DatasourceSegment],
    candidate_segments: Sequence[DatasourceSegment],
) -> dict[str, str]:
    """Pair an implicit single-dataset ``primary`` with its candidate continuation.

    Converting a single-dataset type to explicit datasources keeps the original
    dataset as the (usually first) segment under a new name; that continuation
    must not read as a datasource removal plus addition.
    """
    if is_multi_datasource_backing(current_backing):
        return {}
    primary = current_segments[0]
    if primary.name in candidate_by_name:
        return {}
    for candidate in candidate_segments:
        if candidate.dataset_ref == primary.dataset_ref:
            return {primary.name: candidate.name}
    return {}


def _property_move_changes(
    api_name: str,
    current: OntologyRow,
    current_properties: Sequence[OntologyRow],
    current_segments: Sequence[DatasourceSegment],
    candidate_properties: Mapping[str, OntologyDefinition],
    candidate_segments: Sequence[DatasourceSegment],
) -> list[OntologyMigrationChange]:
    """Block properties whose backing dataset ref moves between datasources."""
    current_refs = _current_property_dataset_refs(current, current_properties, current_segments)
    candidate_refs_by_name = {segment.name: segment.dataset_ref for segment in candidate_segments}
    changes: list[OntologyMigrationChange] = []
    for property_api_name in sorted(set(current_refs) & set(candidate_properties)):
        candidate_name = property_datasource_name(candidate_properties[property_api_name], candidate_segments)
        if candidate_name is None:
            continue  # became edit-layer; the property reindex rules already cover it
        candidate_ref = candidate_refs_by_name.get(candidate_name)
        if candidate_ref is not None and candidate_ref != current_refs[property_api_name]:
            changes.append(
                blocked_property_datasource_moved(
                    api_name, property_api_name, current_refs[property_api_name], candidate_ref
                )
            )
    return changes


def _current_property_dataset_refs(
    current: OntologyRow,
    current_properties: Sequence[OntologyRow],
    current_segments: Sequence[DatasourceSegment],
) -> dict[str, str]:
    refs_by_name = {segment.name: segment.dataset_ref for segment in current_segments}
    declared = _persisted_property_datasources(current)
    refs: dict[str, str] = {}
    for prop in current_properties:
        if prop.get("source") != "dataset":
            continue
        property_api_name = str(prop["api_name"])
        segment_name = declared.get(property_api_name, current_segments[0].name)
        ref = refs_by_name.get(segment_name)
        if ref is not None:
            refs[property_api_name] = ref
    return refs


def _persisted_property_datasources(current: OntologyRow) -> dict[str, str]:
    config = current.get("config")
    if not isinstance(config, Mapping):
        return {}
    declared = cast("Mapping[str, object]", config).get("propertyDatasources")
    if not isinstance(declared, Mapping):
        return {}
    items = cast("Mapping[str, object]", declared)
    return {str(key): str(value) for key, value in items.items() if isinstance(value, str)}


def _backing(row: OntologyRow) -> Mapping[str, object]:
    value = row.get("backing")
    if not isinstance(value, Mapping):
        return {}
    return cast("Mapping[str, object]", value)


def _candidate_backing(candidate: OntologyDefinition) -> Mapping[str, object]:
    value = candidate.get("backing")
    if not isinstance(value, Mapping):
        return {}
    return cast("Mapping[str, object]", value)
