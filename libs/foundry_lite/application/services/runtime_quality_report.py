"""Build operator-facing quality summaries for Operations run detail payloads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import DatasetCheckResultRow, RuntimeJsonObject, RuntimeRow

QUALITY_STATUS_KEYS = ("PASS", "WARN", "QUARANTINE", "BLOCK_COMMIT")


def quality_report_for_transaction(
    dataset_transaction: RuntimeRow | None,
    check_results: Sequence[DatasetCheckResultRow],
    failed_row_samples: Sequence[RuntimeRow] = (),
) -> RuntimeJsonObject | None:
    """Return a compact quality report for one dataset transaction, when present."""
    if dataset_transaction is None or not check_results:
        return None
    transaction_id = _string_field(dataset_transaction, "id")
    if transaction_id is None:
        return None
    return {
        "transactionId": transaction_id,
        "summary": _summary(check_results),
        "schemaReferences": _schema_references(check_results),
        "checkedManifestHashes": _checked_manifest_hashes(check_results),
        "results": [_result_payload(row) for row in check_results],
        "failedRowSampleCount": len(failed_row_samples),
        "failedRowSamples": [_failed_row_sample(row) for row in failed_row_samples[:5]],
    }


def _summary(rows: Sequence[DatasetCheckResultRow]) -> RuntimeJsonObject:
    counts = {status: 0 for status in QUALITY_STATUS_KEYS}
    for row in rows:
        status = row["status"]
        if status in counts:
            counts[status] += 1
    return {
        "total": len(rows),
        "pass": counts["PASS"],
        "warn": counts["WARN"],
        "quarantine": counts["QUARANTINE"],
        "blockCommit": counts["BLOCK_COMMIT"],
    }


def _schema_references(rows: Sequence[DatasetCheckResultRow]) -> list[RuntimeJsonObject]:
    references: list[RuntimeJsonObject] = []
    seen: set[tuple[str, int]] = set()
    for row in rows:
        key = (row["validated_against_schema_version_id"], row["validated_against_schema_version"])
        if key not in seen:
            seen.add(key)
            references.append({"id": key[0], "version": key[1]})
    return references


def _checked_manifest_hashes(rows: Sequence[DatasetCheckResultRow]) -> list[str]:
    return sorted({row["checked_manifest_hash"] for row in rows})


def _result_payload(row: DatasetCheckResultRow) -> RuntimeJsonObject:
    return {
        "id": row["id"],
        "checkId": row["check_id"],
        "runId": row["run_id"],
        "status": row["status"],
        "checkedManifestHash": row["checked_manifest_hash"],
        "validatedAgainstSchemaVersionId": row["validated_against_schema_version_id"],
        "validatedAgainstSchemaVersion": row["validated_against_schema_version"],
        "details": dict(row["details"]),
        "createdAt": row["created_at"],
    }


def _failed_row_sample(row: RuntimeRow) -> RuntimeJsonObject:
    metadata = _mapping(row.get("metadata"))
    return {
        "deadLetterRecordId": row.get("id"),
        "sourceRunId": row.get("source_run_id"),
        "sourceEventId": row.get("source_event_id"),
        "status": row.get("status"),
        "errorKind": row.get("error_kind"),
        "errorMessage": row.get("error_message"),
        "rowIndex": metadata.get("rowIndex"),
        "checkedManifestHash": metadata.get("checkedManifestHash"),
        "check": metadata.get("check"),
        "result": metadata.get("result"),
        "payload": _mapping(row.get("payload")),
    }


def _mapping(value: object) -> RuntimeJsonObject:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def _string_field(row: RuntimeRow, key: str) -> str | None:
    value = row.get(key)
    return value if isinstance(value, str) and value else None
