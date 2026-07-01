"""Application service helpers for transform watermarks workflows."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from foundry_lite.application.ports import DatasetRow, DatasetTransactionRow
from foundry_lite.application.services.observability_time import parse_optional_time


def transform_output_platform_watermark(
    *,
    output_dataset: DatasetRow,
    input_versions: Mapping[str, str],
    input_transactions: Mapping[str, DatasetTransactionRow | None],
) -> Mapping[str, object] | None:
    inputs = _watermarked_inputs(input_versions, input_transactions)
    if not inputs:
        return None
    reprocessing = _reprocessing(inputs)
    return {
        "scope": "platform_dataset_event_time",
        "producer": "transform",
        "resourceType": "dataset",
        "resourceId": output_dataset["id"],
        "datasetRef": f"{output_dataset['namespace']}.{output_dataset['name']}",
        "status": "REPROCESS_REQUIRED" if reprocessing["isRequired"] else "CURRENT",
        "inputCount": len(input_versions),
        "watermarkedInputCount": len(inputs),
        "timeAxis": _time_axis(inputs),
        "inputs": inputs,
        "reprocessing": reprocessing,
    }


def _watermarked_inputs(
    input_versions: Mapping[str, str],
    input_transactions: Mapping[str, DatasetTransactionRow | None],
) -> list[Mapping[str, object]]:
    rows: list[Mapping[str, object]] = []
    for dataset_ref, version_id in sorted(input_versions.items()):
        transaction = input_transactions.get(dataset_ref)
        watermark = _platform_watermark(transaction)
        if not watermark:
            continue
        rows.append(_watermarked_input(dataset_ref, version_id, watermark))
    return rows


def _watermarked_input(
    dataset_ref: str,
    version_id: str,
    watermark: Mapping[str, object],
) -> Mapping[str, object]:
    return {
        "datasetRef": dataset_ref,
        "inputDatasetVersionId": version_id,
        "sourceKey": watermark.get("sourceKey"),
        "status": watermark.get("status") or "CURRENT",
        "timeAxis": _mapping(watermark.get("timeAxis")),
        "reprocessing": _mapping(watermark.get("reprocessing")),
        "platformWatermark": dict(watermark),
    }


def _time_axis(inputs: list[Mapping[str, object]]) -> Mapping[str, object]:
    return {
        "kind": "event_time",
        "policy": "min_input_event_time",
        "watermarkEventTime": _min_time_axis_value(inputs, "watermarkEventTime"),
        "maxAcceptedEventTime": _min_time_axis_value(inputs, "maxAcceptedEventTime"),
    }


def _reprocessing(inputs: list[Mapping[str, object]]) -> Mapping[str, object]:
    impacted = [_reprocessing_source(row) for row in inputs if _requires_reprocessing(row)]
    return {"isRequired": bool(impacted), "sourceInputs": impacted}


def _reprocessing_source(row: Mapping[str, object]) -> Mapping[str, object]:
    return {
        "datasetRef": row.get("datasetRef"),
        "inputDatasetVersionId": row.get("inputDatasetVersionId"),
        "sourceKey": row.get("sourceKey"),
        "status": row.get("status"),
        "reprocessing": row.get("reprocessing"),
    }


def _requires_reprocessing(row: Mapping[str, object]) -> bool:
    if row.get("status") == "REPROCESS_REQUIRED":
        return True
    reprocessing = _mapping(row.get("reprocessing"))
    return reprocessing.get("isRequired") is True


def _platform_watermark(transaction: DatasetTransactionRow | None) -> Mapping[str, object]:
    if transaction is None:
        return {}
    metadata = _mapping(transaction.get("metadata"))
    return _mapping(metadata.get("platformWatermark"))


def _min_time_axis_value(inputs: list[Mapping[str, object]], field: str) -> str | None:
    values: list[str] = []
    for row in inputs:
        time_axis = _mapping(row.get("timeAxis"))
        value = time_axis.get(field)
        if isinstance(value, str) and _can_parse_time(value):
            values.append(value)
    if not values:
        return None
    return min(values, key=_parsed_time)


def _parsed_time(value: str) -> datetime:
    parsed = parse_optional_time(value)
    if parsed is None:
        raise AssertionError("checked platform watermark time could not be parsed")
    return parsed


def _can_parse_time(value: str) -> bool:
    try:
        return parse_optional_time(value) is not None
    except ValueError:
        return False


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}
