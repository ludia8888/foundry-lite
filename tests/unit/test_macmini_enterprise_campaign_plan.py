from __future__ import annotations

from datetime import UTC, datetime

from scripts.operations.macmini_enterprise_campaign_plan import (
    CAMPAIGN_DURATION_SECONDS,
    EVENTS,
    PHASES,
    build_campaign_plan,
    fault_windows,
)


def test_campaign_plan_covers_exact_24_hours_without_phase_gaps() -> None:
    assert CAMPAIGN_DURATION_SECONDS == 86400
    assert PHASES[0].start_second == 0
    assert PHASES[-1].end_second == 86400
    assert all(left.end_second == right.start_second for left, right in zip(PHASES, PHASES[1:], strict=False))


def test_campaign_contains_all_requested_fault_families_and_quiet_soak() -> None:
    values = {event.value for event in EVENTS}

    assert {"api-pod", "worker-pod", "controller-pod", "worker-oom"} <= values
    assert {
        "dependency-postgresql",
        "dependency-minio",
        "dependency-temporal",
        "dependency-redpanda",
        "dependency-elasticsearch",
        "dependency-clamav",
    } <= values
    assert {
        "network-latency",
        "network-packet-loss",
        "network-connection-reset",
        "network-dns-failure",
    } <= values
    assert PHASES[-1].phase_id == "quiet"
    assert PHASES[-1].end_second - PHASES[-1].start_second == 7200


def test_fault_windows_are_absolute_and_only_cover_mutating_events() -> None:
    started = datetime(2026, 8, 18, tzinfo=UTC)
    windows = fault_windows(started)
    plan = build_campaign_plan(started)

    assert plan["endedAt"] == "2026-08-19T00:00:00+00:00"
    assert windows[0]["eventId"] == "api-pod-delete"
    assert all(window["eventId"] != "tenant-a-flood-tenant-b-business" for window in windows)
