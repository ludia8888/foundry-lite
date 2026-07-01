from __future__ import annotations

from typing import cast

from foundry_lite.application.ports import DatasetRow, DatasetTransactionRow
from foundry_lite.application.services.materialization_types import MaterializationRunPlan
from foundry_lite.application.services.materialization_watermarks import materialization_platform_watermark
from foundry_lite.application.services.transform_watermarks import transform_output_platform_watermark


def test_platform_watermark_contract_covers_action_boundaries() -> None:
    metadata = materialization_platform_watermark(
        _materialization_plan(
            "action_log",
            _dataset("ds_ops_action_log", "ops", "action_log"),
            {
                "action_run_completed_at_lte": "2026-06-18T02:03:04+00:00",
                "action_run_id_lte": "act_run_123",
            },
        )
    )

    time_axis = cast(dict[str, object], metadata["timeAxis"])

    assert metadata["producer"] == "materialization"
    assert metadata["scope"] == "platform_action_time"
    assert metadata["status"] == "CURRENT"
    assert metadata["datasetRef"] == "ops.action_log"
    assert time_axis["kind"] == "action_completed_at"
    assert time_axis["completedAtLte"] == "2026-06-18T02:03:04+00:00"
    assert time_axis["actionRunIdLte"] == "act_run_123"
    assert cast(dict[str, object], metadata["reprocessing"])["isRequired"] is False


def test_platform_watermark_contract_covers_object_materialization_reprocessing() -> None:
    metadata = materialization_platform_watermark(
        _materialization_plan(
            "object_snapshot",
            _dataset("ds_ops_order_current", "ops", "order_current"),
            {
                "object_change_sequence_lte": 42,
                "active_index_version": "idx_v2",
                "lateDataReopen": {
                    "isReopened": True,
                    "reason": "late_data_reprocess",
                    "previousMaterializationRunId": "mat_run_old",
                    "previousDatasetVersionId": "dsv_old",
                    "sourceIndexRunId": "idx_run_late",
                    "lateDataStatusCounts": {"LATE_REQUIRES_REPROCESS": 1},
                    "lateEventIds": ["topic:0:40"],
                    "maxEventTimeLagSeconds": 7200,
                },
            },
        )
    )

    time_axis = cast(dict[str, object], metadata["timeAxis"])
    reprocessing = cast(dict[str, object], metadata["reprocessing"])

    assert metadata["scope"] == "platform_object_store_watermark"
    assert metadata["status"] == "REPROCESS_REQUIRED"
    assert time_axis["kind"] == "object_change_sequence"
    assert time_axis["objectChangeSequenceLte"] == 42
    assert time_axis["activeIndexVersion"] == "idx_v2"
    assert reprocessing["isRequired"] is True
    assert reprocessing["previousDatasetVersionId"] == "dsv_old"


def test_transform_output_platform_watermark_uses_min_input_event_time_and_reprocessing() -> None:
    metadata = transform_output_platform_watermark(
        output_dataset=cast(DatasetRow, _dataset("ds_clean_orders", "clean", "orders")),
        input_versions={
            "raw.orders": "dsv_raw_orders",
            "raw.customers": "dsv_raw_customers",
            "raw.static_ref": "dsv_static",
        },
        input_transactions={
            "raw.orders": _transaction(
                "dsv_raw_orders",
                watermark_time="2026-06-18T02:00:00+00:00",
                status="CURRENT",
            ),
            "raw.customers": _transaction(
                "dsv_raw_customers",
                watermark_time="2026-06-18T01:00:00+00:00",
                status="REPROCESS_REQUIRED",
            ),
            "raw.static_ref": None,
        },
    )

    assert metadata is not None
    assert metadata["producer"] == "transform"
    assert metadata["status"] == "REPROCESS_REQUIRED"
    assert metadata["inputCount"] == 3
    assert metadata["watermarkedInputCount"] == 2
    assert cast(dict[str, object], metadata["timeAxis"]) == {
        "kind": "event_time",
        "policy": "min_input_event_time",
        "watermarkEventTime": "2026-06-18T01:00:00+00:00",
        "maxAcceptedEventTime": "2026-06-18T01:00:00+00:00",
    }
    reprocessing = cast(dict[str, object], metadata["reprocessing"])
    source_inputs = cast(list[dict[str, object]], reprocessing["sourceInputs"])
    inputs = cast(list[dict[str, object]], metadata["inputs"])
    assert reprocessing["isRequired"] is True
    assert source_inputs[0]["datasetRef"] == "raw.customers"
    assert [row["datasetRef"] for row in inputs] == ["raw.customers", "raw.orders"]


def test_transform_output_platform_watermark_returns_none_without_watermarked_inputs() -> None:
    metadata = transform_output_platform_watermark(
        output_dataset=cast(DatasetRow, _dataset("ds_clean_orders", "clean", "orders")),
        input_versions={"raw.orders": "dsv_raw_orders"},
        input_transactions={"raw.orders": _transaction("dsv_raw_orders", watermark_time=None)},
    )

    assert metadata is None


def _dataset(dataset_id: str, namespace: str, name: str) -> dict[str, object]:
    return {
        "id": dataset_id,
        "tenant_id": "tenant_demo",
        "namespace": namespace,
        "name": name,
        "description": None,
        "storage_kind": "local",
        "storage_uri": None,
        "owner_team": None,
        "classification": None,
        "status": "active",
        "primary_key": ["id"],
        "partition_spec": [],
        "sort_order": [],
        "target_file_size_bytes": None,
        "created_at": "2026-06-18T00:00:00Z",
        "updated_at": "2026-06-18T00:00:00Z",
    }


def _materialization_plan(
    materialization_type: str,
    dataset: dict[str, object],
    watermark: dict[str, object],
) -> MaterializationRunPlan:
    return MaterializationRunPlan(
        api_name="action_log" if materialization_type == "action_log" else "order_current",
        materialization_id="mat_123",
        materialization_type=materialization_type,
        target_dataset=cast(DatasetRow, dataset),
        transaction_id="dstx_123",
        run_id="mat_run_123",
        watermark=watermark,
        rows=(),
        fieldnames=[],
    )


def _transaction(
    version_id: str,
    *,
    watermark_time: str | None,
    status: str = "CURRENT",
) -> DatasetTransactionRow:
    metadata: dict[str, object] = {}
    if watermark_time is not None:
        metadata["platformWatermark"] = {
            "scope": "platform_dataset_event_time",
            "producer": "stream_archive",
            "sourceKey": f"shipments:events:0:{version_id}",
            "status": status,
            "timeAxis": {
                "kind": "event_time",
                "watermarkEventTime": watermark_time,
                "maxAcceptedEventTime": watermark_time,
            },
            "reprocessing": {"isRequired": status == "REPROCESS_REQUIRED"},
        }
    return cast(
        DatasetTransactionRow,
        {
            "id": f"dstx_{version_id}",
            "tenant_id": "tenant_demo",
            "dataset_id": "ds_input",
            "branch": "main",
            "tx_type": "APPEND",
            "status": "COMMITTED",
            "base_version_id": None,
            "committed_version_id": version_id,
            "schema_version": 1,
            "created_by": "tester",
            "created_at": "2026-06-18T00:00:00Z",
            "committed_at": "2026-06-18T00:00:01Z",
            "metadata": metadata,
        },
    )
