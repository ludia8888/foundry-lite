from __future__ import annotations

from foundry_lite.application.state_transitions import (
    ACTION_RUN_COMPENSATION_REQUIRED,
    ACTION_RUN_EXTERNAL_PENDING,
    ACTION_RUN_FAILED,
    ACTION_RUN_OUTCOME_UNKNOWN,
    ACTION_RUN_SUCCEEDED,
    SYNC_RUN_FAILED,
    classify_transition_miss,
    dataset_run_failed_transition,
)


def test_action_run_external_pending_is_a_recoverable_write_ahead_state() -> None:
    # The durable write-ahead marker is committed only from the transient ``received`` state.
    assert ACTION_RUN_EXTERNAL_PENDING.from_statuses == ("received",)
    assert ACTION_RUN_EXTERNAL_PENDING.to_status == "external_pending"
    # Every resolution fires from BOTH ``received`` (simulated / no-adapter inline path) and the committed
    # ``external_pending`` marker (real-adapter inline resolve or recovery sweep), so a committed
    # external_pending run is always recoverable — never stranded.
    for transition in (
        ACTION_RUN_SUCCEEDED,
        ACTION_RUN_FAILED,
        ACTION_RUN_OUTCOME_UNKNOWN,
        ACTION_RUN_COMPENSATION_REQUIRED,
    ):
        assert transition.from_statuses == ("received", "external_pending")


def test_classify_transition_miss_distinguishes_missing_row_from_stale_state() -> None:
    result = classify_transition_miss(SYNC_RUN_FAILED, None)

    assert result.outcome == "not_found"
    assert result.current_status is None
    assert not result.updated


def test_dataset_run_failed_transition_routes_unknown_run_kind_to_materialization_abort() -> None:
    transition = dataset_run_failed_transition("materialization")

    assert transition.from_statuses == ("running",)
    assert transition.to_status == "FAILED"
