"""Multi-datasource source-row resolution and column-wise merge for reindexes.

A multi-datasource object type (MDO) is backed by N datasets; each rebuild
reads every segment's latest committed parquet, groups rows by primary key
per segment, and merges them into ONE row per primary key shaped exactly like
a single-dataset parquet row (column-name keyed). The union of primary keys
defines the object universe: a PK missing from a segment leaves that
segment's property columns null. Downstream machinery (base-patch projection,
edit merge, conflicts, changelog hashes, link indexing, outbox) consumes the
merged rows unchanged.

The run's ``source_dataset_version_id`` becomes a deterministic composite of
each segment's dataset version id in datasource-declaration order, so no-op
detection and run evidence keep working without any schema change.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from foundry_lite.application.ports import (
    DatasetVersionRow,
    ObjectTypeRow,
    PropertyTypeRow,
    TabularRow,
    TransactionContext,
)
from foundry_lite.application.services.object_store.indexing_protocols import (
    IndexDatasetRegistry,
    IndexDatasetVersions,
)
from foundry_lite.application.services.object_store.indexing_types import (
    ObjectIndexMultiSourcePlan,
    ObjectIndexRebuildPlan,
    ObjectIndexSourceSegment,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.object_store.indexing import require_unique_source_object_ids
from foundry_lite.domain.ontology.datasources import (
    backing_datasources,
    composite_source_dataset_version_id,
    is_multi_datasource_backing,
    segment_primary_key_column,
    split_source_dataset_version_id,
)

VersionFilePath = Callable[[DatasetVersionRow], Path]
RowsFromParquet = Callable[[Path], Sequence[TabularRow]]


def read_index_source_rows(
    plan: ObjectIndexRebuildPlan,
    *,
    version_file_path: VersionFilePath,
    rows_from_parquet: RowsFromParquet,
) -> Sequence[TabularRow]:
    """Read the one source snapshot or merge all pinned datasource segments."""
    if plan.multi_source is not None:
        return read_merged_source_rows(
            plan.object_type_api_name,
            plan.multi_source,
            version_file_path=version_file_path,
            rows_from_parquet=rows_from_parquet,
        )
    return rows_from_parquet(version_file_path(plan.dataset_version))


def resolve_multi_source_plan(
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    properties: Sequence[PropertyTypeRow],
    *,
    dataset_registry_service: IndexDatasetRegistry,
    dataset_version_service: IndexDatasetVersions,
) -> ObjectIndexMultiSourcePlan | None:
    """Resolve latest committed versions for every segment, or None for single-dataset types."""
    versions = _latest_segment_versions(
        conn,
        ctx,
        object_type,
        dataset_registry_service=dataset_registry_service,
        dataset_version_service=dataset_version_service,
    )
    if versions is None:
        return None
    return _multi_source_plan(object_type, properties, versions)


def replay_multi_source_plan(
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    properties: Sequence[PropertyTypeRow],
    source_dataset_version_id: str,
    *,
    dataset_version_service: IndexDatasetVersions,
) -> ObjectIndexMultiSourcePlan:
    """Rebind a recorded composite version id to the object type's declared segments.

    The composite joins version ids in datasource-declaration order, so a
    replay re-reads exactly the recorded snapshot per segment. A segment count
    mismatch means the backing changed since the failed run and the replay is
    no longer meaningful.
    """
    version_ids = split_source_dataset_version_id(source_dataset_version_id)
    segments = backing_datasources(object_type["backing"])
    if len(version_ids) != len(segments):
        raise ValidationFailed(
            "index run source no longer matches the object type's datasources",
            details={"objectType": object_type["api_name"], "sourceDatasetVersionId": source_dataset_version_id},
        )
    versions: list[DatasetVersionRow] = []
    for version_id in version_ids:
        version = dataset_version_service._version_by_id(conn, version_id)
        if version["tenant_id"] != ctx.tenant_id:
            raise ValidationFailed("index run source is not replayable", details={"version_id": version_id})
        versions.append(version)
    return _multi_source_plan(object_type, properties, tuple(versions))


def multi_source_version_id(multi_source: ObjectIndexMultiSourcePlan) -> str:
    """Composite source id joining segment version ids in declaration order."""
    return composite_source_dataset_version_id([segment.dataset_version["id"] for segment in multi_source.segments])


def multi_source_version_ids_by_name(multi_source: ObjectIndexMultiSourcePlan) -> dict[str, str]:
    """Per-segment dataset version ids keyed by datasource name (run evidence)."""
    return {segment.name: segment.dataset_version["id"] for segment in multi_source.segments}


def read_merged_source_rows(
    object_type_api_name: str,
    multi_source: ObjectIndexMultiSourcePlan,
    *,
    version_file_path: VersionFilePath,
    rows_from_parquet: RowsFromParquet,
) -> list[TabularRow]:
    """Read every segment's parquet and merge rows by primary key (union of PKs)."""
    segment_rows = [
        _segment_rows_by_pk(
            object_type_api_name, segment, rows_from_parquet(version_file_path(segment.dataset_version))
        )
        for segment in multi_source.segments
    ]
    return _merge_segment_rows(multi_source, segment_rows)


def _merge_segment_rows(
    multi_source: ObjectIndexMultiSourcePlan,
    segment_rows: Sequence[dict[str, TabularRow]],
) -> list[TabularRow]:
    merged: list[TabularRow] = []
    for pk in _union_primary_keys(segment_rows):
        merged.append(_merged_row(multi_source, segment_rows, pk))
    return merged


def _merged_row(
    multi_source: ObjectIndexMultiSourcePlan,
    segment_rows: Sequence[dict[str, TabularRow]],
    pk: str,
) -> TabularRow:
    row: dict[str, object] = {}
    for rows_by_pk in segment_rows:
        segment_row = rows_by_pk.get(pk)
        if segment_row is not None:
            row.update(segment_row)
    # Property-mapped columns always resolve from their OWN segment so a PK
    # missing there yields null even if another dataset shares the column name.
    for segment, rows_by_pk in zip(multi_source.segments, segment_rows, strict=True):
        segment_row = rows_by_pk.get(pk)
        for column in segment.property_columns:
            row[column] = segment_row.get(column) if segment_row is not None else None
    row[multi_source.primary_key_column] = _raw_primary_key(multi_source, segment_rows, pk)
    return row


def _raw_primary_key(
    multi_source: ObjectIndexMultiSourcePlan,
    segment_rows: Sequence[dict[str, TabularRow]],
    pk: str,
) -> object:
    """Keep the first segment's raw PK value so scalar types survive the merge."""
    for segment, rows_by_pk in zip(multi_source.segments, segment_rows, strict=True):
        segment_row = rows_by_pk.get(pk)
        if segment_row is not None:
            return segment_row[segment.primary_key_column]
    raise ValidationFailed("merged primary key vanished from every segment", details={"objectId": pk})


def _union_primary_keys(segment_rows: Sequence[dict[str, TabularRow]]) -> list[str]:
    """Union of PKs in first-seen order across segments (declaration order)."""
    ordered: list[str] = []
    seen: set[str] = set()
    for rows_by_pk in segment_rows:
        for pk in rows_by_pk:
            if pk not in seen:
                seen.add(pk)
                ordered.append(pk)
    return ordered


def _segment_rows_by_pk(
    object_type_api_name: str,
    segment: ObjectIndexSourceSegment,
    rows: Sequence[TabularRow],
) -> dict[str, TabularRow]:
    keys: list[str] = []
    rows_by_pk: dict[str, TabularRow] = {}
    for row in rows:
        pk = row.get(segment.primary_key_column)
        if pk in {None, ""}:
            raise ValidationFailed(
                "object primary key cannot be null",
                details={"objectType": object_type_api_name, "datasource": segment.name},
            )
        keys.append(str(pk))
        rows_by_pk[str(pk)] = row
    require_unique_source_object_ids(
        object_type_api_name,
        keys,
        source_dataset_version_id=segment.dataset_version["id"],
    )
    return rows_by_pk


def _latest_segment_versions(
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    *,
    dataset_registry_service: IndexDatasetRegistry,
    dataset_version_service: IndexDatasetVersions,
) -> tuple[DatasetVersionRow, ...] | None:
    backing = object_type["backing"]
    if not is_multi_datasource_backing(backing):
        return None
    versions: list[DatasetVersionRow] = []
    for segment in backing_datasources(backing):
        dataset = dataset_registry_service.get_dataset(segment.dataset_ref, ctx=ctx)
        versions.append(dataset_version_service._latest_version_by_dataset_id(conn, dataset["id"]))
    return tuple(versions)


def _multi_source_plan(
    object_type: ObjectTypeRow,
    properties: Sequence[PropertyTypeRow],
    versions: Sequence[DatasetVersionRow],
) -> ObjectIndexMultiSourcePlan:
    segments = backing_datasources(object_type["backing"])
    pk_column = _canonical_primary_key_column(object_type, properties)
    property_columns = _property_columns_by_segment(object_type, properties, [segment.name for segment in segments])
    return ObjectIndexMultiSourcePlan(
        primary_key_column=pk_column,
        segments=tuple(
            ObjectIndexSourceSegment(
                name=segment.name,
                dataset_ref=segment.dataset_ref,
                primary_key_column=segment_primary_key_column(segment, pk_column),
                property_columns=property_columns.get(segment.name, ()),
                dataset_version=version,
            )
            for segment, version in zip(segments, versions, strict=True)
        ),
    )


def _canonical_primary_key_column(object_type: ObjectTypeRow, properties: Sequence[PropertyTypeRow]) -> str:
    pk_property = object_type["primary_key_property"]
    for prop in properties:
        if prop["api_name"] == pk_property and prop["column_name"] is not None:
            return prop["column_name"]
    raise ValidationFailed("object primary key column missing", details={"objectType": object_type["api_name"]})


def _property_columns_by_segment(
    object_type: ObjectTypeRow,
    properties: Sequence[PropertyTypeRow],
    segment_names: Sequence[str],
) -> dict[str, tuple[str, ...]]:
    declared = _persisted_property_datasources(object_type)
    default_name = segment_names[0]
    pk_property = object_type["primary_key_property"]
    columns: dict[str, list[str]] = {name: [] for name in segment_names}
    for prop in properties:
        if prop["source"] != "dataset" or prop["column_name"] is None or prop["api_name"] == pk_property:
            continue
        name = declared.get(prop["api_name"], default_name)
        if name in columns:
            columns[name].append(prop["column_name"])
    return {name: tuple(items) for name, items in columns.items()}


def _persisted_property_datasources(object_type: ObjectTypeRow) -> dict[str, str]:
    declared = object_type["config"].get("propertyDatasources")
    if not isinstance(declared, Mapping):
        return {}
    return {str(key): str(value) for key, value in declared.items() if isinstance(value, str)}
