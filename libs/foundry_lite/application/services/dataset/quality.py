from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
from foundry_lite.application.primitives import (
    StagedFileStats,
    _file_hash,
    _json_hash,
    _new_id,
    _normalize_duckdb_type,
    _now,
    _required_row,
    _sql_identifier,
)
from foundry_lite.application.services.base import CoreServiceMixin
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from sqlalchemy import and_, func, insert, select
from sqlalchemy.engine import Connection


class DatasetQualityMixin(CoreServiceMixin):
    def _inspect_parquet(self, parquet_path: Path, primary_key: list[str]) -> StagedFileStats:
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
        columns = [
            {
                "name": row[0],
                "type": _normalize_duckdb_type(row[1]),
                "nullable": row[0] not in set(primary_key),
            }
            for row in describe
        ]
        schema_json = {"columns": columns, "primary_key": primary_key, "cdc": {"enabled": False}}
        return StagedFileStats(
            parquet_path=parquet_path,
            row_count=int(row_count),
            byte_size=parquet_path.stat().st_size,
            content_hash=_file_hash(parquet_path),
            schema_json=schema_json,
            schema_hash=_json_hash(schema_json),
        )

    def _ensure_schema(
        self,
        conn: Connection,
        dataset: dict[str, Any],
        schema_json: dict[str, Any],
        schema_hash: str,
    ) -> int:
        existing = (
            conn.execute(
                select(db.dataset_schemas).where(
                    and_(
                        db.dataset_schemas.c.dataset_id == dataset["id"],
                        db.dataset_schemas.c.schema_hash == schema_hash,
                    )
                )
            )
            .mappings()
            .first()
        )
        if existing:
            return int(existing["version"])
        latest = (
            conn.execute(
                select(func.max(db.dataset_schemas.c.version)).where(db.dataset_schemas.c.dataset_id == dataset["id"])
            ).scalar()
            or 0
        )
        version = int(latest) + 1
        conn.execute(
            insert(db.dataset_schemas).values(
                id=_new_id("schema"),
                dataset_id=dataset["id"],
                version=version,
                schema_json=schema_json,
                schema_hash=schema_hash,
                created_at=_now(),
            )
        )
        return version

    def _schema_compatibility_error(
        self,
        conn: Connection,
        dataset: dict[str, Any],
        next_schema: dict[str, Any],
    ) -> dict[str, Any] | None:
        latest_version = self._latest_version_by_dataset_id(conn, dataset["id"], allow_missing=True)
        if latest_version is None:
            return None
        current_schema = self._schema_for_version(dataset["id"], latest_version["schema_version"])["schema_json"]
        current_columns = self._schema_columns_by_name(current_schema)
        next_columns = self._schema_columns_by_name(next_schema)
        return (
            self._missing_columns_error(current_columns, next_columns)
            or self._type_change_error(current_columns, next_columns)
            or self._missing_primary_key_error(dataset, next_columns)
        )

    def _schema_columns_by_name(self, schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {column["name"]: column for column in schema["columns"]}

    def _missing_columns_error(
        self,
        current_columns: dict[str, dict[str, Any]],
        next_columns: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        missing = sorted(set(current_columns) - set(next_columns))
        if not missing:
            return None
        return {"check": "schema_compatibility", "status": "failed", "missing_columns": missing}

    def _type_change_error(
        self,
        current_columns: dict[str, dict[str, Any]],
        next_columns: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        for name, column in current_columns.items():
            if name in next_columns and column["type"] != next_columns[name]["type"]:
                return {
                    "check": "schema_compatibility",
                    "status": "failed",
                    "column": name,
                    "from": column["type"],
                    "to": next_columns[name]["type"],
                }
        return None

    def _missing_primary_key_error(
        self,
        dataset: dict[str, Any],
        next_columns: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        for pk in dataset["primary_key"]:
            if pk not in next_columns:
                return {"check": "schema_compatibility", "status": "failed", "missing_primary_key": pk}
        return None

    def _run_dataset_checks(
        self,
        conn: Connection,
        ctx: RequestContext,
        dataset: dict[str, Any],
        parquet_path: Path,
        row_count: int,
        run_id: str,
        transaction_id: str,
        *,
        extra_checks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        checks = [{"type": "row_count_min", "min": 1}]
        for pk in dataset["primary_key"]:
            checks.append({"type": "not_null", "columns": [pk]})
            checks.append({"type": "unique", "column": pk})
        checks.extend(extra_checks)
        failures: list[dict[str, Any]] = []
        for check in checks:
            check_id = self._ensure_dataset_check(conn, ctx, dataset, check)
            result = self._execute_check(parquet_path, row_count, check)
            conn.execute(
                insert(db.dataset_check_results).values(
                    id=_new_id("check_result"),
                    tenant_id=ctx.tenant_id,
                    check_id=check_id,
                    run_id=run_id,
                    transaction_id=transaction_id,
                    status=result["status"],
                    details=result,
                    created_at=_now(),
                )
            )
            if result["status"] == "failed":
                failures.append(result)
        return failures

    def _ensure_dataset_check(
        self,
        conn: Connection,
        ctx: RequestContext,
        dataset: dict[str, Any],
        check: dict[str, Any],
    ) -> str:
        name = json.dumps(check, sort_keys=True)
        row = (
            conn.execute(
                select(db.dataset_checks).where(
                    and_(
                        db.dataset_checks.c.tenant_id == ctx.tenant_id,
                        db.dataset_checks.c.dataset_id == dataset["id"],
                        db.dataset_checks.c.name == name,
                    )
                )
            )
            .mappings()
            .first()
        )
        if row:
            return row["id"]
        check_id = _new_id("check")
        conn.execute(
            insert(db.dataset_checks).values(
                id=check_id,
                tenant_id=ctx.tenant_id,
                dataset_id=dataset["id"],
                name=name,
                check_type=check["type"],
                config=check,
                severity="error",
                enabled=True,
            )
        )
        return check_id

    def _execute_check(
        self,
        parquet_path: Path,
        row_count: int,
        check: dict[str, Any],
    ) -> dict[str, Any]:
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
