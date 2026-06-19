from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from foundry_lite.application.ports import DatasetRow, DeadLetterRecord, TransactionContext
from foundry_lite.application.ports.dataset_quality_repository import (
    DatasetCheckConfig,
    DatasetCheckRecord,
    DatasetCheckResult,
    DatasetCheckResultRecord,
    DatasetSchemaJson,
    DatasetSchemaRecord,
    DatasetSchemaReference,
)
from foundry_lite.application.primitives import StagedFileStats, _json_hash, _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.protocols import DatasetRuntimeBoundary, DatasetVersionLookup
from foundry_lite.application.services.dataset.schema_evolution import (
    DatasetSchemaEvolutionResult,
    build_schema_evolution_result,
)
from foundry_lite.domain.context import RequestContext


@dataclass(frozen=True)
class DatasetQualityRunContext:
    conn: TransactionContext
    request_ctx: RequestContext
    dataset: DatasetRow
    parquet_path: Path
    row_count: int
    run_id: str
    transaction_id: str
    checked_manifest_hash: str
    schema_version_id: str
    schema_version: int


class DatasetQualityService(CoreService):
    required_dependencies = ("compute_adapter", "dataset_quality_repository", "dataset_transaction_repository")
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
        dataset: DatasetRow,
        extra_checks: Sequence[DatasetCheckConfig],
    ) -> list[DatasetCheckConfig]:
        checks: list[DatasetCheckConfig] = []
        if not _allows_empty_dataset(extra_checks):
            checks.append({"type": "row_count_min", "min": 1})
        for pk in dataset["primary_key"]:
            checks.append({"type": "not_null", "columns": [pk]})
            checks.append({"type": "unique", "column": pk})
        checks.extend(check for check in extra_checks if check.get("type") != "allow_empty")
        return checks

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
        for check in self._checks_for_dataset(dataset, extra_checks):
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
                severity=_check_severity(check),
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


def _allows_empty_dataset(extra_checks: Sequence[DatasetCheckConfig]) -> bool:
    return any(check.get("type") == "allow_empty" for check in extra_checks)


def _quality_policy_status(result: DatasetCheckResult, check: DatasetCheckConfig) -> str:
    if str(result.get("status")) != "failed":
        return "PASS"
    severity = _check_severity(check)
    if severity in {"warn", "warning"}:
        return "WARN"
    if severity in {"quarantine", "quarantined"} and _is_row_quarantine_check(check):
        return "QUARANTINE"
    return "BLOCK_COMMIT"


def _quality_result_details(result: DatasetCheckResult, policy_status: str) -> DatasetCheckResult:
    return {**result, "contract_status": policy_status}


def _check_severity(check: DatasetCheckConfig) -> str:
    return str(check.get("severity", "error")).lower()


def _is_row_quarantine_check(check: DatasetCheckConfig) -> bool:
    return str(check.get("type")) in {"not_null", "unique"}


def _failed_row_indexes(rows: Sequence[Mapping[str, object]], check: DatasetCheckConfig) -> list[int]:
    check_type = str(check.get("type"))
    if check_type == "not_null":
        return _not_null_failed_indexes(rows, check)
    if check_type == "unique":
        return _unique_failed_indexes(rows, check)
    return []


def _not_null_failed_indexes(rows: Sequence[Mapping[str, object]], check: DatasetCheckConfig) -> list[int]:
    columns = _string_list(check.get("columns"))
    return [index for index, row in enumerate(rows) if any(row.get(column) is None for column in columns)]


def _unique_failed_indexes(rows: Sequence[Mapping[str, object]], check: DatasetCheckConfig) -> list[int]:
    column = str(check.get("column"))
    counts: dict[str, int] = {}
    keys = [_json_hash({"value": row.get(column)}) for row in rows]
    for key in keys:
        counts[key] = counts.get(key, 0) + 1
    return [index for index, key in enumerate(keys) if counts[key] > 1]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [item for item in value if isinstance(item, str)]


def _quarantine_dead_letter_record(
    quality_ctx: DatasetQualityRunContext,
    row: Mapping[str, object],
    row_index: int,
    check: DatasetCheckConfig,
    result: DatasetCheckResult,
) -> tuple[DeadLetterRecord, str]:
    now = _now()
    payload_hash = _json_hash(row)
    record_id = _new_id("dlqr")
    return (
        DeadLetterRecord(
            dead_letter_record_id=record_id,
            tenant_id=quality_ctx.request_ctx.tenant_id,
            source_event_id=f"quality:{quality_ctx.transaction_id}:{row_index}:{payload_hash[:12]}",
            source_dataset_version_id=None,
            source_run_id=quality_ctx.run_id,
            payload=dict(row),
            payload_hash=payload_hash,
            schema_version=quality_ctx.schema_version,
            transform_version=None,
            error_kind="DATA_QUALITY_CONTRACT",
            error_message=_quality_error_message(result),
            event_time=None,
            ingested_at=now,
            first_failed_at=now,
            attempts=1,
            status="QUARANTINED",
            replay_status="NOT_REQUESTED",
            replay_run_id=None,
            is_closed_partition_affected=False,
            metadata=_quarantine_metadata(quality_ctx, row_index, check, result),
        ),
        record_id,
    )


def _quarantine_metadata(
    quality_ctx: DatasetQualityRunContext,
    row_index: int,
    check: DatasetCheckConfig,
    result: DatasetCheckResult,
) -> dict[str, object]:
    return {
        "recordDlqKind": "data_quality_contract",
        "datasetId": quality_ctx.dataset["id"],
        "datasetRef": f"{quality_ctx.dataset['namespace']}.{quality_ctx.dataset['name']}",
        "transactionId": quality_ctx.transaction_id,
        "rowIndex": row_index,
        "check": dict(check),
        "result": dict(result),
        "checkedManifestHash": quality_ctx.checked_manifest_hash,
        "validatedAgainstSchemaVersionId": quality_ctx.schema_version_id,
        "validatedAgainstSchemaVersion": quality_ctx.schema_version,
    }


def _quality_error_message(result: DatasetCheckResult) -> str:
    return f"dataset quality check failed: {result.get('check', 'unknown')}"
