from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from foundry_lite.application.ports import DatasetRow, TransactionContext
from foundry_lite.application.ports.dataset_quality_repository import (
    DatasetCheckConfig,
    DatasetCheckRecord,
    DatasetCheckResult,
    DatasetCheckResultRecord,
    DatasetSchemaJson,
    DatasetSchemaRecord,
)
from foundry_lite.application.primitives import StagedFileStats, _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.protocols import DatasetVersionLookup
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

DatasetSchemaColumn = Mapping[str, object]
DatasetColumnsByName = dict[str, DatasetSchemaColumn]


class DatasetQualityService(CoreService):
    required_dependencies = ("compute_adapter", "dataset_quality_repository")
    required_collaborators = ("dataset_version_service",)
    dataset_version_service: DatasetVersionLookup

    def _inspect_parquet(self, parquet_path: Path, primary_key: list[str]) -> StagedFileStats:
        return self.compute_adapter.inspect_parquet(parquet_path, primary_key)

    def _ensure_schema(
        self,
        conn: TransactionContext,
        dataset: DatasetRow,
        schema_json: DatasetSchemaJson,
        schema_hash: str,
    ) -> int:
        dataset_id = str(dataset["id"])
        existing = self.dataset_quality_repository.schema_by_hash(
            transaction=conn,
            dataset_id=dataset_id,
            schema_hash=schema_hash,
        )
        if existing:
            return int(existing["version"])
        latest = self.dataset_quality_repository.latest_schema_version(
            transaction=conn,
            dataset_id=dataset_id,
        )
        version = (latest or 0) + 1
        self.dataset_quality_repository.insert_schema(
            transaction=conn,
            record=DatasetSchemaRecord(
                schema_id=_new_id("schema"),
                dataset_id=dataset_id,
                version=version,
                schema_json=schema_json,
                schema_hash=schema_hash,
                created_at=_now(),
            ),
        )
        return version

    def _schema_compatibility_error(
        self,
        conn: TransactionContext,
        dataset: DatasetRow,
        next_schema: DatasetSchemaJson,
    ) -> DatasetCheckResult | None:
        dataset_id = str(dataset["id"])
        latest_version = self.dataset_version_service._latest_version_by_dataset_id(
            conn, dataset_id, allow_missing=True
        )
        if latest_version is None:
            return None
        current_schema = self.dataset_version_service._schema_for_version(dataset_id, latest_version["schema_version"])[
            "schema_json"
        ]
        current_columns = self._schema_columns_by_name(current_schema)
        next_columns = self._schema_columns_by_name(next_schema)
        return (
            self._missing_columns_error(current_columns, next_columns)
            or self._type_change_error(current_columns, next_columns)
            or self._missing_primary_key_error(dataset, next_columns)
        )

    def _schema_columns_by_name(self, schema: DatasetSchemaJson) -> DatasetColumnsByName:
        columns = schema.get("columns")
        if not isinstance(columns, Sequence) or isinstance(columns, str | bytes):
            raise ValidationFailed("dataset schema columns must be a list")
        result: DatasetColumnsByName = {}
        for index, column in enumerate(columns):
            column_map = self._schema_column(column, index)
            result[self._column_name(column_map, index)] = column_map
        return result

    def _schema_column(self, value: object, index: int) -> dict[str, object]:
        if not isinstance(value, Mapping):
            raise ValidationFailed("dataset schema column must be a mapping", details={"index": index})
        result: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise ValidationFailed("dataset schema column keys must be strings", details={"index": index})
            result[raw_key] = raw_value
        return result

    def _column_name(self, column: Mapping[str, object], index: int) -> str:
        name = column.get("name")
        if not isinstance(name, str) or not name:
            raise ValidationFailed("dataset schema column name must be a non-empty string", details={"index": index})
        return name

    def _missing_columns_error(
        self,
        current_columns: DatasetColumnsByName,
        next_columns: DatasetColumnsByName,
    ) -> DatasetCheckResult | None:
        missing = sorted(set(current_columns) - set(next_columns))
        if not missing:
            return None
        return {"check": "schema_compatibility", "status": "failed", "missing_columns": missing}

    def _type_change_error(
        self,
        current_columns: DatasetColumnsByName,
        next_columns: DatasetColumnsByName,
    ) -> DatasetCheckResult | None:
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
        dataset: DatasetRow,
        next_columns: DatasetColumnsByName,
    ) -> DatasetCheckResult | None:
        for pk in dataset["primary_key"]:
            if pk not in next_columns:
                return {"check": "schema_compatibility", "status": "failed", "missing_primary_key": pk}
        return None

    def _run_dataset_checks(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
        parquet_path: Path,
        row_count: int,
        run_id: str,
        transaction_id: str,
        *,
        extra_checks: Sequence[DatasetCheckConfig],
    ) -> list[DatasetCheckResult]:
        checks: list[DatasetCheckConfig] = [{"type": "row_count_min", "min": 1}]
        for pk in dataset["primary_key"]:
            checks.append({"type": "not_null", "columns": [pk]})
            checks.append({"type": "unique", "column": pk})
        checks.extend(extra_checks)
        failures: list[DatasetCheckResult] = []
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
                    status=str(result["status"]),
                    details=result,
                    created_at=_now(),
                ),
            )
            if str(result["status"]) == "failed":
                failures.append(result)
        return failures

    def _ensure_dataset_check(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
        check: DatasetCheckConfig,
    ) -> str:
        dataset_id = str(dataset["id"])
        name = json.dumps(check, sort_keys=True)
        row = self.dataset_quality_repository.check_by_name(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            dataset_id=dataset_id,
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
                dataset_id=dataset_id,
                name=name,
                check_type=str(check["type"]),
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
        check: DatasetCheckConfig,
    ) -> DatasetCheckResult:
        return self.compute_adapter.execute_check(parquet_path, row_count, check)
