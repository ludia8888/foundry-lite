"""Application port contract for the object-index row-hash changelog.

Dataset-backed reindexes are snapshot rebuilds: without a memory of what was
indexed last time, every refresh must compare every parquet row against the
database. This port persists one row hash per currently indexed primary key so
the next refresh can diff in memory and touch only changed or removed keys.
The stored hash reuses the exact ``source_hash`` function applied to object
records, so a stored hash always corresponds to the base layer the index holds.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Protocol

from foundry_lite.application.ports.transaction_context import TransactionContext

__all__ = ["ObjectIndexRowHashRepository"]


class ObjectIndexRowHashRepository(Protocol):
    """DB boundary for per-primary-key row hashes of dataset-backed object types."""

    def load_row_hashes(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_id: str,
    ) -> dict[str, str]:
        """Return the stored ``object_id -> row_hash`` map for one object type.

        An empty map means no changelog baseline exists yet (first refresh after
        the feature landed), which callers must treat as "full rebuild required".
        """
        ...

    def replace_row_hashes(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_id: str,
        dataset_version_id: str,
        row_hashes: Mapping[str, str],
        updated_at: str,
    ) -> None:
        """Replace the whole stored map with the hashes of one dataset version.

        Full rebuilds (seeding, shadow switches, ontology reindexes) recompute
        every row, so wholesale replacement is the only write that keeps the
        stored map exactly in step with what the rebuild just indexed.
        """
        ...

    def apply_row_hash_changes(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_id: str,
        dataset_version_id: str,
        upserted: Mapping[str, str],
        removed_object_ids: Collection[str],
        updated_at: str,
    ) -> None:
        """Apply an incremental delta: upsert changed keys, drop removed keys.

        Incremental refreshes must not rewrite hashes for unchanged keys,
        otherwise the write cost grows with dataset size instead of change size
        and the whole point of the changelog is lost.
        """
        ...
