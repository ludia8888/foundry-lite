from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract


@dataclass(frozen=True)
class SourceTableBatch:
    """Rows and schema read from a database source table."""

    rows: tuple[Mapping[str, object], ...]
    schema: Mapping[str, object]
    checkpoint: Mapping[str, object]


class SourceDatabaseAdapter(Protocol):
    """Boundary for exploring and batch-reading SQL database sources."""

    @property
    def profile_name(self) -> str: ...

    def failure_contract(self) -> AdapterFailureContract:
        """Return the adapter failure taxonomy promised by this profile."""
        ...

    def list_tables(self, database_url: str, *, sample_limit: int) -> Sequence[Mapping[str, object]]:
        """List visible tables/views with optional lightweight row samples."""
        ...

    def read_table_batch(
        self,
        database_url: str,
        *,
        table_name: str,
        batch_limit: int,
        checkpoint_column: str | None = None,
        after_value: object | None = None,
    ) -> SourceTableBatch:
        """Read one small, replayable source table batch."""
        ...
