from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from foundry_lite.application.ports.pipeline_repository import PipelineRunRow, PipelineScheduleRow
from foundry_lite.application.services.pipeline_execution_contracts import PipelineScheduleSpec
from foundry_lite.application.services.pipeline_schedule_runtime import (
    initial_next_due_at,
    next_due_after_slot,
    normalize_legacy_pipeline_schedule,
    normalize_pipeline_schedule,
    parse_schedule_timestamp,
    pipeline_schedule_spec_from_row,
    resumed_next_due_at,
    schedule_iso,
    schedule_operation_fingerprint,
    schedule_spec_payload,
    scheduled_run_idempotency_key,
    scheduler_now,
)
from foundry_lite.application.services.pipeline_scheduler_results import (
    completion_values,
    terminal_observation_values,
)
from foundry_lite.domain.errors import ValidationFailed


def test_interval_schedule_normalizes_legacy_minutes_and_advances_exact_slot() -> None:
    now = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    spec = normalize_pipeline_schedule(
        {"type": "interval", "intervalMinutes": 15, "timezone": "Asia/Seoul"},
        is_enabled=True,
        now=now,
    )

    assert schedule_spec_payload(spec) == {
        "triggerType": "interval",
        "timezone": "Asia/Seoul",
        "intervalSeconds": 900,
        "startAt": "2026-01-01T00:00:00Z",
    }
    assert initial_next_due_at(spec, now) == "2026-01-01T00:00:00Z"
    assert next_due_after_slot(spec, "2026-01-01T00:00:00Z") == "2026-01-01T00:15:00Z"
    assert resumed_next_due_at(spec, now) == "2026-01-01T00:15:00Z"


def test_legacy_scheduler_fallback_keeps_pre_v2_enabled_schedule_runnable() -> None:
    now = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)

    spec, is_fallback = normalize_legacy_pipeline_schedule(
        {"kind": "manual"},
        is_enabled=True,
        now=now,
    )

    assert is_fallback is True
    assert schedule_spec_payload(spec) == {
        "triggerType": "interval",
        "timezone": "UTC",
        "intervalSeconds": 60,
        "startAt": "2026-01-01T00:00:00Z",
    }
    assert initial_next_due_at(spec, now) == "2026-01-01T00:00:00Z"


def test_interval_schedule_skips_past_slots_without_replaying_backlog() -> None:
    now = datetime(2026, 1, 1, 0, 7, 30, tzinfo=UTC)
    spec = normalize_pipeline_schedule(
        {
            "triggerType": "interval",
            "intervalSeconds": 300,
            "startAt": "2026-01-01T00:00:00Z",
        },
        is_enabled=True,
        now=now,
    )

    assert initial_next_due_at(spec, now) == "2026-01-01T00:10:00Z"


def test_cron_schedule_uses_named_timezone_and_skips_nonexistent_dst_time() -> None:
    now = datetime(2026, 3, 8, 6, 0, tzinfo=UTC)
    spec = normalize_pipeline_schedule(
        {
            "triggerType": "cron",
            "cronExpression": "30 2 * * *",
            "timezone": "America/New_York",
            "startAt": "2026-03-08T06:00:00Z",
        },
        is_enabled=True,
        now=now,
    )

    assert initial_next_due_at(spec, now) == "2026-03-09T06:30:00Z"


def test_cron_day_of_month_and_weekday_follow_standard_or_semantics() -> None:
    now = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    spec = normalize_pipeline_schedule(
        {
            "triggerType": "cron",
            "cronExpression": "0 0 15 * 1",
            "timezone": "UTC",
        },
        is_enabled=True,
        now=now,
    )

    assert initial_next_due_at(spec, now) == "2026-01-05T00:00:00Z"


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("0 0 */2 * 1", "2026-01-05T00:00:00Z"),
        ("0 0 15 * */3", "2026-02-15T00:00:00Z"),
    ],
)
def test_cron_star_steps_keep_standard_day_field_semantics(expression: str, expected: str) -> None:
    now = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    spec = normalize_pipeline_schedule(
        {
            "triggerType": "cron",
            "cronExpression": expression,
            "timezone": "UTC",
        },
        is_enabled=True,
        now=now,
    )

    assert initial_next_due_at(spec, now) == expected


def test_cron_registration_mid_minute_skips_elapsed_slot_and_supports_step_from_value() -> None:
    now = datetime(2026, 1, 1, 0, 5, 30, tzinfo=UTC)
    spec = normalize_pipeline_schedule(
        {
            "triggerType": "cron",
            "cronExpression": "5/15 * * * *",
            "timezone": "UTC",
        },
        is_enabled=True,
        now=now,
    )

    assert initial_next_due_at(spec, now) == "2026-01-01T00:20:00Z"


@pytest.mark.parametrize(
    "schedule",
    [
        {"triggerType": "data", "timezone": "UTC"},
        {"triggerType": "logic", "timezone": "UTC"},
        {"triggerType": "cron", "cronExpression": "0 * * * *", "timezone": "Mars/Base"},
        {"triggerType": "interval", "intervalSeconds": 0, "timezone": "UTC"},
        {"triggerType": "interval", "intervalSeconds": 10**30, "timezone": "UTC"},
        {"triggerType": "interval", "intervalMinutes": 3_000_000, "timezone": "UTC"},
    ],
)
def test_pipeline_schedule_rejects_unimplemented_or_invalid_time_contracts(
    schedule: dict[str, object],
) -> None:
    with pytest.raises(ValidationFailed):
        normalize_pipeline_schedule(schedule, is_enabled=True, now=datetime(2026, 1, 1, tzinfo=UTC))


def test_failed_schedule_tick_auto_pauses_at_configured_threshold() -> None:
    row = cast(PipelineScheduleRow, _schedule_row())
    values = completion_values(
        row,
        "2026-01-01T00:00:00Z",
        {"id": "run-a", "status": "failed"},
        {"code": "ADAPTER_FAILURE", "requestId": "request-a"},
        datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        False,
    )

    assert values["status"] == "paused"
    assert values["enabled"] is False
    assert values["failure_count"] == 2
    assert values["paused_reason"] == "consecutive_failures"
    assert values["next_due_at"] is None
    assert values["last_error"] == {"code": "ADAPTER_FAILURE", "requestId": "request-a"}


def test_terminal_observer_applies_failure_only_after_async_run_finishes() -> None:
    schedule = cast(PipelineScheduleRow, _schedule_row())
    run = cast(
        PipelineRunRow,
        {
            "id": "run-a",
            "status": "partial",
            "schedule_slot_at": "2026-01-01T00:00:00Z",
            "error": {"code": "OUTPUT_FAILED"},
        },
    )

    values = terminal_observation_values(schedule, run, "2026-01-01T00:03:00Z")

    assert values["failure_count"] == 2
    assert values["status"] == "paused"
    assert values["enabled"] is False
    assert values["last_terminal_run_id"] == "run-a"
    assert values["last_terminal_status"] == "partial"
    assert values["last_terminal_at"] == "2026-01-01T00:03:00Z"


def test_terminal_observer_resets_consecutive_failures_after_success() -> None:
    schedule = cast(PipelineScheduleRow, _schedule_row())
    run = cast(
        PipelineRunRow,
        {
            "id": "run-success",
            "status": "succeeded",
            "schedule_slot_at": "2026-01-01T00:01:00Z",
            "error": None,
        },
    )

    values = terminal_observation_values(schedule, run, "2026-01-01T00:04:00Z")

    assert values["failure_count"] == 0
    assert values["last_failure_at"] is None
    assert values["last_error"] is None
    assert values["last_terminal_status"] == "succeeded"


def test_cron_payload_resume_and_slot_helpers_preserve_full_schedule_contract() -> None:
    now = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    spec = normalize_pipeline_schedule(
        {
            "triggerType": "cron",
            "cronExpression": "0 9 * * 1-5",
            "timezone": "UTC",
            "startAt": "2026-01-01T00:00:00Z",
            "autoPauseAfterFailures": 3,
        },
        is_enabled=True,
        now=now,
    )

    assert schedule_spec_payload(spec) == {
        "triggerType": "cron",
        "timezone": "UTC",
        "cronExpression": "0 9 * * 1-5",
        "startAt": "2026-01-01T00:00:00Z",
        "autoPauseAfterFailures": 3,
    }
    assert resumed_next_due_at(spec, now) == "2026-01-01T09:00:00Z"
    assert next_due_after_slot(spec, "2026-01-01T09:00:00Z") == "2026-01-02T09:00:00Z"
    assert initial_next_due_at(replace(spec, is_enabled=False), now) is None
    assert pipeline_schedule_spec_from_row(schedule_spec_payload(spec), is_enabled=True).trigger_kind == "cron"


def test_schedule_identity_timestamp_and_clock_helpers_are_stable() -> None:
    payload_a = {"enabled": True, "schedule": {"intervalSeconds": 60, "timezone": "UTC"}}
    payload_b = {"schedule": {"timezone": "UTC", "intervalSeconds": 60}, "enabled": True}

    assert schedule_operation_fingerprint("pipe-a", "upsert", payload_a) == schedule_operation_fingerprint(
        "pipe-a",
        "upsert",
        payload_b,
    )
    assert scheduled_run_idempotency_key("schedule-a", "2026-01-01T00:00:00Z") == (
        "pipeline-schedule:schedule-a:2026-01-01T00:00:00Z"
    )
    assert parse_schedule_timestamp("2026-01-01T09:00:00+09:00", field="slot") == datetime(
        2026,
        1,
        1,
        tzinfo=UTC,
    )
    assert scheduler_now(datetime(2026, 1, 1, 0, 0, 0, 999999, tzinfo=UTC)).microsecond == 0
    assert schedule_iso(datetime(2026, 1, 1, 9, 0, tzinfo=UTC)) == "2026-01-01T09:00:00Z"


@pytest.mark.parametrize(
    "schedule",
    [
        {"triggerType": "interval"},
        {"triggerType": "interval", "everyMinutes": 5, "startAt": "not-a-date"},
        {"triggerType": "cron", "cronExpression": "* * *"},
        {"triggerType": "cron", "cronExpression": "61 * * * *"},
        {"triggerType": "cron", "cronExpression": "10-5 * * * *"},
        {"triggerType": "cron", "cronExpression": "*/bad * * * *"},
        {"triggerType": "cron", "cronExpression": "-1 * * * *"},
        {"triggerType": "cron", "cronExpression": "* * * * *", "autoPauseAfterFailures": "bad"},
    ],
)
def test_pipeline_schedule_rejects_malformed_time_fields(schedule: dict[str, object]) -> None:
    with pytest.raises(ValidationFailed):
        normalize_pipeline_schedule(schedule, is_enabled=True, now=datetime(2026, 1, 1, tzinfo=UTC))


def test_schedule_runtime_rejects_naive_timestamps_and_incomplete_interval_specs() -> None:
    with pytest.raises(ValidationFailed):
        parse_schedule_timestamp("2026-01-01T00:00:00", field="slot")
    with pytest.raises(ValidationFailed):
        scheduler_now(datetime(2026, 1, 1))

    spec = cast(
        PipelineScheduleSpec,
        SimpleNamespace(
            trigger_kind="interval",
            timezone="UTC",
            cron_expression=None,
            interval_seconds=None,
            start_at=None,
            auto_pause_after_failures=None,
            is_enabled=True,
        ),
    )
    with pytest.raises(ValidationFailed):
        initial_next_due_at(spec, datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(ValidationFailed):
        resumed_next_due_at(spec, datetime(2026, 1, 1, tzinfo=UTC))


def _schedule_row() -> dict[str, object]:
    return {
        "id": "schedule-a",
        "tenant_id": "tenant-a",
        "pipeline_id": "pipeline-a",
        "version_id": "version-a",
        "schedule": {
            "triggerType": "interval",
            "timezone": "UTC",
            "intervalSeconds": 60,
            "startAt": "2026-01-01T00:00:00Z",
            "autoPauseAfterFailures": 2,
        },
        "enabled": True,
        "status": "active",
        "updated_by": "user-a",
        "updated_at": "2026-01-01T00:00:00Z",
        "last_tick_at": None,
        "last_slot_at": None,
        "trigger_type": "interval",
        "timezone": "UTC",
        "next_due_at": "2026-01-01T00:00:00Z",
        "runtime_config_updated_at": "2026-01-01T00:00:00Z",
        "lease_owner": "worker-a",
        "lease_token": "lease-a",
        "lease_expires_at": "2026-01-01T00:01:00Z",
        "fencing_token": 1,
        "failure_count": 1,
        "paused_reason": None,
        "last_failure_at": "2025-12-31T23:59:00Z",
        "last_error": {"code": "OLD"},
    }
