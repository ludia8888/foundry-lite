"""Application service helpers for platform watermark workflows."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import DatasetRow


def platform_watermark_metadata(
    dataset: DatasetRow,
    transaction_metadata: Mapping[str, object],
) -> Mapping[str, object] | None:
    watermark = _mapping(transaction_metadata.get("lateDataWatermark"))
    if not watermark:
        return None
    summary = _mapping(transaction_metadata.get("lateDataSummary"))
    plan = _mapping(transaction_metadata.get("lateDataReprocessingPlan"))
    return {
        "scope": "platform_dataset_event_time",
        "producer": "stream_archive",
        "resourceType": "dataset",
        "resourceId": dataset["id"],
        "datasetRef": f"{dataset['namespace']}.{dataset['name']}",
        "sourceKey": _source_key(watermark),
        "stream": _stream_identity(watermark),
        "timeAxis": _time_axis(watermark),
        "policy": _policy(watermark),
        "status": _status(plan),
        "summary": dict(summary),
        "reprocessing": _reprocessing(plan),
    }


def _source_key(watermark: Mapping[str, object]) -> str:
    return ":".join(
        [
            str(watermark.get("streamName")),
            str(watermark.get("topic")),
            str(watermark.get("partition")),
            str(watermark.get("consumerGroup")),
        ]
    )


def _stream_identity(watermark: Mapping[str, object]) -> Mapping[str, object]:
    return {
        "streamName": watermark.get("streamName"),
        "topic": watermark.get("topic"),
        "partition": watermark.get("partition"),
        "consumerGroup": watermark.get("consumerGroup"),
    }


def _time_axis(watermark: Mapping[str, object]) -> Mapping[str, object]:
    return {
        "kind": "event_time",
        "eventTimeField": watermark.get("eventTimeField"),
        "sourceTimeField": watermark.get("sourceTimeField"),
        "timeZone": watermark.get("timeZone"),
        "watermarkEventTime": watermark.get("watermarkEventTime"),
        "maxAcceptedEventTime": watermark.get("maxAcceptedEventTime"),
    }


def _policy(watermark: Mapping[str, object]) -> Mapping[str, object]:
    return {
        "allowedLatenessSeconds": watermark.get("allowedLatenessSeconds"),
        "tooLateAfterSeconds": watermark.get("tooLateAfterSeconds"),
    }


def _status(plan: Mapping[str, object]) -> str:
    return "REPROCESS_REQUIRED" if plan.get("status") == "REPROCESS_REQUIRED" else "CURRENT"


def _reprocessing(plan: Mapping[str, object]) -> Mapping[str, object]:
    if plan.get("status") != "REPROCESS_REQUIRED":
        return {"isRequired": False, "affectedClosedOutputs": []}
    return {
        "isRequired": True,
        "reason": plan.get("reason"),
        "previousCommittedDatasetVersionId": plan.get("previousCommittedDatasetVersionId"),
        "affectedClosedOutputs": _sequence(plan.get("affectedClosedOutputs")),
        "lateEventIds": _sequence(plan.get("lateEventIds")),
        "reprocessFromEventTime": plan.get("reprocessFromEventTime"),
        "latestLateEventTime": plan.get("latestLateEventTime"),
        "maxEventTimeLagSeconds": plan.get("maxEventTimeLagSeconds"),
    }


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> list[object]:
    return list(value) if isinstance(value, list | tuple) else []
