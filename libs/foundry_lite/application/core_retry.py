"""Application-layer models and helpers for core retry."""

from __future__ import annotations

from foundry_lite.application.ports import RuntimeRetryMaterializationResult, RuntimeRetryPlan
from foundry_lite.application.primitives import CommitResult


def retry_materialization_name(result: RuntimeRetryPlan) -> str | None:
    if result["eventType"] != "materialization.requested":
        return None
    value = result["payload"].get("materialization")
    return value if isinstance(value, str) and value else None


def retry_materialization_result(
    api_name: str,
    commit: CommitResult,
) -> RuntimeRetryMaterializationResult:
    return {
        "kind": "materialization",
        "apiName": api_name,
        "datasetId": commit.dataset_id,
        "datasetRef": commit.dataset_ref,
        "transactionId": commit.transaction_id,
        "versionId": commit.version_id,
        "versionNumber": commit.version_number,
        "rowCount": commit.row_count,
    }
