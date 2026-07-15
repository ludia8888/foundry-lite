from __future__ import annotations

from datetime import UTC, datetime

import pytest
from foundry_lite.application.services.source_scheduler_config import (
    scheduler_idempotency_key,
    source_schedule_decision,
)
from foundry_lite.domain.errors import ValidationFailed


def test_source_schedule_cron_accepts_midnight_zero_values() -> None:
    decision = source_schedule_decision(
        {
            "sync_name": "daily_orders",
            "created_at": "2026-01-01T00:00:00Z",
            "schedule": {"mode": "cron", "cron": "0 0 * * *"},
        },
        [],
        now=datetime(2026, 1, 2, 0, 0, tzinfo=UTC),
    )

    assert decision.as_dict()["due"] is True
    assert decision.as_dict()["slotStart"] == "2026-01-02T00:00:00Z"


def test_source_schedule_rejects_invalid_datetime_as_domain_error() -> None:
    with pytest.raises(ValidationFailed, match="ISO-8601"):
        source_schedule_decision(
            {
                "sync_name": "bad_schedule",
                "created_at": "not-a-date",
                "schedule": {"mode": "interval", "everySeconds": 60},
            },
            [],
            now=datetime(2026, 1, 2, 0, 0, tzinfo=UTC),
        )


def test_source_schedule_disabled_manual_and_not_due_interval_edges() -> None:
    manual = source_schedule_decision(
        {"sync_name": "manual_sync", "schedule": {"mode": "manual"}},
        [],
        now=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
    )
    not_due = source_schedule_decision(
        {
            "sync_name": "future_sync",
            "created_at": "2026-01-01T00:00:00Z",
            "schedule": {"mode": "interval", "everySeconds": "60", "startAt": "2026-01-01T00:10:00Z"},
        },
        [],
        now=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
    )

    assert manual.as_dict()["reason"] == "manual_schedule"
    assert manual.as_dict()["enabled"] is False
    assert not_due.as_dict()["reason"] == "not_due"
    assert not_due.as_dict()["nextDueAt"] == "2026-01-01T00:10:00Z"


def test_source_schedule_future_cron_anchor_reports_first_matching_slot() -> None:
    decision = source_schedule_decision(
        {
            "sync_name": "future_daily_sync",
            "created_at": "2026-01-01T00:00:00Z",
            "schedule": {"mode": "cron", "cron": "0 0 * * *", "startAt": "2026-01-02T00:00:00Z"},
        },
        [],
        now=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
    )

    assert decision.as_dict()["reason"] == "not_due"
    assert decision.as_dict()["nextDueAt"] == "2026-01-02T00:00:00Z"


def test_source_schedule_skips_active_run_and_already_started_slot() -> None:
    slot = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)
    idempotency_key = scheduler_idempotency_key("hourly_sync", slot)
    active = source_schedule_decision(
        {
            "sync_name": "hourly_sync",
            "created_at": "2026-01-01T00:00:00Z",
            "schedule": {"mode": "interval", "everySeconds": 3600, "batchLimit": "3"},
        },
        [{"status": "running"}],
        now=slot,
    )
    already_started = source_schedule_decision(
        {
            "sync_name": "hourly_sync",
            "created_at": "2026-01-01T00:00:00Z",
            "schedule": {"mode": "interval", "everySeconds": 3600},
        },
        [{"status": "succeeded", "idempotency_key": idempotency_key}],
        now=slot,
    )

    assert active.as_dict()["reason"] == "active_run_in_progress"
    assert active.as_dict()["batchLimit"] == 3
    assert already_started.as_dict()["reason"] == "slot_already_started"
    assert already_started.as_dict()["idempotencyKey"] == idempotency_key


@pytest.mark.parametrize(
    ("schedule", "message"),
    [
        ({"mode": "cron"}, "requires cron"),
        ({"mode": "cron", "cron": "* * *"}, "five fields"),
        ({"mode": "cron", "cron": "60 * * * *"}, "outside allowed range"),
        ({"mode": "cron", "cron": "*/0 * * * *"}, "positive integer"),
        ({"mode": "interval", "everySeconds": True}, "cannot be boolean"),
        ({"mode": "interval", "everySeconds": object()}, "must be an integer"),
        ({"mode": "interval", "everySeconds": "soon"}, "must be an integer"),
        ({"mode": "unknown"}, "unsupported"),
    ],
)
def test_source_schedule_rejects_invalid_schedule_shapes(schedule: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationFailed, match=message):
        source_schedule_decision(
            {"sync_name": "bad_sync", "created_at": "2026-01-01T00:00:00Z", "schedule": schedule},
            [],
            now=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        )


def test_source_schedule_cron_reports_no_recent_or_upcoming_slot() -> None:
    with pytest.raises(ValidationFailed, match="no recent matching slot"):
        source_schedule_decision(
            {
                "sync_name": "old_anchor",
                "created_at": "2026-01-01T00:00:00Z",
                "schedule": {"mode": "cron", "cron": "0 0 1 1 *"},
            },
            [],
            now=datetime(2026, 1, 20, 0, 0, tzinfo=UTC),
        )

    with pytest.raises(ValidationFailed, match="no upcoming matching slot"):
        source_schedule_decision(
            {
                "sync_name": "future_anchor",
                "created_at": "2026-01-01T00:00:00Z",
                "schedule": {"mode": "cron", "cron": "0 0 1 1 *", "startAt": "2026-01-02T00:00:00Z"},
            },
            [],
            now=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        )
