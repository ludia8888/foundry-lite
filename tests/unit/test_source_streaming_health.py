from __future__ import annotations

from datetime import UTC, datetime, timedelta

from foundry_lite.application.services.source_streaming_health import streaming_health_view


def test_streaming_health_reports_checkpoint_lag_and_failure_rules() -> None:
    observed_at = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    health = streaming_health_view(
        {
            "config_summary": {
                "monitoring": {
                    "checkpointLivenessSeconds": 30,
                    "maxCheckpointDurationMs": 1_000,
                    "maxBrokerLag": 10,
                    "minOutputRatePerSecond": 5,
                }
            }
        },
        {
            "lastCheckpointAt": (observed_at - timedelta(seconds=31)).isoformat(),
            "checkpointDurationMs": 1_500,
            "outputRatePerSecond": 2,
            "consecutiveFailures": 1,
            "kafka": {"brokerLag": 11},
        },
        is_worker_stale=False,
        is_active=True,
        observed_at=observed_at,
    )

    statuses = {rule["ruleId"]: rule["status"] for rule in health["rules"]}
    assert health["status"] == "UNHEALTHY"
    assert statuses == {
        "worker_heartbeat": "PASS",
        "checkpoint_liveness": "FAIL",
        "checkpoint_duration": "WARN",
        "total_lag": "WARN",
        "output_throughput": "WARN",
        "consecutive_failures": "FAIL",
    }


def test_streaming_health_keeps_optional_thresholds_disabled() -> None:
    observed_at = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    health = streaming_health_view(
        {"config_summary": {}},
        {
            "lastCheckpointAt": observed_at.isoformat(),
            "consecutiveFailures": 0,
            "kafka": {"brokerLag": 999_999},
        },
        is_worker_stale=False,
        is_active=True,
        observed_at=observed_at,
    )

    statuses = {rule["ruleId"]: rule["status"] for rule in health["rules"]}
    assert health["status"] == "HEALTHY"
    assert statuses["checkpoint_liveness"] == "PASS"
    assert statuses["total_lag"] == "DISABLED"
    assert statuses["checkpoint_duration"] == "DISABLED"
