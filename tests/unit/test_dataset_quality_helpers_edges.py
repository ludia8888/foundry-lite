from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from foundry_lite.application.services.dataset import quality_helpers as helpers
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import ValidationFailed


def test_dataset_quality_helper_policy_and_row_failure_edges() -> None:
    rows = [
        {"order_id": "O-1", "line_id": "1", "status": "PENDING"},
        {"order_id": "O-1", "line_id": "1", "status": "CANCELLED"},
        {"order_id": "O-2", "line_id": "2", "status": None},
    ]

    assert helpers._quality_policy_status({"status": "passed"}, {"type": "row_count_min"}) == "PASS"
    assert helpers._quality_policy_status({"status": "failed"}, {"type": "row_count_min", "severity": "warn"}) == "WARN"
    assert (
        helpers._quality_policy_status({"status": "failed"}, {"type": "not_null", "severity": "quarantine"})
        == "QUARANTINE"
    )
    assert helpers._quality_policy_status({"status": "failed"}, {"type": "row_count_min"}) == "BLOCK_COMMIT"
    assert helpers._failed_row_indexes(rows, {"type": "not_null", "columns": ["status"]}) == [2]
    assert helpers._failed_row_indexes(rows, {"type": "unique", "column": "order_id"}) == [0, 1]
    assert helpers._failed_row_indexes(rows, {"type": "unique_tuple", "columns": ["order_id", "line_id"]}) == [0, 1]
    assert helpers._failed_row_indexes(
        rows, {"type": "accepted_values", "column": "status", "values": ["PENDING", "APPROVED"]}
    ) == [1]
    assert helpers._failed_row_indexes(rows, {"type": "allow_empty"}) == []
    assert helpers._accepted_values({"values": "PENDING"}) == []
    assert helpers._string_list("status") == []


def test_dataset_quality_helper_contract_validation_edges() -> None:
    accepted = helpers._contract_check_config(
        {"type": "accepted_values", "column": "status", "values": ["PENDING", 1, True, 1.5]},
        severity="warning",
    )

    assert accepted["severity"] == "warning"
    assert helpers._validate_contract_key(" default ") == "default"
    with pytest.raises(ValidationFailed, match="contract key is required"):
        helpers._validate_contract_key(" ")
    with pytest.raises(ValidationFailed, match="contract key is too long"):
        helpers._validate_contract_key("x" * 81)
    with pytest.raises(ValidationFailed, match="requires a column"):
        helpers._contract_check_config({"type": "accepted_values", "column": " status", "values": ["PENDING"]}, None)
    with pytest.raises(ValidationFailed, match="requires values"):
        helpers._contract_check_config({"type": "accepted_values", "column": "status", "values": "PENDING"}, None)
    with pytest.raises(ValidationFailed, match="scalar JSON values"):
        helpers._contract_check_config({"type": "accepted_values", "column": "status", "values": [None]}, None)
    with pytest.raises(ValidationFailed, match="scalar JSON values"):
        helpers._contract_check_config({"type": "accepted_values", "column": "status", "values": [float("nan")]}, None)


def test_dataset_quality_helper_shapes_history_summary_and_quarantine_record(tmp_path: Path) -> None:
    dataset = {
        "id": "dataset-1",
        "tenant_id": "tenant-demo",
        "namespace": "raw",
        "name": "orders",
        "description": None,
        "created_by": "admin",
        "created_at": "2026-07-01T00:00:00+00:00",
        "primary_key": ["order_id"],
    }
    check_row = {
        "id": "check-1",
        "tenant_id": "tenant-demo",
        "dataset_id": "dataset-1",
        "name": "not-null-status",
        "check_type": "not_null",
        "config": {"type": "not_null", "columns": ["status"]},
        "severity": "quarantine",
        "enabled": True,
    }
    updated = helpers._updated_contract_check_record(
        check_row, config={"type": "row_count_min", "min": 1}, severity=None, enabled=None
    )
    contract_version = helpers._contract_version(
        {
            "id": "contract-1",
            "tenant_id": "tenant-demo",
            "dataset_id": "dataset-1",
            "contract_key": "default",
            "version": 1,
            "status": "ACTIVE",
            "owner_user_id": "owner",
            "description": "contract",
            "checks_snapshot": [helpers._contract_check_snapshot(check_row)],
            "schema_version_id": "schema-1",
            "schema_version": 7,
            "created_at": "2026-07-01T00:00:00+00:00",
            "activated_at": None,
        },
        dataset,
    )
    summary = helpers._quality_result_summary(
        dataset,
        status_rows=[{"status": "PASS", "count": 2}],
        type_rows=[{"check_type": "not_null", "status": "PASS", "count": 2}],
        latest_rows=[
            {
                "id": "result-1",
                "tenant_id": "tenant-demo",
                "dataset_id": "dataset-1",
                "check_id": "check-1",
                "data_contract_version_id": "contract-1",
                "check_name": "not-null-status",
                "check_type": "not_null",
                "severity": "quarantine",
                "run_id": "run-1",
                "transaction_id": "tx-1",
                "checked_manifest_hash": "manifest-hash",
                "validated_against_schema_version_id": "schema-1",
                "validated_against_schema_version": 7,
                "status": "PASS",
                "details": "ok",
                "created_at": "2026-07-01T00:00:00+00:00",
            }
        ],
    )
    quality_ctx = helpers.DatasetQualityRunContext(
        conn=cast(Any, None),
        request_ctx=demo_admin_context(),
        dataset=dataset,
        parquet_path=tmp_path / "orders.parquet",
        row_count=1,
        run_id="run-1",
        transaction_id="tx-1",
        checked_manifest_hash="manifest-hash",
        schema_version_id="schema-1",
        schema_version=7,
        active_contract_version_id="contract-1",
    )
    record, record_id = helpers._quarantine_dead_letter_record(
        quality_ctx,
        {"order_id": "O-1", "status": None},
        3,
        {"type": "not_null", "columns": ["status"]},
        {"check": "not_null", "status": "failed"},
    )

    assert updated.config == {"type": "row_count_min", "min": 1, "severity": "quarantine"}
    assert contract_version["datasetRef"] == "raw.orders"
    assert contract_version["checks"][0]["datasetId"] == "dataset-1"
    assert summary["totalResults"] == 2
    assert summary["latestResults"][0]["details"] == {"value": "ok"}
    assert record.dead_letter_record_id == record_id
    assert record.error_message == "dataset quality check failed: not_null"
    assert record.metadata["rowIndex"] == 3
    assert record.metadata["validatedAgainstSchemaVersion"] == 7
