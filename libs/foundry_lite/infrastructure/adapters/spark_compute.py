"""Spark-based compute adapter (distributed transform execution on local parquet)."""

from __future__ import annotations

import shutil
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureKind,
    AdapterFailureMode,
)
from foundry_lite.application.ports.code_execution import CodeExecutionAdapter
from foundry_lite.application.ports.compute_adapter import (
    PythonTransformPlan,
    SqlTransformPlan,
    TransformExecutionResult,
    TransformPlan,
)
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters.compute import (
    INPUT_PATTERN,
    DuckDBComputeAdapter,
    _input_path_tuple,
    _sql_identifier,
)


class SparkComputeAdapter(DuckDBComputeAdapter):
    """Runs CSV ingest and SQL transforms on a Spark session.

    Compute only ever sees local parquet paths (the engine resolves a pinned
    dataset version through the storage adapter before calling compute), so this
    adapter stays unaware of S3/Iceberg internals. Spark writes a directory of
    part files; each operation coalesces to one file and promotes it to the
    single-file target the dataset transaction protocol expects. Local parquet
    reads (preview/inspect/check) reuse the shared reader, so only the compute
    path itself runs on Spark.
    """

    profile_name = "spark"

    def __init__(
        self,
        *,
        session: Any | None = None,
        code_execution_adapter: CodeExecutionAdapter | None = None,
    ) -> None:
        super().__init__(code_execution_adapter=code_execution_adapter)
        self._session = session

    @property
    def _spark(self) -> Any:
        """Lazily build the Spark session so importing the adapter never starts a JVM."""
        if self._session is None:
            self._session = _build_local_spark_session()
        return self._session

    def failure_contract(self) -> AdapterFailureContract:
        """Return the Spark compute failure taxonomy."""
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "csv_to_parquet",
                    "validation",
                    False,
                    "CSV input could not be parsed by Spark; check file format and headers.",
                ),
                AdapterFailureMode(
                    "execute_transform",
                    "unsupported",
                    False,
                    "Transform plan is not supported by the Spark compute adapter.",
                ),
                AdapterFailureMode(
                    "execute_transform",
                    "timeout",
                    True,
                    "Spark job timed out; the driver was stopped, retry with the same transaction input.",
                    timeout_seconds=600,
                ),
                AdapterFailureMode(
                    "execute_transform",
                    "unavailable",
                    True,
                    "Spark session/executor became unavailable; retry after the cluster recovers.",
                ),
            ),
        )

    def csv_to_parquet(self, source_path: Path, target_path: Path) -> None:
        """Convert a local CSV file into a single parquet file using Spark."""
        try:
            frame = (
                self._spark.read.option("header", True)
                .option("inferSchema", True)
                .option("quote", '"')
                .option("escape", '"')
                .csv(str(source_path))
            )
            self._write_single_parquet(frame, target_path)
        except Exception as exc:  # noqa: BLE001 - classified below
            if _is_parse_error(exc):
                raise ValidationFailed("invalid csv input", details={"path": str(source_path)}) from exc
            raise self._compute_error("csv_to_parquet", exc) from exc

    def execute_transform(self, plan: TransformPlan) -> TransformExecutionResult:
        """Execute a SQL transform plan on Spark, writing one parquet output file."""
        if isinstance(plan, PythonTransformPlan):
            return super().execute_transform(plan)
        if not isinstance(plan, SqlTransformPlan):
            raise ValidationFailed(
                "SparkComputeAdapter only supports SqlTransformPlan today",
                details={"plan_kind": type(plan).__name__},
            )
        sql = plan.sql_template
        run_id = uuid4().hex
        views: list[str] = []
        try:
            for index, (dataset_ref, input_paths) in enumerate(plan.input_paths_by_ref.items()):
                view = f"foundry_input_{run_id}_{index}"
                _sql_identifier(view)
                parquet_paths = [str(path) for path in _input_path_tuple(input_paths)]
                self._spark.read.parquet(*parquet_paths).createOrReplaceTempView(view)
                views.append(view)
                sql = sql.replace(f"{{{{ input('{dataset_ref}') }}}}", view)
            unresolved = INPUT_PATTERN.findall(sql)
            if unresolved:
                raise ValidationFailed("transform has unresolved inputs", details={"inputs": unresolved})
            result = self._spark.sql(sql)
            self._write_single_parquet(result, plan.target_path)
            return TransformExecutionResult()
        except ValidationFailed:
            raise
        except Exception as exc:  # noqa: BLE001 - classified for the failure contract
            raise self._compute_error("execute_transform", exc) from exc
        finally:
            self._drop_temp_views(views)

    def _write_single_parquet(self, frame: Any, target_path: Path) -> None:
        """Coalesce a Spark frame to one part file and promote it to ``target_path``."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        staging_dir = target_path.parent / f".spark-{uuid4().hex}"
        frame.coalesce(1).write.mode("overwrite").parquet(str(staging_dir))
        try:
            parts = sorted(staging_dir.glob("*.parquet"))
            if len(parts) != 1:
                raise ValueError(f"Spark write produced {len(parts)} parquet parts, expected 1")
            if target_path.exists():
                target_path.unlink()
            shutil.move(str(parts[0]), str(target_path))
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

    def _compute_error(self, operation: str, exc: Exception) -> AdapterError:
        """Wrap a Spark failure as a typed, classified adapter error."""
        kind: AdapterFailureKind = "timeout" if isinstance(exc, TimeoutError) else "unavailable"
        return AdapterError(
            AdapterFailure(
                self.profile_name,
                operation,
                kind,
                True,
                f"Spark compute {operation} failed: {exc}",
                timeout_seconds=600 if kind == "timeout" else None,
            )
        )

    def _drop_temp_views(self, views: list[str]) -> None:
        """Drop per-execution temp views without masking the transform result."""
        for view in views:
            with suppress(Exception):
                self._spark.catalog.dropTempView(view)


def _is_parse_error(exc: Exception) -> bool:
    """Classify a Spark exception as a CSV/analysis parse failure (vs transient/cluster)."""
    name = type(exc).__name__
    return "AnalysisException" in name or "ParseException" in name or "IllegalArgument" in name


def _build_local_spark_session() -> Any:
    """Build (or reuse) a single-worker local Spark session tuned for deterministic tests."""
    from pyspark.sql import SparkSession  # noqa: PLC0415 - optional dependency, imported on use

    builder: Any = SparkSession.builder
    return (
        builder.master("local[1]")
        .appName("foundry-lite-spark-compute")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.ui.enabled", "false")
        .config("spark.driver.memory", "512m")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )
