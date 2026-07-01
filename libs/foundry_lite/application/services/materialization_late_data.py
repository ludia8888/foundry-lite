"""Application service helpers for materialization late data workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import RuntimeRow, TransactionContext
from foundry_lite.application.ports.materialization_repository import MaterializationRepository, MaterializationRow
from foundry_lite.application.services.materialization_protocols import MaterializationRuntimeBoundary
from foundry_lite.domain.context import RequestContext


def object_snapshot_watermark(
    materialization_repository: MaterializationRepository,
    runtime_service: MaterializationRuntimeBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    materialization: MaterializationRow,
) -> Mapping[str, object]:
    object_type = str(materialization["source_ref"].get("objectType", "Order"))
    active_version = materialization_repository.active_object_index_version(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        object_type_api_name=object_type,
    )
    max_sequence = materialization_repository.latest_object_record_watermark(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        object_type_api_name=object_type,
        index_version=active_version,
    )
    watermark: dict[str, object] = {
        "object_change_sequence_lte": max_sequence,
        "active_index_version": active_version,
    }
    reopen = late_data_reopen_context(runtime_service, conn, ctx, materialization, object_type)
    if reopen["isReopened"]:
        watermark["lateDataReopen"] = reopen
    return watermark


def late_data_reopen_context(
    runtime_service: MaterializationRuntimeBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    materialization: MaterializationRow,
    object_type: str,
) -> dict[str, object]:
    previous = _latest_completed_materialization_run(runtime_service, conn, ctx, str(materialization["api_name"]))
    late_index = _latest_late_index_run(runtime_service, conn, ctx, object_type)
    if previous is None or late_index is None or not _row_completed_after(late_index, previous):
        return {"isReopened": False}
    cursor = late_index.get("cursor")
    cursor = cursor if isinstance(cursor, Mapping) else {}
    return {
        "isReopened": True,
        "reason": "late_data_reprocess",
        "previousMaterializationRunId": previous["id"],
        "previousDatasetVersionId": previous.get("target_dataset_version_id"),
        "sourceIndexRunId": late_index["id"],
        "lateDataStatusCounts": cursor.get("lateDataStatusCounts", {}),
        "lateEventIds": cursor.get("lateEventIds", []),
        "maxEventTimeLagSeconds": cursor.get("maxEventTimeLagSeconds"),
    }


def _latest_completed_materialization_run(
    runtime_service: MaterializationRuntimeBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    api_name: str,
) -> RuntimeRow | None:
    rows = [
        row
        for row in runtime_service._rows_for_tenant(conn, "materialization_runs", ctx)
        if row.get("api_name") == api_name and row.get("status") == "succeeded" and row.get("completed_at")
    ]
    return _latest_runtime_row(rows)


def _latest_late_index_run(
    runtime_service: MaterializationRuntimeBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: str,
) -> RuntimeRow | None:
    rows = [
        row for row in runtime_service._rows_for_tenant(conn, "index_runs", ctx) if _is_late_index_run(row, object_type)
    ]
    return _latest_runtime_row(rows)


def _is_late_index_run(row: RuntimeRow, object_type: str) -> bool:
    if row.get("object_type_api_name") != object_type or row.get("status") != "succeeded":
        return False
    cursor = row.get("cursor")
    if not isinstance(cursor, Mapping):
        return False
    counts = cursor.get("lateDataStatusCounts")
    return isinstance(counts, Mapping) and _int_count(counts.get("LATE_REQUIRES_REPROCESS")) > 0


def _row_completed_after(candidate: RuntimeRow, baseline: RuntimeRow) -> bool:
    candidate_time = _row_time(candidate)
    baseline_time = _row_time(baseline)
    if candidate_time is None or baseline_time is None:
        return False
    return candidate_time > baseline_time


def _latest_runtime_row(rows: Sequence[RuntimeRow]) -> RuntimeRow | None:
    if not rows:
        return None
    return sorted(rows, key=lambda row: (_row_time(row) or "", str(row.get("id", ""))))[-1]


def _row_time(row: RuntimeRow) -> str | None:
    value = row.get("completed_at") or row.get("updated_at") or row.get("created_at") or row.get("started_at")
    return value if isinstance(value, str) and value else None


def _int_count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
