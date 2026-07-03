"""Changelog-hash refresh planning for dataset-backed object reindexes.

A dataset reindex historically re-read every row of the latest parquet version
and compared each one against the database. This module turns the stored
per-primary-key row hashes into an in-memory diff so a refresh only touches
changed, added, or removed keys, while every unsafe flow (no stored baseline,
shadow rebuild, ontology-migration reindex, failed-run replay, CDC-backed
types) deterministically falls back to today's full pass. Full passes always
reseed the stored map inside the same transaction, so the next plain reindex
can diff against exactly what that rebuild (or shadow switch) left indexed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from foundry_lite.application.ports import (
    IndexRunSourceRef,
    ObjectIndexRowHashRepository,
    ObjectPropertyMap,
    ObjectTypeRow,
    PropertyTypeRow,
    TabularRow,
    TransactionContext,
)
from foundry_lite.application.primitives import _json_hash, _now
from foundry_lite.application.services.object_store.indexing_protocols import IndexOntologyLookup
from foundry_lite.application.services.object_store.indexing_types import (
    ObjectIndexRebuildCounts,
    ObjectIndexRebuildPlan,
    ObjectIndexSourceRow,
)
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.object_store.indexing import FULL_INDEX_MODE

CHANGELOG_INCREMENTAL_REFRESH_MODE = "changelog_incremental"
FULL_REFRESH_MODE = "full"
_CHANGELOG_TRIGGER_TYPE = "reindex"


@dataclass(frozen=True)
class ChangelogRefreshPlan:
    """How one rebuild resolves its source snapshot against the stored hashes."""

    refresh_mode: str
    rows_to_index: tuple[ObjectIndexSourceRow, ...]
    removed_object_ids: tuple[str, ...]
    skipped_count: int
    upserted_row_hashes: dict[str, str]
    row_hashes: dict[str, str]

    @property
    def is_incremental(self) -> bool:
        return self.refresh_mode == CHANGELOG_INCREMENTAL_REFRESH_MODE


def source_row_hash(source: ObjectIndexSourceRow) -> str:
    """Hash one source row exactly like the record's persisted ``source_hash``.

    Reusing ``_json_hash`` over the base patch means hashes seeded from a fresh
    parquet read agree with the ``source_hash`` already stored on legacy object
    records, so "hash unchanged" is exactly "base layer unchanged".
    """
    return _json_hash(source.base_patch)


def changelog_diff_supported(plan: ObjectIndexRebuildPlan) -> bool:
    """Return whether this rebuild may skip rows via the stored hash map.

    Shadow rebuilds and ontology-migration reindexes rewrite index shape (new
    index version, new property projection), and failed-run replays may target
    an older dataset version whose bookkeeping must be fully reapplied - all of
    them must reprocess every row. CDC-backed types mutate the base layer
    outside the snapshot path, so their stored hashes cannot be trusted either.
    """
    if plan.mode != FULL_INDEX_MODE or plan.trigger_type != _CHANGELOG_TRIGGER_TYPE:
        return False
    return plan.object_type["backing"].get("cdc") is None


def plan_changelog_refresh(
    plan: ObjectIndexRebuildPlan,
    stored_hashes: Mapping[str, str],
    source_rows: Sequence[ObjectIndexSourceRow],
) -> ChangelogRefreshPlan:
    """Diff the new snapshot against stored hashes, or demand a full pass.

    An empty stored map means there is no baseline to trust (first refresh
    after the changelog landed), so the plan degrades to a full rebuild that
    seeds the map for the next run.
    """
    row_hashes = {source.object_id: source_row_hash(source) for source in source_rows}
    if not changelog_diff_supported(plan) or not stored_hashes:
        return _full_refresh_plan(source_rows, row_hashes)
    changed = tuple(
        source for source in source_rows if stored_hashes.get(source.object_id) != row_hashes[source.object_id]
    )
    removed = tuple(sorted(set(stored_hashes) - set(row_hashes)))
    return ChangelogRefreshPlan(
        refresh_mode=CHANGELOG_INCREMENTAL_REFRESH_MODE,
        rows_to_index=changed,
        removed_object_ids=removed,
        skipped_count=len(source_rows) - len(changed),
        upserted_row_hashes={source.object_id: row_hashes[source.object_id] for source in changed},
        row_hashes=row_hashes,
    )


def load_stored_row_hashes(
    conn: TransactionContext,
    repository: ObjectIndexRowHashRepository,
    tenant_id: str,
    plan: ObjectIndexRebuildPlan,
) -> dict[str, str]:
    """Load the diff baseline, or nothing when this plan must run a full pass.

    Full-pass plans never consult the baseline, so skipping the read avoids
    loading every stored key just to ignore it.
    """
    if not changelog_diff_supported(plan):
        return {}
    return repository.load_row_hashes(
        transaction=conn,
        tenant_id=tenant_id,
        object_type_id=plan.object_type["id"],
    )


def persist_row_hashes(
    conn: TransactionContext,
    repository: ObjectIndexRowHashRepository,
    tenant_id: str,
    plan: ObjectIndexRebuildPlan,
    refresh: ChangelogRefreshPlan,
) -> None:
    """Store the refreshed baseline inside the rebuild's own transaction.

    Sharing the transaction with the index writes (and, for shadow runs, the
    alias switch) means hashes and indexed rows commit or roll back together,
    so the next incremental diff always sees a truthful baseline.
    """
    if refresh.is_incremental:
        repository.apply_row_hash_changes(
            transaction=conn,
            tenant_id=tenant_id,
            object_type_id=plan.object_type["id"],
            dataset_version_id=plan.source_dataset_version_id,
            upserted=refresh.upserted_row_hashes,
            removed_object_ids=refresh.removed_object_ids,
            updated_at=_now(),
        )
        return
    repository.replace_row_hashes(
        transaction=conn,
        tenant_id=tenant_id,
        object_type_id=plan.object_type["id"],
        dataset_version_id=plan.source_dataset_version_id,
        row_hashes=refresh.row_hashes,
        updated_at=_now(),
    )


def changelog_source_ref_updates(counts: ObjectIndexRebuildCounts) -> IndexRunSourceRef:
    """Expose the refresh mode and changed/deleted/skipped counts to operators.

    The index run's ``source_ref`` is the operator-facing evidence surface for
    "why did this refresh touch so few (or so many) rows", so the resolved mode
    and per-run counts are merged there when the run succeeds.
    """
    return {
        "mode": counts.refresh_mode,
        "changed": counts.objects_upserted,
        "deleted": counts.objects_deleted,
        "skipped": counts.rows_skipped,
    }


def source_rows_from_dataset_rows(
    conn: TransactionContext,
    ontology_service: IndexOntologyLookup,
    object_type: ObjectTypeRow,
    rows: Sequence[TabularRow],
) -> list[ObjectIndexSourceRow]:
    """Project dataset rows into (primary key, base patch) source rows.

    The projection is resolved from the active ontology on every rebuild so a
    property mapping change alters the base patch - and therefore the row hash -
    forcing the changed rows through the regular upsert path.
    """
    properties = ontology_service._properties_for_object_type(conn, object_type["id"])
    pk_column = _primary_key_column(object_type, properties)
    return [_source_row_from_dataset_row(row, pk_column, properties) for row in rows]


def _full_refresh_plan(
    source_rows: Sequence[ObjectIndexSourceRow],
    row_hashes: dict[str, str],
) -> ChangelogRefreshPlan:
    return ChangelogRefreshPlan(
        refresh_mode=FULL_REFRESH_MODE,
        rows_to_index=tuple(source_rows),
        removed_object_ids=(),
        skipped_count=0,
        upserted_row_hashes=dict(row_hashes),
        row_hashes=row_hashes,
    )


def _primary_key_column(object_type: ObjectTypeRow, properties: Sequence[PropertyTypeRow]) -> str:
    pk_prop = next(prop for prop in properties if prop["api_name"] == object_type["primary_key_property"])
    pk_column = pk_prop["column_name"]
    if pk_column is None:
        raise ValidationFailed("object primary key column missing")
    return pk_column


def _source_row_from_dataset_row(
    row: TabularRow,
    pk_column: str,
    properties: Sequence[PropertyTypeRow],
) -> ObjectIndexSourceRow:
    object_id = row.get(pk_column)
    if object_id in {None, ""}:
        raise ValidationFailed("object primary key cannot be null")
    return ObjectIndexSourceRow(object_id=str(object_id), base_patch=_base_patch_from_dataset_row(row, properties))


def _base_patch_from_dataset_row(
    row: TabularRow,
    properties: Sequence[PropertyTypeRow],
) -> ObjectPropertyMap:
    base_patch: dict[str, object] = {}
    for prop in properties:
        if prop["source"] != "dataset":
            continue
        column_name = prop["column_name"]
        if column_name is None:
            raise ValidationFailed("dataset-backed property column missing", details={"property": prop["api_name"]})
        base_patch[prop["api_name"]] = row.get(column_name)
    return base_patch
