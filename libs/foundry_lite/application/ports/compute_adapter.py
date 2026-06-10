from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from foundry_lite.application.primitives import StagedFileStats


class ComputeAdapter(Protocol):
    """Compute boundary for local tabular file work and SQL transforms."""

    def csv_to_parquet(self, source_path: Path, target_path: Path) -> None:
        """Convert a CSV file into a Parquet file."""
        ...

    def rows_to_parquet(self, rows: list[dict[str, Any]], target_path: Path, fieldnames: list[str]) -> None:
        """Write in-memory rows into a Parquet file."""
        ...

    def rows_from_parquet(self, parquet_path: Path) -> list[dict[str, Any]]:
        """Read all rows from a Parquet file as JSON-ready dictionaries."""
        ...

    def preview_parquet(self, parquet_path: Path, *, limit: int) -> list[dict[str, Any]]:
        """Read a bounded preview from a Parquet file."""
        ...

    def inspect_parquet(self, parquet_path: Path, primary_key: list[str]) -> StagedFileStats:
        """Return row count, schema, hash, and size for a Parquet file."""
        ...

    def execute_check(self, parquet_path: Path, row_count: int, check: dict[str, Any]) -> dict[str, Any]:
        """Execute a dataset health check against a Parquet file."""
        ...

    def execute_sql_transform(
        self,
        *,
        sql_template: str,
        input_paths_by_ref: dict[str, Path],
        target_path: Path,
    ) -> None:
        """Execute a SQL transform and write the output Parquet file."""
        ...
