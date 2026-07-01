"""Application service helpers for late data reprocessing workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import DatasetRow, DatasetTransactionRow, StreamArchiveConfig, StreamEvent
from foundry_lite.application.services.dataset.platform_watermark import platform_watermark_metadata
from foundry_lite.application.services.dataset.stream_archive import stream_transaction_metadata


def stream_commit_metadata(
    dataset: DatasetRow,
    stream: StreamArchiveConfig,
    events: Sequence[StreamEvent],
    previous_transaction: DatasetTransactionRow | None,
    rows: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    previous_metadata = previous_transaction["metadata"] if previous_transaction is not None else {}
    metadata = dict(stream_transaction_metadata(stream, events, previous_metadata, rows))
    metadata["lateDataSummary"] = stream_late_data_summary(rows)
    plan = stream_reprocessing_plan(dataset, stream, previous_transaction, rows)
    if plan is not None:
        metadata["lateDataReprocessingPlan"] = plan
    platform_watermark = platform_watermark_metadata(dataset, metadata)
    if platform_watermark is not None:
        metadata["platformWatermark"] = platform_watermark
    return metadata


def stream_late_data_summary(rows: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
    status_counts = _status_counts(rows)
    lag_values = _event_time_lags(rows)
    return {
        "rowCount": len(rows),
        "statusCounts": status_counts,
        "maxEventTimeLagSeconds": max(lag_values, default=None),
        "requiresReprocessing": status_counts.get("LATE_REQUIRES_REPROCESS", 0) > 0,
    }


def stream_reprocessing_plan(
    dataset: DatasetRow,
    stream: StreamArchiveConfig,
    previous_transaction: DatasetTransactionRow | None,
    rows: Sequence[Mapping[str, object]],
) -> Mapping[str, object] | None:
    late_rows = _late_reprocess_rows(rows)
    if not late_rows:
        return None
    previous_version_id = _previous_version_id(previous_transaction)
    return {
        "status": "REPROCESS_REQUIRED",
        "scope": "stream_archive",
        "reason": "accepted_late_data",
        "sourceDatasetId": dataset["id"],
        "sourceDatasetRef": f"{dataset['namespace']}.{dataset['name']}",
        "streamName": stream.stream_name,
        "topic": stream.topic,
        "partition": stream.partition,
        "consumerGroup": stream.consumer_group,
        "previousCommittedDatasetVersionId": previous_version_id,
        "affectedClosedOutputs": _affected_outputs(previous_version_id),
        "lateRecordCount": len(late_rows),
        "lateEventIds": _late_event_ids(late_rows),
        "reprocessFromEventTime": _min_text_value(late_rows, "event_time"),
        "latestLateEventTime": _max_text_value(late_rows, "event_time"),
        "maxEventTimeLagSeconds": max(_event_time_lags(late_rows), default=None),
        "nextStep": "Rerun downstream transforms/materializations that read the affected dataset version.",
    }


def _status_counts(rows: Sequence[Mapping[str, object]]) -> Mapping[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = row.get("late_data_status")
        if isinstance(status, str) and status:
            counts[status] = counts.get(status, 0) + 1
    return counts


def _event_time_lags(rows: Sequence[Mapping[str, object]]) -> list[int]:
    return [value for row in rows if isinstance((value := row.get("event_time_lag_seconds")), int)]


def _late_reprocess_rows(rows: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return [row for row in rows if row.get("late_data_status") == "LATE_REQUIRES_REPROCESS"]


def _previous_version_id(previous_transaction: DatasetTransactionRow | None) -> str | None:
    if previous_transaction is None:
        return None
    value = previous_transaction.get("committed_version_id")
    return value if isinstance(value, str) and value else None


def _affected_outputs(previous_version_id: str | None) -> list[Mapping[str, object]]:
    if previous_version_id is None:
        return []
    return [{"resourceType": "dataset_version", "resourceId": previous_version_id, "relation": "closed_archive"}]


def _late_event_ids(rows: Sequence[Mapping[str, object]]) -> list[str]:
    return [value for row in rows if isinstance((value := row.get("event_id")), str) and value]


def _min_text_value(rows: Sequence[Mapping[str, object]], key: str) -> str | None:
    values = [value for row in rows if isinstance((value := row.get(key)), str) and value]
    return min(values) if values else None


def _max_text_value(rows: Sequence[Mapping[str, object]], key: str) -> str | None:
    values = [value for row in rows if isinstance((value := row.get(key)), str) and value]
    return max(values) if values else None
