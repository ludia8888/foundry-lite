"""Application service helpers for materialization watermarks workflows."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.services.materialization_types import MaterializationRunPlan


def materialization_platform_watermark(plan: MaterializationRunPlan) -> Mapping[str, object]:
    if plan.materialization_type == "action_log":
        return _action_log_platform_watermark(plan)
    return _object_snapshot_platform_watermark(plan)


def _action_log_platform_watermark(plan: MaterializationRunPlan) -> Mapping[str, object]:
    return _base_platform_watermark(plan) | {
        "scope": "platform_action_time",
        "status": "CURRENT",
        "sourceKey": f"materialization:{plan.api_name}:{plan.watermark.get('action_run_id_lte')}",
        "timeAxis": {
            "kind": "action_completed_at",
            "completedAtLte": plan.watermark.get("action_run_completed_at_lte"),
            "actionRunIdLte": plan.watermark.get("action_run_id_lte"),
        },
        "reprocessing": {"isRequired": False},
    }


def _object_snapshot_platform_watermark(plan: MaterializationRunPlan) -> Mapping[str, object]:
    reopen = _mapping(plan.watermark.get("lateDataReopen"))
    return _base_platform_watermark(plan) | {
        "scope": "platform_object_store_watermark",
        "status": "REPROCESS_REQUIRED" if reopen.get("isReopened") is True else "CURRENT",
        "sourceKey": _object_snapshot_source_key(plan),
        "timeAxis": {
            "kind": "object_change_sequence",
            "objectChangeSequenceLte": plan.watermark.get("object_change_sequence_lte"),
            "activeIndexVersion": plan.watermark.get("active_index_version"),
        },
        "reprocessing": _object_snapshot_reprocessing(reopen),
    }


def _base_platform_watermark(plan: MaterializationRunPlan) -> dict[str, object]:
    return {
        "producer": "materialization",
        "resourceType": "dataset",
        "resourceId": plan.target_dataset["id"],
        "datasetRef": f"{plan.target_dataset['namespace']}.{plan.target_dataset['name']}",
        "materialization": {
            "apiName": plan.api_name,
            "materializationType": plan.materialization_type,
            "runId": plan.run_id,
        },
        "sourceCursor": dict(plan.watermark),
    }


def _object_snapshot_source_key(plan: MaterializationRunPlan) -> str:
    return (
        f"materialization:{plan.api_name}:"
        f"{plan.watermark.get('active_index_version')}:{plan.watermark.get('object_change_sequence_lte')}"
    )


def _object_snapshot_reprocessing(reopen: Mapping[str, object]) -> Mapping[str, object]:
    if reopen.get("isReopened") is not True:
        return {"isRequired": False}
    return {
        "isRequired": True,
        "reason": reopen.get("reason"),
        "previousMaterializationRunId": reopen.get("previousMaterializationRunId"),
        "previousDatasetVersionId": reopen.get("previousDatasetVersionId"),
        "sourceIndexRunId": reopen.get("sourceIndexRunId"),
        "lateDataStatusCounts": reopen.get("lateDataStatusCounts"),
        "lateEventIds": reopen.get("lateEventIds"),
        "maxEventTimeLagSeconds": reopen.get("maxEventTimeLagSeconds"),
    }


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}
