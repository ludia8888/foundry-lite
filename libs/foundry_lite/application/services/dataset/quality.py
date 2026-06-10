from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from foundry_lite.application.ports.dataset_quality_repository import (
    DatasetCheckRecord,
    DatasetCheckResultRecord,
    DatasetSchemaRecord,
)
from foundry_lite.application.primitives import StagedFileStats, _new_id, _now
from foundry_lite.application.services.base import CoreServiceMixin
from foundry_lite.domain.context import RequestContext


class DatasetQualityMixin(CoreServiceMixin):
    def _inspect_parquet(self, parquet_path: Path, primary_key: list[str]) -> StagedFileStats:
        return self.compute_adapter.inspect_parquet(parquet_path, primary_key)

    def _ensure_schema(
        self,
        conn: Any,
        dataset: dict[str, Any],
        schema_json: dict[str, Any],
        schema_hash: str,
    ) -> int:
        existing = self.dataset_quality_repository.schema_by_hash(
            transaction=conn,
            dataset_id=dataset["id"],
            schema_hash=schema_hash,
        )
        if existing:
            return int(existing["version"])
        latest = self.dataset_quality_repository.latest_schema_version(
            transaction=conn,
            dataset_id=dataset["id"],
        )
        version = (latest or 0) + 1
        self.dataset_quality_repository.insert_schema(
            transaction=conn,
            record=DatasetSchemaRecord(
                schema_id=_new_id("schema"),
                dataset_id=dataset["id"],
                version=version,
                schema_json=schema_json,
                schema_hash=schema_hash,
                created_at=_now(),
            ),
        )
        return version

    def _schema_compatibility_error(
        self,
        conn: Any,
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
        conn: Any,
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
            self.dataset_quality_repository.insert_check_result(
                transaction=conn,
                record=DatasetCheckResultRecord(
                    check_result_id=_new_id("check_result"),
                    tenant_id=ctx.tenant_id,
                    check_id=check_id,
                    run_id=run_id,
                    transaction_id=transaction_id,
                    status=result["status"],
                    details=result,
                    created_at=_now(),
                ),
            )
            if result["status"] == "failed":
                failures.append(result)
        return failures

    def _ensure_dataset_check(
        self,
        conn: Any,
        ctx: RequestContext,
        dataset: dict[str, Any],
        check: dict[str, Any],
    ) -> str:
        name = json.dumps(check, sort_keys=True)
        row = self.dataset_quality_repository.check_by_name(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            dataset_id=dataset["id"],
            name=name,
        )
        if row:
            return row["id"]
        check_id = _new_id("check")
        self.dataset_quality_repository.insert_check(
            transaction=conn,
            record=DatasetCheckRecord(
                check_id=check_id,
                tenant_id=ctx.tenant_id,
                dataset_id=dataset["id"],
                name=name,
                check_type=check["type"],
                config=check,
                severity="error",
                enabled=True,
            ),
        )
        return check_id

    def _execute_check(
        self,
        parquet_path: Path,
        row_count: int,
        check: dict[str, Any],
    ) -> dict[str, Any]:
        return self.compute_adapter.execute_check(parquet_path, row_count, check)
