from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from foundry_lite.application.ports.compute_adapter import SqlTransformPlan, TransformPlan
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
    _write_rows_to_csv,
)
from foundry_lite.domain.errors import ValidationFailed


class DuckDBComputeAdapter:
    """DuckDB-backed compute adapter for the local MVP runtime."""

    profile_name = "duckdb"

    def csv_to_parquet(self, source_path: Path, target_path: Path) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect()
        try:
            con.read_csv(str(source_path), header=True).write_parquet(str(target_path))
        except Exception as exc:
            raise ValidationFailed("invalid csv input", details={"path": str(source_path)}) from exc
        finally:
            con.close()

    def rows_to_parquet(self, rows: list[dict[str, Any]], target_path: Path, fieldnames: list[str]) -> None:
        csv_path = target_path.with_suffix(".csv")
        _write_rows_to_csv(rows, csv_path, fieldnames)
        self.csv_to_parquet(csv_path, target_path)

    def rows_from_parquet(self, parquet_path: Path) -> list[dict[str, Any]]:
        con = duckdb.connect()
        try:
            result = con.execute("select * from read_parquet(?)", [str(parquet_path)])
            names = [column[0] for column in result.description]
            return [_json_ready(dict(zip(names, row, strict=True))) for row in result.fetchall()]
        finally:
            con.close()

    def preview_parquet(self, parquet_path: Path, *, limit: int) -> list[dict[str, Any]]:
        con = duckdb.connect()
        try:
            result = con.execute("select * from read_parquet(?) limit ?", [str(parquet_path), int(limit)])
            columns = [column[0] for column in result.description]
            return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
        finally:
            con.close()

    def inspect_parquet(self, parquet_path: Path, primary_key: list[str]) -> StagedFileStats:
        con = duckdb.connect()
        try:
            row_count = int(
                _required_row(
                    con.execute("select count(*) from read_parquet(?)", [str(parquet_path)]).fetchone(),
                    "parquet row count",
                )[0]
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
        schema_json = {"columns": columns, "primary_key": primary_key, "cdc": {"enabled": False}}
        return StagedFileStats(
            parquet_path=parquet_path,
            row_count=row_count,
            byte_size=parquet_path.stat().st_size,
            content_hash=_file_hash(parquet_path),
            schema_json=schema_json,
            schema_hash=_json_hash(schema_json),
        )

    def execute_check(self, parquet_path: Path, row_count: int, check: dict[str, Any]) -> dict[str, Any]:
        check_type = check["type"]
        if check_type == "row_count_min":
            return self._row_count_min_check(row_count, check)
        con = duckdb.connect()
        try:
            if check_type == "not_null":
                return self._not_null_check(con, parquet_path, check)
            if check_type == "unique":
                return self._unique_check(con, parquet_path, check)
        finally:
            con.close()
        return {"check": check_type, "status": "passed", "note": "unsupported check treated as noop"}

    def execute_transform(self, plan: TransformPlan) -> None:
        if not isinstance(plan, SqlTransformPlan):
            raise ValidationFailed(
                "DuckDBComputeAdapter only supports SqlTransformPlan today",
                details={"plan_kind": type(plan).__name__},
            )
        sql = plan.sql_template
        target_path = plan.target_path
        con = duckdb.connect()
        try:
            for index, (dataset_ref, parquet_path) in enumerate(plan.input_paths_by_ref.items()):
                view = f"input_{index}"
                _sql_identifier(view)
                con.read_parquet(str(parquet_path)).create_view(view, replace=True)
                sql = sql.replace(f"{{{{ input('{dataset_ref}') }}}}", view)
            unresolved = INPUT_PATTERN.findall(sql)
            if unresolved:
                raise ValidationFailed("transform has unresolved inputs", details={"inputs": unresolved})
            target_path.parent.mkdir(parents=True, exist_ok=True)
            # target_path is SQL-escaped by _sql_literal; transform SQL is
            # trusted local project code authored by registered transforms.
            # If transform SQL ever becomes user-supplied, this must be
            # replaced with parameterised DuckDB execution.
            # nosemgrep: foundry-lite-no-fstring-sql
            con.execute(f"copy ({sql}) to {_sql_literal(target_path)} (format parquet)")  # nosec B608
        finally:
            con.close()

    def _row_count_min_check(self, row_count: int, check: dict[str, Any]) -> dict[str, Any]:
        status = "passed" if row_count >= int(check["min"]) else "failed"
        return {"check": check["type"], "status": status, "row_count": row_count, "min": check["min"]}

    def _not_null_check(
        self,
        con: duckdb.DuckDBPyConnection,
        parquet_path: Path,
        check: dict[str, Any],
    ) -> dict[str, Any]:
        failures: dict[str, int] = {}
        for column in check["columns"]:
            count = self._null_count(con, parquet_path, column)
            if count:
                failures[column] = count
        return {
            "check": check["type"],
            "status": "failed" if failures else "passed",
            "failures": failures,
        }

    def _null_count(self, con: duckdb.DuckDBPyConnection, parquet_path: Path, column: str) -> int:
        column_identifier = _sql_identifier(column)
        # column_identifier is validated and parquet path is a bound parameter.
        null_check_sql = f"select count(*) from read_parquet(?) where {column_identifier} is null"  # nosec B608
        return int(
            _required_row(
                con.execute(null_check_sql, [str(parquet_path)]).fetchone(),  # nosec B608
                "not null health check",
            )[0]
        )

    def _unique_check(
        self,
        con: duckdb.DuckDBPyConnection,
        parquet_path: Path,
        check: dict[str, Any],
    ) -> dict[str, Any]:
        column = check["column"]
        duplicate_count = self._duplicate_group_count(con, parquet_path, column)
        return {
            "check": check["type"],
            "status": "failed" if duplicate_count else "passed",
            "column": column,
            "duplicate_groups": duplicate_count,
        }

    def _duplicate_group_count(self, con: duckdb.DuckDBPyConnection, parquet_path: Path, column: str) -> int:
        column_identifier = _sql_identifier(column)
        duplicate_check_sql = (
            "select count(*) from (select "  # nosec B608
            f"{column_identifier}, count(*) c from read_parquet(?) "
            f"group by {column_identifier} having c > 1)"
        )
        return int(
            _required_row(
                con.execute(duplicate_check_sql, [str(parquet_path)]).fetchone(),  # nosec B608
                "unique health check",
            )[0]
        )


class FakeComputeAdapter(DuckDBComputeAdapter):
    """Fake compute profile that keeps local semantics but exercises adapter replacement."""

    profile_name = "fake-compute"
