from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.action_repository import ActionRunRecord, ActionRunRow
from foundry_lite.application.services.action_runtime_monitoring import (
    action_monitoring_bucket,
    action_monitoring_window,
    action_runtime_monitoring_payload,
)
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies


def _run(index: int, status: str, error_kind: str | None = None) -> ActionRunRow:
    error = {"kind": error_kind} if error_kind else None
    return cast(
        ActionRunRow,
        {
            "id": f"run-{index}",
            "status": status,
            "error": error,
            "created_at": "2026-08-04T00:00:00+00:00",
            "completed_at": "2026-08-04T00:00:12+00:00",
        },
    )


def test_monitoring_window_is_exactly_thirty_days() -> None:
    started_at, ended_at = action_monitoring_window(datetime(2026, 8, 4, tzinfo=UTC))

    assert started_at == "2026-07-05T00:00:00+00:00"
    assert ended_at == "2026-08-04T00:00:00+00:00"
    assert action_monitoring_bucket(datetime(2026, 8, 4, 7, 42, tzinfo=UTC)) == "2026-08-04T07:00:00+00:00"


def test_monitoring_classifies_failures_and_evaluates_alert_policies() -> None:
    rows = [_run(index, "failed", "TRANSIENT_ADAPTER") for index in range(2)]
    rows.extend(_run(index, "succeeded") for index in range(2, 20))

    payload = action_runtime_monitoring_payload(
        rows,
        {"pending": 101, "dead_letter": 1, "outcome_unknown": 1},
        started_at="2026-07-05T00:00:00+00:00",
        ended_at="2026-08-04T00:00:00+00:00",
    )

    assert payload["failure"] == {
        "count": 2,
        "rate": 0.1,
        "terminalSample": 20,
        "byStatus": {"failed": 2},
        "byErrorKind": {"TRANSIENT_ADAPTER": 2},
    }
    assert payload["durationMs"] == {"p95": 12_000, "terminalSample": 20}
    active = {item["policyId"] for item in payload["alerts"]["active"]}
    assert active == {
        "action-failure-rate",
        "action-p95-duration",
        "action-effect-backlog",
        "action-effect-dead-letter",
        "action-effect-outcome-unknown",
    }


def test_active_monitoring_alerts_reach_external_stream_once_per_hour(tmp_path: Path) -> None:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "runtime")
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    observed_at = datetime(2026, 8, 5, 0, 30, tzinfo=UTC)
    _insert_monitoring_runs(foundry, count=20, failed=2)

    first = foundry._services.action.monitoring_alerts.publish(ctx=ctx, observed_at=observed_at)
    replay = foundry._services.action.monitoring_alerts.publish(ctx=ctx, observed_at=observed_at)
    delivery = foundry.operations.publish_pending_outbox(ctx=ctx, stream_name="action-alerts", limit=10)
    events = dependencies.stream_adapter.read_events("action-alerts")

    assert first["active"] == 2
    assert first["published"] == 2
    assert replay["active"] == 2
    assert replay["published"] == 0
    assert delivery["published"] == 2
    assert {event.event_type for event in events} == {"action.monitoring.alert.triggered"}
    assert {event.payload["aggregateId"] for event in events} == {
        "action-failure-rate",
        "action-p95-duration",
    }
    assert all(event.payload["payload"]["deliveryBucket"] == first["bucket"] for event in events)


def _insert_monitoring_runs(foundry: FoundryLite, *, count: int, failed: int) -> None:
    with foundry.engine.begin() as transaction:
        for index in range(count):
            status = "failed" if index < failed else "succeeded"
            foundry._services.action.log_revert.action_repository.insert_action_run(
                transaction=transaction,
                record=_monitoring_record(index, status),
            )


def _monitoring_record(index: int, status: str) -> ActionRunRecord:
    return ActionRunRecord(
        action_run_id=f"monitor-run-{index}",
        tenant_id="tenant-demo",
        action_type_id="monitor-action",
        action_type_api_name="MonitorAction",
        actor_user_id="monitor-user",
        target_object_type_id="monitor-object-type",
        target_object_type_api_name="Order",
        target_object_id=f"O-{index}",
        expected_object_version=1,
        parameters={},
        status=status,
        idempotency_key=f"monitor-{index}",
        request_fingerprint=f"sha256:monitor-{index}",
        result={} if status == "succeeded" else None,
        error={"kind": "transient_adapter"} if status == "failed" else None,
        created_at="2026-08-04T00:00:00+00:00",
        completed_at="2026-08-04T00:00:12+00:00",
    )
