from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from foundry_lite.application.ports.dataset_quality_repository import DatasetCheckConfig, DatasetCheckResult
from foundry_lite.application.primitives import StagedFileStats

TabularRow = dict[str, object]
"""JSON-ready tabular row keyed by column name."""


@dataclass(frozen=True)
class SqlTransformPlan:
    """Logical plan for a SQL-based transform.

    `sql_template` may reference ``{{ input('dataset_ref') }}`` placeholders;
    the adapter substitutes them with the input file paths bound at execution
    time. This DTO is the only vendor-neutral surface application services
    use; concrete adapters (DuckDB today, future Spark/Flink) consume the
    plan and decide how to materialize it. New plan kinds (e.g.,
    ``PythonUdfTransformPlan``, ``IcebergMergeTransformPlan``) join this
    family without changing the application layer.
    """

    sql_template: str
    input_paths_by_ref: dict[str, Path]
    target_path: Path


TransformPlan = SqlTransformPlan
"""Union root for all logical transform plans. Today there is only
``SqlTransformPlan``; future kinds (Python UDF, native Spark, Iceberg
merge, etc.) are added as additional dataclass variants and unioned here
so application services stay vendor-neutral."""


class ComputeAdapter(Protocol):
    """Compute boundary for local tabular file work and logical transforms."""

    def csv_to_parquet(self, source_path: Path, target_path: Path) -> None:
        """Convert a CSV file into a Parquet file."""
        ...

    def rows_to_parquet(self, rows: Sequence[Mapping[str, object]], target_path: Path, fieldnames: list[str]) -> None:
        """Write in-memory rows into a Parquet file."""
        ...

    def rows_from_parquet(self, parquet_path: Path) -> list[TabularRow]:
        """Read all rows from a Parquet file as JSON-ready dictionaries."""
        ...

    def preview_parquet(self, parquet_path: Path, *, limit: int) -> list[TabularRow]:
        """Read a bounded preview from a Parquet file."""
        ...

    def inspect_parquet(self, parquet_path: Path, primary_key: list[str]) -> StagedFileStats:
        """Return row count, schema, hash, and size for a Parquet file."""
        ...

    def execute_check(self, parquet_path: Path, row_count: int, check: DatasetCheckConfig) -> DatasetCheckResult:
        """Execute a dataset health check against a Parquet file."""
        ...

    def execute_transform(self, plan: TransformPlan) -> None:
        """Execute a logical transform plan and write the output file.

        Adapters dispatch by plan kind (today only SqlTransformPlan). When
        new plan kinds are added, adapters should raise a typed error for
        unsupported variants rather than silently degrade.
        """
        ...
