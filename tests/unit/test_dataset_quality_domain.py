from __future__ import annotations

from foundry_lite.domain.dataset.quality import (
    allows_empty_dataset,
    dataset_quality_policy_status,
    default_primary_key_checks,
    is_row_quarantine_check,
)


def test_dataset_quality_policy_status_warns_or_blocks_by_severity() -> None:
    assert dataset_quality_policy_status("passed", "row_count_min", "error") == "PASS"
    assert dataset_quality_policy_status("failed", "row_count_min", "warning") == "WARN"
    assert dataset_quality_policy_status("failed", "not_null", "quarantine") == "QUARANTINE"
    assert dataset_quality_policy_status("failed", "row_count_min", "quarantine") == "BLOCK_COMMIT"


def test_default_primary_key_checks_are_row_level_contracts() -> None:
    checks = default_primary_key_checks(["order_id", "line_id"])

    assert checks == [
        {"type": "not_null", "columns": ["order_id"]},
        {"type": "not_null", "columns": ["line_id"]},
        {"type": "unique_tuple", "columns": ["order_id", "line_id"]},
    ]
    assert is_row_quarantine_check("unique_tuple")


def test_allow_empty_check_suppresses_default_row_count_min() -> None:
    assert allows_empty_dataset([{"type": "allow_empty"}])
    assert not allows_empty_dataset([{"type": "row_count_min", "min": 1}])
