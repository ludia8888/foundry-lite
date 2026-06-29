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
    DatasetCheckResultHistoryRow,
    DatasetCheckResultStatusCountRow,
    DatasetCheckResultTypeStatusCountRow,
    DatasetCheckRow,
    DatasetQualityContractCheck,
    DatasetQualityResultHistoryItem,
    DatasetQualityResultStatusCount,
    DatasetQualityResultSummary,
    DatasetQualityResultTypeStatusCount,
)
from foundry_lite.application.primitives import _json_hash, _new_id, _now
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

SUPPORTED_DATASET_CHECK_TYPES = frozenset({"allow_empty", "row_count_min", "not_null", "unique", "unique_tuple"})


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


def _allows_empty_dataset(extra_checks: Sequence[DatasetCheckConfig]) -> bool:
    return any(check.get("type") == "allow_empty" for check in extra_checks)


def _dataset_ref(dataset: DatasetRow) -> str:
    return f"{dataset['namespace']}.{dataset['name']}"


def _contract_check_config(config: DatasetCheckConfig, severity: str | None) -> DatasetCheckConfig:
    candidate = dict(config)
    if severity is not None:
        candidate["severity"] = severity
    _validate_dataset_check_config(candidate)
    return candidate


def _contract_check_record(
    ctx: RequestContext,
    dataset_id: str,
    check_name: str,
    config: DatasetCheckConfig,
    enabled: bool,
) -> DatasetCheckRecord:
    return DatasetCheckRecord(
        check_id=_new_id("check"),
        tenant_id=ctx.tenant_id,
        dataset_id=dataset_id,
        name=check_name,
        check_type=str(config["type"]),
        config=config,
        severity=_check_severity(config),
        enabled=enabled,
    )


def _updated_contract_check_record(
    row: DatasetCheckRow,
    *,
    config: DatasetCheckConfig | None,
    severity: str | None,
    enabled: bool | None,
) -> DatasetCheckRecord:
    next_config = _updated_contract_check_config(row, config, severity)
    return DatasetCheckRecord(
        check_id=row["id"],
        tenant_id=row["tenant_id"],
        dataset_id=row["dataset_id"],
        name=_check_name(next_config),
        check_type=str(next_config["type"]),
        config=next_config,
        severity=_check_severity(next_config),
        enabled=row["enabled"] if enabled is None else enabled,
    )


def _updated_contract_check_config(
    row: DatasetCheckRow,
    config: DatasetCheckConfig | None,
    severity: str | None,
) -> DatasetCheckConfig:
    base = dict(row["config"] if config is None else config)
    if severity is not None:
        base["severity"] = severity
    elif config is not None and "severity" not in base:
        base["severity"] = row["severity"]
    _validate_dataset_check_config(base)
    return base


def _contract_check(row: DatasetCheckRow) -> DatasetQualityContractCheck:
    return {
        "id": row["id"],
        "datasetId": row["dataset_id"],
        "name": row["name"],
        "checkType": row["check_type"],
        "config": row["config"],
        "severity": row["severity"],
        "enabled": row["enabled"],
    }


def _quality_result_history_item(row: DatasetCheckResultHistoryRow) -> DatasetQualityResultHistoryItem:
    return {
        "id": str(row["id"]),
        "checkId": str(row["check_id"]),
        "checkName": str(row["check_name"]),
        "checkType": str(row["check_type"]),
        "severity": str(row["severity"]),
        "runId": str(row["run_id"]),
        "transactionId": str(row["transaction_id"]),
        "checkedManifestHash": str(row["checked_manifest_hash"]),
        "validatedAgainstSchemaVersionId": str(row["validated_against_schema_version_id"]),
        "validatedAgainstSchemaVersion": int(row["validated_against_schema_version"]),
        "status": str(row["status"]),
        "details": dict(row["details"]) if isinstance(row["details"], Mapping) else {"value": row["details"]},
        "createdAt": str(row["created_at"]),
    }


def _quality_result_summary(
    dataset: DatasetRow,
    status_rows: Sequence[DatasetCheckResultStatusCountRow],
    type_rows: Sequence[DatasetCheckResultTypeStatusCountRow],
    latest_rows: Sequence[DatasetCheckResultHistoryRow],
) -> DatasetQualityResultSummary:
    status_counts = [_quality_result_status_count(row) for row in status_rows]
    return {
        "datasetRef": _dataset_ref(dataset),
        "totalResults": sum(row["count"] for row in status_counts),
        "statusCounts": status_counts,
        "checkTypeStatusCounts": [_quality_result_type_status_count(row) for row in type_rows],
        "latestResults": [_quality_result_history_item(row) for row in latest_rows],
    }


def _quality_result_status_count(row: DatasetCheckResultStatusCountRow) -> DatasetQualityResultStatusCount:
    return {"status": str(row["status"]), "count": int(row["count"])}


def _quality_result_type_status_count(
    row: DatasetCheckResultTypeStatusCountRow,
) -> DatasetQualityResultTypeStatusCount:
    return {"checkType": str(row["check_type"]), "status": str(row["status"]), "count": int(row["count"])}


def _contract_check_audit_ref(dataset: DatasetRow, check: DatasetCheckRow) -> dict[str, object]:
    return {
        "datasetId": dataset["id"],
        "datasetRef": _dataset_ref(dataset),
        "checkType": check["check_type"],
        "severity": check["severity"],
        "enabled": check["enabled"],
    }


def _unique_checks(checks: Sequence[DatasetCheckConfig]) -> list[DatasetCheckConfig]:
    unique: dict[str, DatasetCheckConfig] = {}
    for check in checks:
        unique.setdefault(_check_name(check), check)
    return list(unique.values())


def _check_name(check: DatasetCheckConfig) -> str:
    return json.dumps(check, sort_keys=True)


def _quality_policy_status(result: DatasetCheckResult, check: DatasetCheckConfig) -> str:
    if str(result.get("status")) != "failed":
        return "PASS"
    severity = _check_severity(check)
    if severity in {"warn", "warning"}:
        return "WARN"
    if severity in {"quarantine", "quarantined"} and _is_row_quarantine_check(check):
        return "QUARANTINE"
    return "BLOCK_COMMIT"


def _validate_dataset_check_config(check: DatasetCheckConfig) -> None:
    check_type = check.get("type")
    if check_type not in SUPPORTED_DATASET_CHECK_TYPES:
        raise ValidationFailed(
            "unsupported dataset quality check type",
            details={"check_type": str(check_type)},
        )


def _quality_result_details(result: DatasetCheckResult, policy_status: str) -> DatasetCheckResult:
    return {**result, "contract_status": policy_status}


def _check_severity(check: DatasetCheckConfig) -> str:
    return str(check.get("severity", "error")).lower()


def _is_row_quarantine_check(check: DatasetCheckConfig) -> bool:
    return str(check.get("type")) in {"not_null", "unique", "unique_tuple"}


def _failed_row_indexes(rows: Sequence[Mapping[str, object]], check: DatasetCheckConfig) -> list[int]:
    check_type = str(check.get("type"))
    if check_type == "not_null":
        return _not_null_failed_indexes(rows, check)
    if check_type == "unique":
        return _unique_failed_indexes(rows, check)
    if check_type == "unique_tuple":
        return _unique_tuple_failed_indexes(rows, check)
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


def _unique_tuple_failed_indexes(rows: Sequence[Mapping[str, object]], check: DatasetCheckConfig) -> list[int]:
    columns = _string_list(check.get("columns"))
    counts: dict[str, int] = {}
    keys = [_json_hash({column: row.get(column) for column in columns}) for row in rows]
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
