from __future__ import annotations

from foundry_lite.application.state_transitions import (
    SYNC_RUN_FAILED,
    classify_transition_miss,
    dataset_run_failed_transition,
)


def test_classify_transition_miss_distinguishes_missing_row_from_stale_state() -> None:
    result = classify_transition_miss(SYNC_RUN_FAILED, None)

    assert result.outcome == "not_found"
    assert result.current_status is None
    assert not result.updated


def test_dataset_run_failed_transition_routes_unknown_run_kind_to_materialization_abort() -> None:
    transition = dataset_run_failed_transition("materialization")

    assert transition.from_statuses == ("running",)
    assert transition.to_status == "FAILED"
