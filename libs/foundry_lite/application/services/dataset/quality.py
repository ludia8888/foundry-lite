from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from foundry_lite.application.ports import DatasetRow, TransactionContext
from foundry_lite.application.ports.dataset_quality_repository import (
    DatasetCheckConfig,
    DatasetCheckRecord,
    DatasetCheckResult,
    DatasetCheckResultRecord,
    DatasetQualityContractCheck,
    DatasetQualityContractCheckCreateResult,
    DatasetQualityContractCheckList,
    DatasetQualityResultHistory,
    DatasetQualityResultSummary,
    DatasetSchemaJson,
    DatasetSchemaRecord,
    DatasetSchemaReference,
)
from foundry_lite.application.primitives import StagedFileStats, _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.protocols import DatasetRuntimeBoundary, DatasetVersionLookup
from foundry_lite.application.services.dataset.quality_contracts import DatasetQualityContractMixin
from foundry_lite.application.services.dataset.quality_helpers import (
    DatasetQualityRunContext,
    _allows_empty_dataset,
    _check_name,
    _check_severity,
    _contract_check,
    _contract_check_config,
    _contract_check_record,
    _dataset_ref,
    _failed_row_indexes,
    _quality_policy_status,
    _quality_result_details,
    _quality_result_history_item,
    _quality_result_summary,
    _quarantine_dead_letter_record,
    _unique_checks,
    _updated_contract_check_record,
    _validate_dataset_check_config,
)
from foundry_lite.application.services.dataset.schema_evolution import (
    DatasetSchemaEvolutionResult,
    build_schema_evolution_result,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed


class DatasetQualityService(DatasetQualityContractMixin, CoreService):
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

    def list_contract_checks(
        self,
        dataset: DatasetRow,
        *,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityContractCheckList:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "dataset:read")
        with self.engine.begin() as conn:
            rows = self.dataset_quality_repository.checks_for_dataset(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                dataset_id=str(dataset["id"]),
            )
        return {"datasetRef": _dataset_ref(dataset), "checks": [_contract_check(row) for row in rows]}

    def list_quality_results(
        self,
        dataset: DatasetRow,
        *,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityResultHistory:
        ctx = ctx or RequestContext()
        if limit < 1 or limit > 100:
            raise ValidationFailed("quality result history limit must be between 1 and 100")
        self.policy.require(ctx, "dataset:read")
        with self.engine.begin() as conn:
            rows = self.dataset_quality_repository.check_results_for_dataset(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                dataset_id=str(dataset["id"]),
                limit=limit,
            )
        return {
            "datasetRef": _dataset_ref(dataset),
            "results": [_quality_result_history_item(row) for row in rows],
        }

    def get_quality_result_summary(
        self,
        dataset: DatasetRow,
        *,
        latest_limit: int = 5,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityResultSummary:
        ctx = ctx or RequestContext()
        if latest_limit < 1 or latest_limit > 20:
            raise ValidationFailed("quality result summary latest limit must be between 1 and 20")
        self.policy.require(ctx, "dataset:read")
        dataset_id = str(dataset["id"])
        with self.engine.begin() as conn:
            status_rows = self.dataset_quality_repository.check_result_status_counts_for_dataset(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                dataset_id=dataset_id,
            )
            type_rows = self.dataset_quality_repository.check_result_type_status_counts_for_dataset(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                dataset_id=dataset_id,
            )
            latest_rows = self.dataset_quality_repository.check_results_for_dataset(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                dataset_id=dataset_id,
                limit=latest_limit,
            )
        return _quality_result_summary(dataset, status_rows, type_rows, latest_rows)

    def create_contract_check(
        self,
        dataset: DatasetRow,
        *,
        config: DatasetCheckConfig,
        severity: str | None = None,
        enabled: bool = True,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityContractCheckCreateResult:
        ctx = ctx or RequestContext()
        dataset_id = str(dataset["id"])
        if config is None and severity is None and enabled is None:
            raise ValidationFailed("quality contract check update requires a change")
        self.runtime_service._require_or_audit(ctx, "dataset:write", "dataset", dataset_id)
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="create_quality_contract_check",
            resource_type="dataset",
            resource_id=dataset_id,
        )
        contract_config = _contract_check_config(config, severity)
        check_name = _check_name(contract_config)
        with self.engine.begin() as conn:
            existing = self.dataset_quality_repository.check_by_name(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                dataset_id=dataset_id,
                name=check_name,
            )
            if existing is not None:
                return {"check": _contract_check(existing), "isIdempotentReplay": True}
            record = _contract_check_record(ctx, dataset_id, check_name, contract_config, enabled)
            self.dataset_quality_repository.insert_check(transaction=conn, record=record)
            persisted = self._required_contract_check(conn, ctx, dataset_id, check_name)
            self._audit_contract_check_created(conn, ctx, dataset, persisted)
            return {"check": _contract_check(persisted), "isIdempotentReplay": False}

    def update_contract_check(
        self,
        dataset: DatasetRow,
        check_id: str,
        *,
        config: DatasetCheckConfig | None = None,
        severity: str | None = None,
        enabled: bool | None = None,
        ctx: RequestContext | None = None,
    ) -> DatasetQualityContractCheck:
        ctx = ctx or RequestContext()
        dataset_id = str(dataset["id"])
        self.runtime_service._require_or_audit(ctx, "dataset:write", "dataset", dataset_id)
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="update_quality_contract_check",
            resource_type="dataset",
            resource_id=dataset_id,
        )
        with self.engine.begin() as conn:
            before = self._required_contract_check_by_id(conn, ctx, dataset_id, check_id)
            record = _updated_contract_check_record(before, config=config, severity=severity, enabled=enabled)
            self._ensure_no_contract_check_name_conflict(conn, ctx, dataset_id, record.name, check_id)
            if not self.dataset_quality_repository.update_check(transaction=conn, record=record):
                raise NotFound("dataset quality contract check not found", details={"check_id": check_id})
            after = self._required_contract_check_by_id(conn, ctx, dataset_id, check_id)
            self._audit_contract_check_updated(conn, ctx, dataset, before, after)
            return _contract_check(after)

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
    ) -> list[DatasetCheckConfig]:
        persisted_checks = self._enabled_contract_check_configs(conn, ctx, dataset)
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
        )
        failures: list[DatasetCheckResult] = []
        for check in self._checks_for_dataset(conn, ctx, dataset, extra_checks):
            result, policy_status = self._run_dataset_check(quality_ctx, check)
            if policy_status == "BLOCK_COMMIT":
                failures.append(result)
        return failures

    def _run_dataset_check(
        self,
        quality_ctx: DatasetQualityRunContext,
        check: DatasetCheckConfig,
    ) -> tuple[DatasetCheckResult, str]:
        check_id = self._ensure_dataset_check(quality_ctx.conn, quality_ctx.request_ctx, quality_ctx.dataset, check)
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
