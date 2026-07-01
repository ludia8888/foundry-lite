"""Application service helpers for quality workflows."""

from __future__ import annotations

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
    DatasetSchemaReference,
)
from foundry_lite.application.primitives import StagedFileStats, _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.protocols import DatasetRuntimeBoundary, DatasetVersionLookup
from foundry_lite.application.services.dataset.quality_api import DatasetQualityApiMixin
from foundry_lite.application.services.dataset.quality_helpers import (
    DEFAULT_QUALITY_CONTRACT_KEY,
    DatasetQualityRunContext,
    _allows_empty_dataset,
    _check_name,
    _check_severity,
    _contract_snapshot_configs,
    _failed_row_indexes,
    _quality_policy_status,
    _quality_result_details,
    _quarantine_dead_letter_record,
    _unique_checks,
    _validate_dataset_check_config,
)
from foundry_lite.application.services.dataset.schema_evolution import (
    DatasetSchemaEvolutionResult,
    build_schema_evolution_result,
)
from foundry_lite.domain.context import RequestContext


class DatasetQualityService(DatasetQualityApiMixin, CoreService):
    required_dependencies = (
        "engine",
        "policy",
        "compute_adapter",
        "dataset_quality_repository",
        "dataset_transaction_repository",
    )
    required_collaborators = ("dataset_version_service", "runtime_service")
    dataset_version_service: DatasetVersionLookup
    runtime_service: DatasetRuntimeBoundary

    def _inspect_parquet(self, parquet_path: Path, primary_key: list[str]) -> StagedFileStats:
        return self.compute_adapter.inspect_parquet(parquet_path, primary_key)

    def _ensure_schema(
        self,
        conn: TransactionContext,
        dataset: DatasetRow,
        schema_json: DatasetSchemaJson,
        schema_hash: str,
    ) -> int:
        return self._ensure_schema_reference(conn, dataset, schema_json, schema_hash).version

    def _ensure_schema_reference(
        self,
        conn: TransactionContext,
        dataset: DatasetRow,
        schema_json: DatasetSchemaJson,
        schema_hash: str,
    ) -> DatasetSchemaReference:
        dataset_id = str(dataset["id"])
        existing = self.dataset_quality_repository.schema_by_hash(
            transaction=conn,
            dataset_id=dataset_id,
            schema_hash=schema_hash,
        )
        if existing:
            return DatasetSchemaReference(schema_id=existing["id"], version=int(existing["version"]))
        latest = self.dataset_quality_repository.latest_schema_version(
            transaction=conn,
            dataset_id=dataset_id,
        )
        version = (latest or 0) + 1
        schema_id = _new_id("schema")
        self.dataset_quality_repository.insert_schema(
            transaction=conn,
            record=DatasetSchemaRecord(
                schema_id=schema_id,
                dataset_id=dataset_id,
                version=version,
                schema_json=schema_json,
                schema_hash=schema_hash,
                created_at=_now(),
            ),
        )
        return DatasetSchemaReference(schema_id=schema_id, version=version)

    def _schema_evolution_result(
        self,
        conn: TransactionContext,
        dataset: DatasetRow,
        next_schema: DatasetSchemaJson,
        target_schema: DatasetSchemaReference,
    ) -> DatasetSchemaEvolutionResult:
        dataset_id = str(dataset["id"])
        latest_version = self.dataset_version_service._latest_version_by_dataset_id(
            conn, dataset_id, allow_missing=True
        )
        if latest_version is None:
            return DatasetSchemaEvolutionResult(failure=None, metadata=None)
        current_schema = self.dataset_version_service._schema_for_version(dataset_id, latest_version["schema_version"])
        return build_schema_evolution_result(
            dataset_id=dataset_id,
            current_schema=current_schema,
            next_schema=next_schema,
            primary_key=list(dataset["primary_key"]),
            target_schema=target_schema,
        )

    def _schema_compatibility_error(
        self,
        conn: TransactionContext,
        dataset: DatasetRow,
        next_schema: DatasetSchemaJson,
    ) -> DatasetCheckResult | None:
        target_schema = DatasetSchemaReference(schema_id="", version=0)
        return self._schema_evolution_result(conn, dataset, next_schema, target_schema).failure

    def _checks_for_dataset(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
        extra_checks: Sequence[DatasetCheckConfig],
        contract_checks: Sequence[DatasetCheckConfig] | None = None,
    ) -> list[DatasetCheckConfig]:
        if contract_checks is None:
            persisted_checks = self._enabled_contract_check_configs(conn, ctx, dataset)
        else:
            persisted_checks = list(contract_checks)
        candidate_checks = [*persisted_checks, *extra_checks]
        for check in candidate_checks:
            _validate_dataset_check_config(check)
        checks: list[DatasetCheckConfig] = []
        if not _allows_empty_dataset(candidate_checks):
            checks.append({"type": "row_count_min", "min": 1})
        primary_key = list(dataset["primary_key"])
        for pk in primary_key:
            checks.append({"type": "not_null", "columns": [pk]})
        if len(primary_key) == 1:
            checks.append({"type": "unique", "column": primary_key[0]})
        elif len(primary_key) > 1:
            checks.append({"type": "unique_tuple", "columns": primary_key})
        checks.extend(check for check in candidate_checks if check.get("type") != "allow_empty")
        return _unique_checks(checks)

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
        checked_manifest_hash: str,
        validated_against_schema_version_id: str,
        validated_against_schema_version: int,
    ) -> list[DatasetCheckResult]:
        active_id, contract_checks, snapshot_ids = self._active_contract_check_context(conn, ctx, dataset)
        quality_ctx = DatasetQualityRunContext(
            conn=conn,
            request_ctx=ctx,
            dataset=dataset,
            parquet_path=parquet_path,
            row_count=row_count,
            run_id=run_id,
            transaction_id=transaction_id,
            checked_manifest_hash=checked_manifest_hash,
            schema_version_id=validated_against_schema_version_id,
            schema_version=validated_against_schema_version,
            active_contract_version_id=active_id,
        )
        failures: list[DatasetCheckResult] = []
        for check in self._checks_for_dataset(conn, ctx, dataset, extra_checks, contract_checks):
            result, policy_status = self._run_dataset_check(
                quality_ctx,
                check,
                check_id=snapshot_ids.get(_check_name(check)),
            )
            if policy_status == "BLOCK_COMMIT":
                failures.append(result)
        return failures

    def _active_contract_check_context(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
    ) -> tuple[str | None, list[DatasetCheckConfig] | None, dict[str, str]]:
        active_contract = self.dataset_quality_repository.active_contract_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            dataset_id=str(dataset["id"]),
            contract_key=DEFAULT_QUALITY_CONTRACT_KEY,
        )
        if active_contract is None:
            return None, None, {}
        return (
            active_contract["id"],
            _contract_snapshot_configs(active_contract),
            _snapshot_check_ids(active_contract),
        )

    def _run_dataset_check(
        self,
        quality_ctx: DatasetQualityRunContext,
        check: DatasetCheckConfig,
        check_id: str | None = None,
    ) -> tuple[DatasetCheckResult, str]:
        check_id = check_id or self._ensure_dataset_check(
            quality_ctx.conn, quality_ctx.request_ctx, quality_ctx.dataset, check
        )
        result = self._execute_check(quality_ctx.parquet_path, quality_ctx.row_count, check)
        policy_status = _quality_policy_status(result, check)
        if policy_status == "QUARANTINE":
            result = self._quarantine_failed_rows(quality_ctx, check, result)
        self._insert_check_result(quality_ctx, check_id, result, policy_status)
        return result, policy_status

    def _insert_check_result(
        self,
        quality_ctx: DatasetQualityRunContext,
        check_id: str,
        result: DatasetCheckResult,
        policy_status: str,
    ) -> None:
        self.dataset_quality_repository.insert_check_result(
            transaction=quality_ctx.conn,
            record=DatasetCheckResultRecord(
                check_result_id=_new_id("check_result"),
                tenant_id=quality_ctx.request_ctx.tenant_id,
                check_id=check_id,
                run_id=quality_ctx.run_id,
                transaction_id=quality_ctx.transaction_id,
                checked_manifest_hash=quality_ctx.checked_manifest_hash,
                validated_against_schema_version_id=quality_ctx.schema_version_id,
                validated_against_schema_version=quality_ctx.schema_version,
                status=policy_status,
                details=_quality_result_details(result, policy_status),
                created_at=_now(),
                data_contract_version_id=quality_ctx.active_contract_version_id,
            ),
        )

    def _ensure_dataset_check(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
        check: DatasetCheckConfig,
    ) -> str:
        dataset_id = str(dataset["id"])
        name = _check_name(check)
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
                severity=_check_severity(check),
                enabled=True,
            ),
        )
        persisted = self.dataset_quality_repository.check_by_name(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            dataset_id=dataset_id,
            name=name,
        )
        if persisted is None:
            raise RuntimeError("dataset check insert had no persisted winner")
        return persisted["id"]

    def _enabled_contract_check_configs(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset: DatasetRow,
    ) -> list[DatasetCheckConfig]:
        rows = self.dataset_quality_repository.checks_for_dataset(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            dataset_id=str(dataset["id"]),
        )
        return [row["config"] for row in rows if row["enabled"]]

    def _execute_check(
        self,
        parquet_path: Path,
        row_count: int,
        check: DatasetCheckConfig,
    ) -> DatasetCheckResult:
        return self.compute_adapter.execute_check(parquet_path, row_count, check)

    def _quarantine_failed_rows(
        self,
        quality_ctx: DatasetQualityRunContext,
        check: DatasetCheckConfig,
        result: DatasetCheckResult,
    ) -> DatasetCheckResult:
        rows = self.compute_adapter.rows_from_parquet(quality_ctx.parquet_path)
        failed_indexes = _failed_row_indexes(rows, check)
        if not rows or not failed_indexes:
            return {**result, "quarantined_rows": 0}
        failed_set = set(failed_indexes)
        for index in failed_indexes:
            self._insert_quarantine_record(
                quality_ctx,
                rows[index],
                index,
                check,
                result,
            )
        clean_rows = [row for index, row in enumerate(rows) if index not in failed_set]
        self.compute_adapter.rows_to_parquet(clean_rows, quality_ctx.parquet_path, list(rows[0]))
        return {**result, "quarantined_rows": len(failed_indexes)}

    def _insert_quarantine_record(
        self,
        quality_ctx: DatasetQualityRunContext,
        row: Mapping[str, object],
        row_index: int,
        check: DatasetCheckConfig,
        result: DatasetCheckResult,
    ) -> None:
        record, record_id = _quarantine_dead_letter_record(quality_ctx, row, row_index, check, result)
        inserted = self.dataset_transaction_repository.insert_dead_letter_record(
            transaction=quality_ctx.conn,
            record=record,
        )
        if inserted:
            self._audit_quarantine_record(
                quality_ctx.conn,
                quality_ctx.request_ctx,
                record_id,
                quality_ctx.run_id,
                row_index,
                result,
            )

    def _audit_quarantine_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        record_id: str,
        run_id: str,
        row_index: int,
        result: DatasetCheckResult,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="dead_letter_record.quarantined",
            resource_type="dead_letter_record",
            resource_id=record_id,
            action="data_quality_quarantine",
            after_ref={"rowIndex": row_index, "check": result.get("check")},
            correlation_id=run_id,
        )


def _snapshot_check_ids(row: object) -> dict[str, str]:
    if not isinstance(row, dict):
        return {}
    snapshots = row.get("checks_snapshot")
    if not isinstance(snapshots, list):
        return {}
    return {
        _check_name(snapshot["config"]): str(snapshot["checkId"])
        for snapshot in snapshots
        if isinstance(snapshot, dict) and snapshot.get("enabled") and isinstance(snapshot.get("config"), Mapping)
    }
