"""Infrastructure adapter implementation for compute."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from foundry_lite.application.ports import DatasetCheckConfig, DatasetCheckResult
from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureMode,
)
from foundry_lite.application.ports.code_execution import CodeExecutionAdapter
from foundry_lite.application.ports.compute_adapter import (
    BoundedParquetRead,
    InputFilePaths,
    ParquetFieldType,
    PythonTransformPlan,
    SqlTransformPlan,
    TabularRow,
    TransformExecutionResult,
    TransformPlan,
)
from foundry_lite.application.ports.dataset_aggregation import (
    DatasetAggregationComputeResult,
    DatasetAggregationFilter,
    DatasetAggregationGroup,
    DatasetAggregationMetric,
    DatasetAggregationPlan,
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
    _sql_literal,
)
from foundry_lite.domain.errors import ValidationFailed


class DuckDBComputeAdapter:
    """DuckDB-backed compute adapter for the local MVP runtime."""

    profile_name = "duckdb"

    def __init__(self, *, code_execution_adapter: CodeExecutionAdapter | None = None) -> None:
        self._code_execution_adapter = code_execution_adapter

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

    def rows_to_parquet(
        self,
        rows: Sequence[Mapping[str, object]],
        target_path: Path,
        fieldnames: list[str],
        *,
        field_types: Mapping[str, ParquetFieldType] | None = None,
    ) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            pq.write_table(_typed_rows_table(rows, fieldnames, field_types=field_types), target_path)
        except ValidationFailed:
            target_path.unlink(missing_ok=True)
            raise
        except (pa.ArrowException, OverflowError, TypeError, ValueError) as exc:
            target_path.unlink(missing_ok=True)
            raise _typed_parquet_failure(target_path, fieldnames, len(rows), exc) from exc

    def rows_from_parquet(self, parquet_path: Path) -> list[TabularRow]:
        con = duckdb.connect()
        try:
            result = con.execute("select * from read_parquet(?)", [str(parquet_path)])
            names = [str(column[0]) for column in result.description]
            return [_tabular_row(dict(zip(names, row, strict=True))) for row in result.fetchall()]
        finally:
            con.close()

    def rows_from_parquet_bounded(
        self,
        parquet_path: Path,
        *,
        max_rows: int,
        max_decoded_bytes: int,
    ) -> BoundedParquetRead:
        metadata_decoded_bytes = _require_parquet_read_bound(parquet_path, max_rows, max_decoded_bytes)
        rows: list[TabularRow] = []
        decoded_byte_count = 0
        con = duckdb.connect()
        try:
            result = con.execute("select * from read_parquet(?)", [str(parquet_path)])
            names = [str(column[0]) for column in result.description]
            while (raw_row := result.fetchone()) is not None:
                row = _tabular_row(dict(zip(names, raw_row, strict=True)))
                decoded_byte_count += _decoded_row_byte_count(row)
                _require_decoded_result_bound(
                    parquet_path,
                    len(rows) + 1,
                    decoded_byte_count,
                    max_rows,
                    max_decoded_bytes,
                )
                rows.append(row)
        finally:
            con.close()
        return BoundedParquetRead(tuple(rows), max(metadata_decoded_bytes, decoded_byte_count))

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
        parquet_schema = pq.read_schema(parquet_path)
        primary_key_set = set(primary_key)
        columns = [
            {
                "name": row[0],
                "type": _inspected_parquet_type(parquet_schema, str(row[0]), str(row[1])),
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

    def aggregate_parquet(
        self,
        parquet_paths: Sequence[Path],
        plan: DatasetAggregationPlan,
    ) -> DatasetAggregationComputeResult:
        paths = tuple(parquet_paths)
        if not paths:
            return {"rowCount": 0, "filteredRowCount": 0, "groups": [], "totalGroups": 0}
        con = duckdb.connect()
        try:
            scan_sql = _dataset_aggregation_scan_sql(paths)
            view_sql = f"create temp view foundry_aggregate_source as select * from {scan_sql}"  # nosec B608
            con.execute(view_sql)
            return _execute_dataset_aggregation(con, plan)
        finally:
            con.close()

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
        if self._code_execution_adapter is None:
            raise AdapterError(
                AdapterFailure(
                    adapter_profile="code-execution-unconfigured",
                    operation="execute_python_transform",
                    kind="unavailable",
                    is_retryable=False,
                    operator_message="Python transform execution requires an isolated code-execution adapter.",
                    details={"codeExecution": {"failureType": "runtime_unavailable", "executionAttempted": False}},
                )
            )
        return self._code_execution_adapter.execute_python_transform(plan)

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


def _inspected_parquet_type(schema: pa.Schema, column_name: str, duckdb_type: str) -> str:
    """Preserve Arrow's null-only marker instead of DuckDB's INTEGER fallback."""

    field = schema.field(column_name)
    if pa.types.is_null(field.type):
        return "null"
    return _normalize_duckdb_type(duckdb_type)


def _typed_rows_table(
    rows: Sequence[Mapping[str, object]],
    fieldnames: list[str],
    *,
    field_types: Mapping[str, ParquetFieldType] | None = None,
) -> pa.Table:
    fields = _validated_parquet_fieldnames(fieldnames)
    declared_types = _validated_parquet_field_types(fields, field_types)
    normalized = _normalized_parquet_rows(rows, fields)
    arrays = [
        pa.array(
            [row[field] for row in normalized],
            type=_declared_or_inferred_arrow_type(normalized, field, declared_types),
        )
        for field in fields
    ]
    return pa.Table.from_arrays(arrays, names=fields)


def _validated_parquet_field_types(
    fieldnames: Sequence[str],
    field_types: Mapping[str, ParquetFieldType] | None,
) -> Mapping[str, ParquetFieldType]:
    declared: dict[str, ParquetFieldType] = dict(field_types or {})
    unknown_fields = sorted(set(declared) - set(fieldnames))
    supported = {"boolean", "float64", "int64", "string"}
    invalid_types = sorted({value for value in declared.values() if value not in supported})
    if unknown_fields or invalid_types:
        raise ValidationFailed(
            "rows cannot be written as typed parquet",
            details={
                "reason": "invalid_field_types",
                "unknownFields": unknown_fields,
                "invalidTypes": invalid_types,
            },
        )
    return declared


def _declared_or_inferred_arrow_type(
    rows: Sequence[Mapping[str, object]],
    field: str,
    declared_types: Mapping[str, ParquetFieldType],
) -> pa.DataType:
    declared = declared_types.get(field)
    if declared is not None:
        return {
            "boolean": pa.bool_(),
            "float64": pa.float64(),
            "int64": pa.int64(),
            "string": pa.string(),
        }[declared]
    return _json_arrow_type([row[field] for row in rows], field)


def _validated_parquet_fieldnames(fieldnames: list[str]) -> tuple[str, ...]:
    if not fieldnames or any(not isinstance(field, str) or not field.strip() for field in fieldnames):
        raise ValidationFailed("rows cannot be written as typed parquet", details={"reason": "invalid_fieldnames"})
    if len(set(fieldnames)) != len(fieldnames):
        raise ValidationFailed("rows cannot be written as typed parquet", details={"reason": "duplicate_fieldnames"})
    return tuple(fieldnames)


def _normalized_parquet_rows(
    rows: Sequence[Mapping[str, object]],
    fieldnames: Sequence[str],
) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValidationFailed(
                "rows cannot be written as typed parquet",
                details={"reason": "row_not_mapping", "rowIndex": index},
            )
        normalized.append({field: _json_ready(row.get(field)) for field in fieldnames})
    return normalized


def _json_arrow_type(values: Sequence[object], path: str) -> pa.DataType:
    present = [value for value in values if value is not None]
    if not present:
        return pa.null()
    container_type = _json_container_arrow_type(present, path)
    if container_type is not None:
        return container_type
    return _json_scalar_arrow_type(present, path)


def _json_container_arrow_type(
    present: Sequence[object],
    path: str,
) -> pa.DataType | None:
    if all(isinstance(value, Mapping) for value in present):
        return _json_mapping_arrow_type(cast(Sequence[Mapping[str, object]], present), path)
    if all(isinstance(value, list) for value in present):
        items = [item for value in cast(Sequence[list[object]], present) for item in value]
        return pa.list_(_json_arrow_type(items, f"{path}[]")) if items else pa.list_(pa.null())
    return None


def _json_scalar_arrow_type(
    present: Sequence[object],
    path: str,
) -> pa.DataType:
    if all(isinstance(value, bool) for value in present):
        return pa.bool_()
    number_type = _json_number_arrow_type(present)
    if number_type is not None:
        return number_type
    if all(isinstance(value, str) for value in present):
        return pa.string()
    raise ValidationFailed(
        "rows cannot be written as typed parquet",
        details={"reason": "incompatible_value_types", "field": path, "valueTypes": _value_type_names(present)},
    )


def _json_number_arrow_type(present: Sequence[object]) -> pa.DataType | None:
    if all(isinstance(value, int) and not isinstance(value, bool) for value in present):
        return _integer_arrow_type(cast(Sequence[int], present))
    if all(isinstance(value, int | float) and not isinstance(value, bool) for value in present):
        return pa.float64()
    return None


def _json_mapping_arrow_type(values: Sequence[Mapping[str, object]], path: str) -> pa.DataType:
    keys = sorted({key for value in values for key in value})
    if not keys:
        return pa.map_(pa.string(), pa.null())
    return pa.struct(
        [pa.field(key, _json_arrow_type([value.get(key) for value in values], f"{path}.{key}")) for key in keys]
    )


def _integer_arrow_type(values: Sequence[int]) -> pa.DataType:
    if all(-(2**63) <= value < 2**63 for value in values):
        return pa.int64()
    precision = max(len(str(abs(value))) for value in values)
    if precision <= 38:
        return pa.decimal128(precision, 0)
    raise ValidationFailed(
        "rows cannot be written as typed parquet",
        details={"reason": "integer_out_of_range", "maxPrecision": precision},
    )


def _value_type_names(values: Sequence[object]) -> list[str]:
    return sorted({type(value).__name__ for value in values})


def _typed_parquet_failure(
    target_path: Path,
    fieldnames: Sequence[str],
    row_count: int,
    exc: Exception,
) -> ValidationFailed:
    return ValidationFailed(
        "rows cannot be written as typed parquet",
        details={
            "path": str(target_path),
            "fieldnames": list(fieldnames),
            "rowCount": row_count,
            "errorType": type(exc).__name__,
        },
    )


def _require_parquet_read_bound(
    parquet_path: Path,
    max_rows: int,
    max_decoded_bytes: int,
) -> int:
    if max_rows < 0 or max_decoded_bytes < 0:
        raise ValidationFailed("parquet read bounds must not be negative")
    metadata = pq.ParquetFile(parquet_path).metadata
    if metadata.num_rows > max_rows:
        raise _parquet_bound_failure("rows", metadata.num_rows, max_rows, parquet_path)
    decoded_bytes = sum(
        max(metadata.row_group(group).column(column).total_uncompressed_size, 0)
        for group in range(metadata.num_row_groups)
        for column in range(metadata.num_columns)
    )
    decoded_bytes += metadata.num_rows * max(metadata.num_columns, 1) * 16
    if decoded_bytes > max_decoded_bytes:
        raise _parquet_bound_failure("decoded_bytes", decoded_bytes, max_decoded_bytes, parquet_path)
    return decoded_bytes


def _require_decoded_result_bound(
    parquet_path: Path,
    row_count: int,
    decoded_byte_count: int,
    max_rows: int,
    max_decoded_bytes: int,
) -> None:
    if row_count > max_rows:
        raise _parquet_bound_failure("rows", row_count, max_rows, parquet_path)
    if decoded_byte_count > max_decoded_bytes:
        raise _parquet_bound_failure("decoded_bytes", decoded_byte_count, max_decoded_bytes, parquet_path)


def _parquet_bound_failure(
    limit_kind: str,
    actual: int,
    maximum: int,
    parquet_path: Path,
) -> ValidationFailed:
    return ValidationFailed(
        "parquet read bound exceeded",
        details={
            "limitKind": limit_kind,
            "actual": actual,
            "maximum": maximum,
            "compressedByteCount": parquet_path.stat().st_size,
        },
    )


def _decoded_row_byte_count(row: Mapping[str, object]) -> int:
    return len(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())


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


def _dataset_aggregation_scan_sql(paths: Sequence[Path]) -> str:
    if len(paths) == 1:
        return f"read_parquet({_sql_literal(paths[0])})"
    path_literals = ", ".join(_sql_literal(path) for path in paths)
    return f"read_parquet([{path_literals}])"


def _execute_dataset_aggregation(
    con: duckdb.DuckDBPyConnection,
    plan: DatasetAggregationPlan,
) -> DatasetAggregationComputeResult:
    where_sql, params = _dataset_aggregation_filter_clause(plan.filters)
    row_count = _dataset_aggregation_count(con, "")
    filtered_count = _dataset_aggregation_count(con, where_sql, params)
    if filtered_count == 0:
        return {"rowCount": row_count, "filteredRowCount": 0, "groups": [], "totalGroups": 0}
    groups = _dataset_aggregation_groups(con, plan, where_sql, params)
    if len(groups) > plan.group_limit:
        raise ValidationFailed(
            "dataset aggregation exceeds the group limit; add a filter or group by a lower-cardinality column",
            details={"groupLimit": plan.group_limit},
        )
    return {
        "rowCount": row_count,
        "filteredRowCount": filtered_count,
        "groups": groups,
        "totalGroups": len(groups),
    }


def _dataset_aggregation_count(
    con: duckdb.DuckDBPyConnection,
    where_sql: str,
    params: Sequence[object] = (),
) -> int:
    query = f"select count(*) from foundry_aggregate_source {where_sql}"  # nosec B608
    return _required_int_cell(con.execute(query, list(params)).fetchone(), "dataset aggregation count")


def _dataset_aggregation_groups(
    con: duckdb.DuckDBPyConnection,
    plan: DatasetAggregationPlan,
    where_sql: str,
    params: Sequence[object],
) -> list[DatasetAggregationGroup]:
    query = _dataset_aggregation_group_query(plan, where_sql)
    rows = con.execute(query, list(params)).fetchall()  # nosec B608
    groups = [_dataset_aggregation_group(row, plan) for row in rows]
    return sorted(groups, key=lambda group: json.dumps(group["key"], sort_keys=True, default=str))


def _dataset_aggregation_group_query(plan: DatasetAggregationPlan, where_sql: str) -> str:
    group_select = [
        f"{_sql_identifier(column)} as {_sql_identifier(f'group_{index}')}"
        for index, column in enumerate(plan.group_by)
    ]
    metric_select = [_dataset_aggregation_metric_sql(metric, index) for index, metric in enumerate(plan.metrics)]
    select_sql = ", ".join([*group_select, *metric_select])
    group_sql = _dataset_aggregation_group_by_sql(plan.group_by)
    limit = plan.group_limit + 1
    return f"select {select_sql} from foundry_aggregate_source {where_sql}{group_sql} limit {limit}"  # nosec B608


def _dataset_aggregation_group_by_sql(group_by: Sequence[str]) -> str:
    if not group_by:
        return ""
    columns = ", ".join(_sql_identifier(column) for column in group_by)
    return f" group by {columns}"


def _dataset_aggregation_metric_sql(metric: DatasetAggregationMetric, index: int) -> str:
    alias = _sql_identifier(f"metric_{index}")
    if metric["function"] == "count":
        return f"count(*) as {alias}"
    column = _sql_identifier(str(metric["property"]))
    function = metric["function"]
    return f"{function}(try_cast({column} as double)) as {alias}"


def _dataset_aggregation_group(row: tuple[object, ...], plan: DatasetAggregationPlan) -> DatasetAggregationGroup:
    group_values = row[: len(plan.group_by)]
    metric_values = row[len(plan.group_by) :]
    return {
        "key": {column: _json_ready(group_values[index]) for index, column in enumerate(plan.group_by)},
        "metrics": {
            metric["name"]: _dataset_aggregation_metric_value(metric, metric_values[index])
            for index, metric in enumerate(plan.metrics)
        },
    }


def _dataset_aggregation_metric_value(metric: DatasetAggregationMetric, value: object) -> float | int | None:
    ready = _json_ready(value)
    if ready is None:
        return None
    if metric["function"] == "count":
        if isinstance(ready, int) and not isinstance(ready, bool):
            return ready
        raise ValidationFailed("dataset aggregation count returned a non-integer value")
    if isinstance(ready, int | float) and not isinstance(ready, bool):
        return float(ready)
    raise ValidationFailed(
        "dataset aggregation metric returned a non-numeric value",
        details={"metric": metric["name"]},
    )


def _dataset_aggregation_filter_clause(filters: Sequence[DatasetAggregationFilter]) -> tuple[str, list[object]]:
    if not filters:
        return "", []
    clauses: list[str] = []
    params: list[object] = []
    for filter_item in filters:
        clause, clause_params = _dataset_aggregation_filter_sql(filter_item)
        clauses.append(clause)
        params.extend(clause_params)
    return " where " + " and ".join(clauses), params


def _dataset_aggregation_filter_sql(filter_item: DatasetAggregationFilter) -> tuple[str, list[object]]:
    column = _sql_identifier(filter_item["column"])
    operator = filter_item["operator"]
    value = filter_item["value"]
    if operator in {"gt", "gte", "lt", "lte"}:
        return f"try_cast({column} as double) {_numeric_operator_sql(operator)} try_cast(? as double)", [value]
    text = f"coalesce(cast({column} as varchar), 'null')"
    if operator == "eq":
        return f"{text} = ?", [value]
    if operator == "neq":
        return f"{text} != ?", [value]
    return f"contains(lower({text}), lower(?))", [value]


def _numeric_operator_sql(operator: str) -> str:
    if operator == "gt":
        return ">"
    if operator == "gte":
        return ">="
    if operator == "lt":
        return "<"
    return "<="


def _read_arrow_tables(paths: InputFilePaths) -> Any:
    parquet_paths = _input_path_tuple(paths)
    if len(parquet_paths) == 1:
        return pq.read_table(parquet_paths[0])
    return pa.concat_tables([pq.read_table(path) for path in parquet_paths])


def _input_path_tuple(paths: InputFilePaths) -> tuple[Path, ...]:
    return (paths,) if isinstance(paths, Path) else paths
