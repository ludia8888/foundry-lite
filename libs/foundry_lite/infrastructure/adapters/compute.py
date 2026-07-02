"""Infrastructure adapter implementation for compute."""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from foundry_lite.application.ports import DatasetCheckConfig, DatasetCheckResult
from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.compute_adapter import (
    InputFilePaths,
    PythonTransformPlan,
    SqlTransformPlan,
    TabularRow,
    TransformDeadLetterRecord,
    TransformExecutionResult,
    TransformPlan,
)
from foundry_lite.application.primitives import (
    INPUT_PATTERN,
    StagedFileStats,
    _file_hash,
    _json_hash,
    _json_ready,
    _normalize_duckdb_type,
    _required_row,
    _sql_identifier,
    _write_rows_to_csv,
)
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.transforms_sdk import Output, _runtime_input, _runtime_output


class DuckDBComputeAdapter:
    """DuckDB-backed compute adapter for the local MVP runtime."""

    profile_name = "duckdb"

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "csv_to_parquet",
                    "validation",
                    False,
                    "CSV input could not be converted; check file format and headers.",
                ),
                AdapterFailureMode(
                    "execute_transform",
                    "unsupported",
                    False,
                    "Transform plan is not supported by this compute adapter.",
                ),
                AdapterFailureMode(
                    "execute_transform",
                    "timeout",
                    True,
                    "Transform execution timed out; retry with the same transaction input.",
                    timeout_seconds=600,
                ),
            ),
        )

    def csv_to_parquet(self, source_path: Path, target_path: Path) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect()
        try:
            con.read_csv(str(source_path), header=True).write_parquet(str(target_path))
        except Exception as exc:
            raise ValidationFailed("invalid csv input", details={"path": str(source_path)}) from exc
        finally:
            con.close()

    def rows_to_parquet(self, rows: Sequence[Mapping[str, object]], target_path: Path, fieldnames: list[str]) -> None:
        csv_path = target_path.with_suffix(".csv")
        _write_rows_to_csv(rows, csv_path, fieldnames)
        self.csv_to_parquet(csv_path, target_path)

    def rows_from_parquet(self, parquet_path: Path) -> list[TabularRow]:
        con = duckdb.connect()
        try:
            result = con.execute("select * from read_parquet(?)", [str(parquet_path)])
            names = [str(column[0]) for column in result.description]
            return [_tabular_row(dict(zip(names, row, strict=True))) for row in result.fetchall()]
        finally:
            con.close()

    def preview_parquet(self, parquet_path: Path, *, limit: int) -> list[TabularRow]:
        con = duckdb.connect()
        try:
            result = con.execute("select * from read_parquet(?) limit ?", [str(parquet_path), int(limit)])
            columns = [str(column[0]) for column in result.description]
            return [_tabular_row(dict(zip(columns, row, strict=True))) for row in result.fetchall()]
        finally:
            con.close()

    def inspect_parquet(self, parquet_path: Path, primary_key: list[str]) -> StagedFileStats:
        con = duckdb.connect()
        try:
            row_count = _required_int_cell(
                con.execute("select count(*) from read_parquet(?)", [str(parquet_path)]).fetchone(),
                "parquet row count",
            )
            describe = con.execute("describe select * from read_parquet(?)", [str(parquet_path)]).fetchall()
        finally:
            con.close()
        primary_key_set = set(primary_key)
        columns = [
            {
                "name": row[0],
                "type": _normalize_duckdb_type(row[1]),
                "nullable": row[0] not in primary_key_set,
            }
            for row in describe
        ]
        schema_json: dict[str, object] = {"columns": columns, "primary_key": primary_key, "cdc": {"enabled": False}}
        return StagedFileStats(
            parquet_path=parquet_path,
            row_count=row_count,
            byte_size=parquet_path.stat().st_size,
            content_hash=_file_hash(parquet_path),
            schema_json=schema_json,
            schema_hash=_json_hash(schema_json),
        )

    def execute_check(self, parquet_path: Path, row_count: int, check: DatasetCheckConfig) -> DatasetCheckResult:
        check_type = str(check["type"])
        if check_type == "row_count_min":
            return self._row_count_min_check(row_count, check)
        con = duckdb.connect()
        try:
            if check_type == "not_null":
                return self._not_null_check(con, parquet_path, check)
            if check_type == "unique":
                return self._unique_check(con, parquet_path, check)
            if check_type == "unique_tuple":
                return self._unique_tuple_check(con, parquet_path, check)
            if check_type == "accepted_values":
                return self._accepted_values_check(con, parquet_path, check)
        finally:
            con.close()
        raise ValidationFailed("unsupported dataset quality check type", details={"check_type": check_type})

    def execute_transform(self, plan: TransformPlan) -> TransformExecutionResult:
        if isinstance(plan, SqlTransformPlan):
            return self._execute_sql_transform(plan)
        if isinstance(plan, PythonTransformPlan):
            return self._execute_python_transform(plan)
        raise ValidationFailed(
            "DuckDBComputeAdapter does not support transform plan",
            details={"plan_kind": type(plan).__name__},
        )

    def _execute_sql_transform(self, plan: SqlTransformPlan) -> TransformExecutionResult:
        sql = plan.sql_template
        target_path = plan.target_path
        # Transform SQL is user-supplied via POST /api/transforms/sql, so it runs with DuckDB
        # filesystem and network access disabled. This blocks replacement scans
        # (e.g. FROM '/other-tenant/data.parquet') and glob(), which a denylist regex cannot
        # reliably catch. Declared inputs are loaded via PyArrow and registered as in-memory
        # views, and the result is written with PyArrow, so no trusted I/O depends on DuckDB's
        # file layer.
        con = duckdb.connect(config={"enable_external_access": False})
        try:
            for index, (dataset_ref, input_paths) in enumerate(plan.input_paths_by_ref.items()):
                view = f"input_{index}"
                _sql_identifier(view)
                con.register(view, _read_arrow_tables(input_paths))
                sql = sql.replace(f"{{{{ input('{dataset_ref}') }}}}", view)
            unresolved = INPUT_PATTERN.findall(sql)
            if unresolved:
                raise ValidationFailed("transform has unresolved inputs", details={"inputs": unresolved})
            result_table = con.sql(sql).to_arrow_table()
            target_path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(result_table, target_path)
            return TransformExecutionResult()
        finally:
            con.close()

    def _execute_python_transform(self, plan: PythonTransformPlan) -> TransformExecutionResult:
        target_path = plan.target_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        module_path = _write_python_transform_module(plan)
        dead_letters: list[TransformDeadLetterRecord] = []
        try:
            with _python_entrypoint_parent_on_path(plan.entrypoint):
                module = _load_python_transform_module(module_path)
                func = _python_transform_callable(module, plan.function_name)
                result = func(**_python_transform_kwargs(plan, func, dead_letters))
            if result is not None:
                output = _runtime_output(plan.output_dataset_ref, _arrow_table_writer(target_path))
                _write_python_returned_rows(output, result)
            if not target_path.exists():
                raise ValidationFailed("Python transform did not write output", details={"entrypoint": plan.entrypoint})
            return TransformExecutionResult(dead_letters=tuple(dead_letters))
        finally:
            module_path.unlink(missing_ok=True)

    def _row_count_min_check(self, row_count: int, check: DatasetCheckConfig) -> DatasetCheckResult:
        minimum = int(cast(str | int, check["min"]))
        status = "passed" if row_count >= minimum else "failed"
        return {"check": str(check["type"]), "status": status, "row_count": row_count, "min": minimum}

    def _not_null_check(
        self,
        con: duckdb.DuckDBPyConnection,
        parquet_path: Path,
        check: DatasetCheckConfig,
    ) -> DatasetCheckResult:
        failures: dict[str, int] = {}
        for column in cast(list[str], check["columns"]):
            count = self._null_count(con, parquet_path, column)
            if count:
                failures[column] = count
        return {
            "check": str(check["type"]),
            "status": "failed" if failures else "passed",
            "failures": failures,
        }

    def _null_count(self, con: duckdb.DuckDBPyConnection, parquet_path: Path, column: str) -> int:
        column_identifier = _sql_identifier(column)
        # column_identifier is validated and parquet path is a bound parameter.
        null_check_sql = f"select count(*) from read_parquet(?) where {column_identifier} is null"  # nosec B608
        return _required_int_cell(
            con.execute(null_check_sql, [str(parquet_path)]).fetchone(),  # nosec B608
            "not null health check",
        )

    def _unique_check(
        self,
        con: duckdb.DuckDBPyConnection,
        parquet_path: Path,
        check: DatasetCheckConfig,
    ) -> DatasetCheckResult:
        column = str(check["column"])
        duplicate_count = self._duplicate_group_count(con, parquet_path, column)
        return {
            "check": str(check["type"]),
            "status": "failed" if duplicate_count else "passed",
            "column": column,
            "duplicate_groups": duplicate_count,
        }

    def _unique_tuple_check(
        self,
        con: duckdb.DuckDBPyConnection,
        parquet_path: Path,
        check: DatasetCheckConfig,
    ) -> DatasetCheckResult:
        columns = _check_columns(check)
        duplicate_count = self._duplicate_tuple_group_count(con, parquet_path, columns)
        return {
            "check": str(check["type"]),
            "status": "failed" if duplicate_count else "passed",
            "columns": columns,
            "duplicate_groups": duplicate_count,
        }

    def _accepted_values_check(
        self,
        con: duckdb.DuckDBPyConnection,
        parquet_path: Path,
        check: DatasetCheckConfig,
    ) -> DatasetCheckResult:
        column = str(check["column"])
        values = _check_values(check)
        invalid_count = self._accepted_values_invalid_count(con, parquet_path, column, values)
        return {
            "check": str(check["type"]),
            "status": "failed" if invalid_count else "passed",
            "column": column,
            "accepted_values": values,
            "invalid_count": invalid_count,
            "sample_invalid_values": self._accepted_values_sample_values(con, parquet_path, column, values),
        }

    def _accepted_values_invalid_count(
        self,
        con: duckdb.DuckDBPyConnection,
        parquet_path: Path,
        column: str,
        values: list[object],
    ) -> int:
        predicate, params = _accepted_values_predicate(column, values)
        invalid_count_sql = f"select count(*) from read_parquet(?) where {predicate}"  # nosec B608
        return _required_int_cell(
            con.execute(invalid_count_sql, [str(parquet_path), *params]).fetchone(),  # nosec B608
            "accepted values health check",
        )

    def _accepted_values_sample_values(
        self,
        con: duckdb.DuckDBPyConnection,
        parquet_path: Path,
        column: str,
        values: list[object],
    ) -> list[object]:
        column_identifier = _sql_identifier(column)
        predicate, params = _accepted_values_predicate(column, values)
        sample_sql = f"select distinct {column_identifier} from read_parquet(?) where {predicate} limit 5"  # nosec B608
        rows = con.execute(sample_sql, [str(parquet_path), *params]).fetchall()  # nosec B608
        return [_json_ready(row[0]) for row in rows]

    def _duplicate_group_count(self, con: duckdb.DuckDBPyConnection, parquet_path: Path, column: str) -> int:
        column_identifier = _sql_identifier(column)
        duplicate_check_sql = (
            "select count(*) from (select "  # nosec B608
            f"{column_identifier}, count(*) c from read_parquet(?) "
            f"group by {column_identifier} having c > 1)"
        )
        return _required_int_cell(
            con.execute(duplicate_check_sql, [str(parquet_path)]).fetchone(),  # nosec B608
            "unique health check",
        )

    def _duplicate_tuple_group_count(
        self,
        con: duckdb.DuckDBPyConnection,
        parquet_path: Path,
        columns: list[str],
    ) -> int:
        column_identifiers = ", ".join(_sql_identifier(column) for column in columns)
        duplicate_check_sql = (
            "select count(*) from (select "  # nosec B608
            f"{column_identifiers}, count(*) c from read_parquet(?) "
            f"group by {column_identifiers} having c > 1)"
        )
        return _required_int_cell(
            con.execute(duplicate_check_sql, [str(parquet_path)]).fetchone(),  # nosec B608
            "unique tuple health check",
        )


class FakeComputeAdapter(DuckDBComputeAdapter):
    """Fake compute profile that keeps local semantics but exercises adapter replacement."""

    profile_name = "fake-compute"


def _tabular_row(value: object) -> TabularRow:
    ready = _json_ready(value)
    if not isinstance(ready, Mapping):
        raise ValidationFailed("compute adapter row must be a mapping")
    row: TabularRow = {}
    for raw_key, raw_value in ready.items():
        if not isinstance(raw_key, str):
            raise ValidationFailed("compute adapter row keys must be strings", details={"key": str(raw_key)})
        row[raw_key] = raw_value
    return row


def _required_int_cell(row: tuple[object, ...] | None, operation: str) -> int:
    value = _required_row(row, operation)[0]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationFailed(f"{operation} did not return an integer", details={"value": str(value)})
    return value


def _check_columns(check: DatasetCheckConfig) -> list[str]:
    raw_columns = check.get("columns")
    if not isinstance(raw_columns, Sequence) or isinstance(raw_columns, str | bytes):
        raise ValidationFailed("unique tuple check requires columns", details={"check": dict(check)})
    columns = [column for column in raw_columns if isinstance(column, str)]
    if not columns or len(columns) != len(raw_columns):
        raise ValidationFailed("unique tuple check requires string columns", details={"check": dict(check)})
    return columns


def _check_values(check: DatasetCheckConfig) -> list[object]:
    raw_values = check.get("values")
    if not isinstance(raw_values, Sequence) or isinstance(raw_values, str | bytes) or not raw_values:
        raise ValidationFailed("accepted values check requires values", details={"check": dict(check)})
    values = list(raw_values)
    if any(isinstance(value, Mapping | Sequence) and not isinstance(value, str | bytes) for value in values):
        raise ValidationFailed("accepted values check requires scalar values", details={"check": dict(check)})
    return values


def _accepted_values_predicate(column: str, values: list[object]) -> tuple[str, list[object]]:
    column_identifier = _sql_identifier(column)
    placeholders = ", ".join("?" for _ in values)
    predicate = f"{column_identifier} is not null and {column_identifier} not in ({placeholders})"
    return predicate, values


def _write_python_transform_module(plan: PythonTransformPlan) -> Path:
    digest = hashlib.sha256(f"{plan.entrypoint}:{plan.source_code}".encode()).hexdigest()[:16]
    module_path = plan.target_path.parent / f".foundry-lite-python-transform-{digest}.py"
    module_path.write_text(plan.source_code, encoding="utf-8")
    return module_path


def _load_python_transform_module(module_path: Path) -> ModuleType:
    module_name = f"_foundry_lite_transform_{module_path.stem.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ValidationFailed("Python transform entrypoint cannot be loaded", details={"entrypoint": str(module_path)})
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def _python_transform_callable(module: ModuleType, function_name: str | None) -> Callable[..., object]:
    if function_name:
        candidate = getattr(module, function_name, None)
        if callable(candidate):
            return cast(Callable[..., object], candidate)
        raise ValidationFailed("Python transform function not found", details={"function": function_name})
    decorated = [
        value
        for value in vars(module).values()
        if callable(value) and isinstance(getattr(value, "_foundry_lite_transform_bindings", None), Mapping)
    ]
    if len(decorated) == 1:
        return cast(Callable[..., object], decorated[0])
    candidate = getattr(module, "compute", None)
    if callable(candidate):
        return cast(Callable[..., object], candidate)
    raise ValidationFailed("Python transform must define one decorated function or compute()")


def _python_transform_kwargs(
    plan: PythonTransformPlan,
    callable_obj: Callable[..., object],
    dead_letters: list[TransformDeadLetterRecord],
) -> dict[str, object]:
    output = _runtime_output(plan.output_dataset_ref, _arrow_table_writer(plan.target_path))
    kwargs: dict[str, object] = {
        alias: _runtime_input(
            dataset_ref,
            _arrow_table_reader(plan.input_paths_by_ref[dataset_ref]),
            _row_error_recorder(dead_letters, dataset_ref),
        )
        for alias, dataset_ref in plan.input_refs_by_alias.items()
    }
    kwargs.update(_python_output_kwargs(callable_obj, output))
    return kwargs


def _row_error_recorder(
    dead_letters: list[TransformDeadLetterRecord],
    dataset_ref: str,
) -> Callable[[int, Mapping[str, object], str, str], None]:
    def record(row_index: int, row: Mapping[str, object], error_kind: str, error_message: str) -> None:
        dead_letters.append(
            TransformDeadLetterRecord(
                input_dataset_ref=dataset_ref,
                row_index=row_index,
                payload=_tabular_row(row),
                error_kind=error_kind,
                error_message=error_message,
            )
        )

    return record


def _python_output_kwargs(callable_obj: Callable[..., object], output: Output) -> dict[str, Output]:
    bindings = getattr(callable_obj, "_foundry_lite_transform_bindings", {})
    aliases = [name for name, binding in bindings.items() if isinstance(binding, Output)]
    if aliases:
        return {alias: output for alias in aliases}
    parameters = inspect.signature(callable_obj).parameters
    if "output" in parameters:
        return {"output": output}
    if "out" in parameters:
        return {"out": output}
    return {}


def _write_python_returned_rows(output: Output, result: object) -> None:
    if not isinstance(result, Sequence) or isinstance(result, str | bytes):
        raise ValidationFailed("Python transform return value must be a sequence of row mappings")
    if not all(isinstance(row, Mapping) for row in result):
        raise ValidationFailed("Python transform return rows must be mappings")
    output.write_rows(cast(Sequence[Mapping[str, object]], result))


def _arrow_table_reader(paths: InputFilePaths) -> Callable[[], Any]:
    return lambda: _read_arrow_tables(paths)


def _read_arrow_tables(paths: InputFilePaths) -> Any:
    parquet_paths = _input_path_tuple(paths)
    if len(parquet_paths) == 1:
        return pq.read_table(parquet_paths[0])
    return pa.concat_tables([pq.read_table(path) for path in parquet_paths])


def _input_path_tuple(paths: InputFilePaths) -> tuple[Path, ...]:
    return (paths,) if isinstance(paths, Path) else paths


def _arrow_table_writer(path: Path) -> Callable[[Any], None]:
    return lambda table: pq.write_table(table, path)


@contextmanager
def _python_entrypoint_parent_on_path(entrypoint: str):
    parent = str(Path(entrypoint).expanduser().resolve().parent)
    sys.path.insert(0, parent)
    try:
        yield
    finally:
        if parent in sys.path:
            sys.path.remove(parent)
