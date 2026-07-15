"""Read-only health rules for durable Source streaming workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime


def streaming_health_view(
    sync: Mapping[str, object],
    telemetry: Mapping[str, object],
    *,
    is_worker_stale: bool,
    is_active: bool,
    observed_at: datetime,
) -> dict[str, object]:
    if not is_active:
        return {"status": "STOPPED", "rules": [], "evaluatedAt": observed_at.isoformat()}
    monitoring = _mapping(_mapping(sync.get("config_summary")).get("monitoring"))
    rules = [
        _heartbeat_rule(is_worker_stale),
        _checkpoint_liveness_rule(telemetry, monitoring, observed_at),
        _maximum_rule(
            telemetry, monitoring, "checkpointDurationMs", "maxCheckpointDurationMs", "checkpoint_duration", "ms"
        ),
        _maximum_rule(
            _mapping(telemetry.get("kafka")), monitoring, "brokerLag", "maxBrokerLag", "total_lag", "records"
        ),
        _minimum_rule(
            telemetry, monitoring, "outputRatePerSecond", "minOutputRatePerSecond", "output_throughput", "records/s"
        ),
        _failure_rule(telemetry),
    ]
    return {
        "status": _overall_status(rules),
        "rules": rules,
        "evaluatedAt": observed_at.isoformat(),
        "monitoringProfile": "source_streaming/v1",
    }


def _heartbeat_rule(is_worker_stale: bool) -> dict[str, object]:
    return _rule(
        "worker_heartbeat",
        "FAIL" if is_worker_stale else "PASS",
        "Worker lease heartbeat",
        "Worker lease expired; a healthy replica must take over." if is_worker_stale else "Worker lease is current.",
        "Start or inspect a standby streaming worker." if is_worker_stale else None,
    )


def _checkpoint_liveness_rule(
    telemetry: Mapping[str, object], monitoring: Mapping[str, object], observed_at: datetime
) -> dict[str, object]:
    threshold = _number(monitoring.get("checkpointLivenessSeconds"), 60.0)
    age = _age_seconds(telemetry.get("lastCheckpointAt"), observed_at)
    if age is None:
        return _metric_rule("checkpoint_liveness", "PENDING", "Checkpoint liveness", None, threshold, "seconds")
    status = "FAIL" if age > threshold else "PASS"
    return _metric_rule("checkpoint_liveness", status, "Checkpoint liveness", round(age, 3), threshold, "seconds")


def _maximum_rule(
    values: Mapping[str, object],
    monitoring: Mapping[str, object],
    value_key: str,
    threshold_key: str,
    rule_id: str,
    unit: str,
) -> dict[str, object]:
    threshold = _optional_number(monitoring.get(threshold_key))
    observed = _optional_number(values.get(value_key))
    if threshold is None:
        return _metric_rule(rule_id, "DISABLED", _rule_label(rule_id), observed, None, unit)
    status = "WARN" if observed is not None and observed > threshold else ("PENDING" if observed is None else "PASS")
    return _metric_rule(rule_id, status, _rule_label(rule_id), observed, threshold, unit)


def _minimum_rule(
    values: Mapping[str, object],
    monitoring: Mapping[str, object],
    value_key: str,
    threshold_key: str,
    rule_id: str,
    unit: str,
) -> dict[str, object]:
    threshold = _optional_number(monitoring.get(threshold_key))
    observed = _optional_number(values.get(value_key))
    if threshold is None:
        return _metric_rule(rule_id, "DISABLED", _rule_label(rule_id), observed, None, unit)
    status = "WARN" if observed is not None and observed < threshold else ("PENDING" if observed is None else "PASS")
    return _metric_rule(rule_id, status, _rule_label(rule_id), observed, threshold, unit)


def _failure_rule(telemetry: Mapping[str, object]) -> dict[str, object]:
    failures = _number(telemetry.get("consecutiveFailures"), 0)
    status = "FAIL" if failures > 0 else "PASS"
    return _metric_rule("consecutive_failures", status, "Consecutive failures", failures, 0, "failures")


def _metric_rule(
    rule_id: str,
    status: str,
    label: str,
    observed: float | None,
    threshold: float | None,
    unit: str,
) -> dict[str, object]:
    action = "Inspect the latest sync run and worker error trace." if status in {"WARN", "FAIL"} else None
    return {
        **_rule(rule_id, status, label, _metric_message(status, label), action),
        "observedValue": observed,
        "threshold": threshold,
        "unit": unit,
    }


def _rule(rule_id: str, status: str, label: str, message: str, action: str | None) -> dict[str, object]:
    return {"ruleId": rule_id, "status": status, "label": label, "message": message, "operatorAction": action}


def _overall_status(rules: Sequence[Mapping[str, object]]) -> str:
    statuses = {rule.get("status") for rule in rules}
    if "FAIL" in statuses:
        return "UNHEALTHY"
    if "WARN" in statuses:
        return "DEGRADED"
    if "PENDING" in statuses and "PASS" not in statuses:
        return "PENDING"
    return "HEALTHY"


def _metric_message(status: str, label: str) -> str:
    if status == "WARN":
        return f"{label} crossed its configured warning threshold."
    if status == "FAIL":
        return f"{label} is unhealthy."
    if status == "PENDING":
        return f"{label} is waiting for its first observation."
    if status == "DISABLED":
        return f"{label} monitor is not configured."
    return f"{label} is within threshold."


def _rule_label(rule_id: str) -> str:
    return {
        "checkpoint_duration": "Checkpoint duration",
        "total_lag": "Total lag",
        "output_throughput": "Output throughput",
    }[rule_id]


def _age_seconds(value: object, observed_at: datetime) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, (observed_at - parsed).total_seconds())


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _number(value: object, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _optional_number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
