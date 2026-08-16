"""Data-minimizing projections for operator-visible runtime runs."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import RuntimeRow, RuntimeRunType


def operator_safe_run_row(row: RuntimeRow, run_type: RuntimeRunType) -> RuntimeRow:
    """Project data-bearing runs to the minimum evidence needed by Operations."""

    if run_type != "source_exploration":
        return row
    safe_keys = (
        "id",
        "tenant_id",
        "source_name",
        "source_type",
        "status",
        "error",
        "operations_path",
        "created_at",
    )
    safe = {key: row[key] for key in safe_keys if key in row}
    summary = row.get("result_summary")
    if isinstance(summary, Mapping):
        safe["result_summary"] = _safe_exploration_metrics(summary)
    return safe


def _safe_exploration_metrics(summary: Mapping[str, object]) -> dict[str, object]:
    allowed = ("datasetCommitCreated", "pageCount", "rowCount", "tableCount", "topicCount")
    return {
        key: value
        for key in allowed
        if (value := summary.get(key)) is not None and isinstance(value, (bool, int, float))
    }


__all__ = ["operator_safe_run_row"]
