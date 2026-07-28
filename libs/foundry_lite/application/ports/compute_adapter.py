"""Application port contract for compute adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.dataset_aggregation import (
    DatasetAggregationComputeResult,
    DatasetAggregationPlan,
)
from foundry_lite.application.ports.dataset_quality_repository import DatasetCheckConfig, DatasetCheckResult
from foundry_lite.application.primitives import StagedFileStats

TabularRow = dict[str, object]
"""JSON-ready tabular row keyed by column name."""

InputFilePaths = Path | tuple[Path, ...]
"""One or more local parquet files backing a pinned transform input."""

ParquetFieldType = Literal["boolean", "float64", "int64", "string"]
"""Vendor-neutral logical types for fields whose current batch is entirely null."""


@dataclass(frozen=True)
class TransformDeadLetterRecord:
    """One input row rejected by a row-aware transform execution policy."""

    input_dataset_ref: str
    row_index: int
    payload: Mapping[str, object]
    error_kind: str
    error_message: str


@dataclass(frozen=True)
class TransformExecutionResult:
    """Adapter result metadata that is durable only after application persistence."""

    dead_letters: tuple[TransformDeadLetterRecord, ...] = ()


@dataclass(frozen=True)
class SqlTransformPlan:
    """Logical plan for a SQL-based transform.

    `sql_template` may reference ``{{ input('dataset_ref') }}`` placeholders;
    the adapter substitutes them with the input file paths bound at execution
    time. This DTO is the only vendor-neutral surface application services
    use; concrete adapters (DuckDB today, Spark or isolated bounded workers)
    consume the
    plan and decide how to materialize it. New plan kinds (e.g.,
    ``PythonUdfTransformPlan``, ``IcebergMergeTransformPlan``) join this
    family without changing the application layer.
    """

    sql_template: str
    input_paths_by_ref: dict[str, InputFilePaths]
    target_path: Path


@dataclass(frozen=True)
class PythonTransformPlan:
    """Logical plan for a Python SDK transform.

    Application services bind committed dataset versions and a staging output
    path, then pass only SDK handles into user code. Raw storage paths stay
    inside the compute adapter.
    """

    entrypoint: str
    source_code: str
    function_name: str | None
    input_refs_by_alias: dict[str, str]
    input_paths_by_ref: dict[str, InputFilePaths]
    output_dataset_ref: str
    target_path: Path


TransformPlan = SqlTransformPlan | PythonTransformPlan
"""Union root for all logical transform plans."""


class ComputeAdapter(Protocol):
    """Compute boundary for local tabular file work and logical transforms."""

    @property
    def profile_name(self) -> str: ...

    def failure_contract(self) -> AdapterFailureContract:
        """Return the adapter failure taxonomy promised by this profile."""
        ...

    def csv_to_parquet(self, source_path: Path, target_path: Path) -> None:
        """Convert a CSV file into a Parquet file."""
        ...

    def rows_to_parquet(
        self,
        rows: Sequence[Mapping[str, object]],
        target_path: Path,
        fieldnames: list[str],
        *,
        field_types: Mapping[str, ParquetFieldType] | None = None,
    ) -> None:
        """Write JSON-ready rows using optional declared logical field types."""
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

    def aggregate_parquet(
        self,
        parquet_paths: Sequence[Path],
        plan: DatasetAggregationPlan,
    ) -> DatasetAggregationComputeResult:
        """Execute a validated aggregate plan against pinned Parquet files."""
        ...

    def execute_check(self, parquet_path: Path, row_count: int, check: DatasetCheckConfig) -> DatasetCheckResult:
        """Execute a dataset health check against a Parquet file."""
        ...

    def execute_transform(self, plan: TransformPlan) -> TransformExecutionResult:
        """Execute a logical transform plan and write the output file.

        Adapters dispatch by plan kind (today only SqlTransformPlan). When
        new plan kinds are added, adapters should raise a typed error for
        unsupported variants rather than silently degrade.
        """
        ...
