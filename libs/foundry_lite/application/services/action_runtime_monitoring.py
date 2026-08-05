"""Deterministic 30-day monitoring and alert evaluation for Action runs."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import cast

from foundry_lite.application.ports.action_repository import ActionRunRow

ACTION_MONITORING_WINDOW_DAYS = 30
ACTION_MONITORING_RUN_LIMIT = 10_000


def action_monitoring_window(observed_at: datetime | None = None) -> tuple[str, str]:
    """Return the inclusive UTC monitoring window persisted in the response."""
    ended_at = observed_at or datetime.now(UTC)
    started_at = ended_at - timedelta(days=ACTION_MONITORING_WINDOW_DAYS)
    return started_at.isoformat(), ended_at.isoformat()


def action_monitoring_bucket(observed_at: datetime | None = None) -> str:
    """Return the UTC hour used to deduplicate external alert delivery."""
    current = (observed_at or datetime.now(UTC)).astimezone(UTC)
    return current.replace(minute=0, second=0, microsecond=0).isoformat()


def action_monitoring_alert_payload(
    alert: Mapping[str, object], *, started_at: str, ended_at: str, bucket: str
) -> dict[str, object]:
    """Build the non-sensitive external event payload for one active policy."""
    return {
        "policyId": str(alert["policyId"]),
        "value": alert["value"],
        "threshold": alert["threshold"],
        "window": {"startsAt": started_at, "endsAt": ended_at},
        "deliveryBucket": bucket,
    }


def action_runtime_monitoring_payload(
    rows: list[ActionRunRow],
    effect_counts: Mapping[str, int],
    *,
    started_at: str,
    ended_at: str,
) -> dict[str, object]:
    """Build bounded metrics, taxonomy, and machine-readable alert decisions."""
    visible = rows[:ACTION_MONITORING_RUN_LIMIT]
    duration_values = [value for row in visible if (value := _duration_ms(row)) is not None]
    terminal = [row for row in visible if row["completed_at"] is not None]
    failures = [row for row in terminal if row["status"] in _FAILURE_STATUSES]
    failure = _failure_payload(failures, terminal)
    effects = _effect_payload(effect_counts)
    duration_summary: dict[str, int | None] = {
        "p95": _nearest_rank_p95(duration_values),
        "terminalSample": len(duration_values),
    }
    return {
        "window": _window_payload(rows, visible, started_at, ended_at),
        "durationMs": duration_summary,
        "failure": failure,
        "effects": effects,
        "alerts": _alert_payload(duration_summary, failure, effects),
    }


def _window_payload(
    rows: list[ActionRunRow], visible: list[ActionRunRow], started_at: str, ended_at: str
) -> dict[str, object]:
    """Describe the bounded query window and truncation state."""
    return {
        "days": ACTION_MONITORING_WINDOW_DAYS,
        "startsAt": started_at,
        "endsAt": ended_at,
        "maxRuns": ACTION_MONITORING_RUN_LIMIT,
        "observedRuns": len(visible),
        "isTruncated": len(rows) > ACTION_MONITORING_RUN_LIMIT,
    }


def _failure_payload(failures: list[ActionRunRow], terminal: list[ActionRunRow]) -> dict[str, object]:
    """Aggregate terminal failures by status and durable error kind."""
    return {
        "count": len(failures),
        "rate": round(len(failures) / len(terminal), 4) if terminal else 0.0,
        "terminalSample": len(terminal),
        "byStatus": _counts(str(row["status"]) for row in failures),
        "byErrorKind": _counts(_error_kind(row) for row in failures),
    }


def _effect_payload(effect_counts: Mapping[str, int]) -> dict[str, int]:
    """Aggregate pending, dead-lettered, and ambiguous effects."""
    backlog = sum(effect_counts.get(status, 0) for status in ("pending", "delivering", "retry_wait"))
    return {
        "deliveryBacklog": backlog,
        "deadLetter": effect_counts.get("dead_letter", 0),
        "outcomeUnknown": effect_counts.get("outcome_unknown", 0),
    }


def _alert_payload(
    duration: Mapping[str, object], failure: Mapping[str, object], effects: Mapping[str, int]
) -> dict[str, object]:
    """Evaluate versioned monitoring policies into active alerts."""
    policies = _alert_policies()
    values = _alert_values(duration, failure, effects)
    active = [
        {"policyId": policy["policyId"], "value": values[str(policy["metric"])], "threshold": policy["threshold"]}
        for policy in policies
        if _policy_is_active(policy, values, failure, duration)
    ]
    return {"policies": policies, "active": active}


def _alert_values(
    duration: Mapping[str, object], failure: Mapping[str, object], effects: Mapping[str, int]
) -> dict[str, float]:
    """Normalize monitoring metrics into numeric alert inputs."""
    return {
        "failure_rate": _number(failure["rate"]),
        "p95_duration_ms": _number(duration["p95"]),
        "effect_backlog": float(effects["deliveryBacklog"]),
        "effect_dead_letter": float(effects["deadLetter"]),
        "effect_outcome_unknown": float(effects["outcomeUnknown"]),
    }


def _alert_policies() -> list[dict[str, object]]:
    """Return the deterministic built-in Action alert policy set."""
    return [
        _policy("action-failure-rate", "failure_rate", 0.05, 20),
        _policy("action-p95-duration", "p95_duration_ms", 10_000, 20),
        _policy("action-effect-backlog", "effect_backlog", 100, 0),
        _policy("action-effect-dead-letter", "effect_dead_letter", 0, 0),
        _policy("action-effect-outcome-unknown", "effect_outcome_unknown", 0, 0),
    ]


def _policy(policy_id: str, metric: str, threshold: float, minimum_sample: int) -> dict[str, object]:
    """Build one machine-readable greater-than alert policy."""
    return {
        "policyId": policy_id,
        "metric": metric,
        "operator": "gt",
        "threshold": threshold,
        "minimumSample": minimum_sample,
    }


def _policy_is_active(
    policy: Mapping[str, object],
    values: Mapping[str, float],
    failure: Mapping[str, object],
    duration: Mapping[str, object],
) -> bool:
    """Apply threshold and minimum-sample rules to one policy."""
    metric = str(policy["metric"])
    sample_value = duration["terminalSample"] if metric == "p95_duration_ms" else failure["terminalSample"]
    sample = int(cast(int | float, sample_value))
    if metric.startswith("effect_"):
        sample = 0
    minimum_sample = int(cast(int | float, policy["minimumSample"]))
    threshold = float(cast(int | float, policy["threshold"]))
    return sample >= minimum_sample and values[metric] > threshold


def _number(value: object) -> float:
    """Convert an already validated monitoring number with a null default."""
    if value is None:
        return 0.0
    return float(cast(int | float, value))


def _error_kind(row: ActionRunRow) -> str:
    """Extract a stable failure taxonomy label from a run row."""
    error = row.get("error")
    if not isinstance(error, Mapping):
        return "unknown"
    value = error.get("kind") or error.get("code") or error.get("errorKind")
    return str(value) if value else "unknown"


def _counts(values: Iterable[str]) -> dict[str, int]:
    """Return deterministic frequency counts for monitoring labels."""
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _duration_ms(row: ActionRunRow) -> int | None:
    """Compute a non-negative terminal run duration in milliseconds."""
    completed_at = row["completed_at"]
    if completed_at is None:
        return None
    try:
        started = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        completed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, round((completed - started).total_seconds() * 1_000))


def _nearest_rank_p95(values: list[int]) -> int | None:
    """Compute the deterministic nearest-rank p95 duration."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


_FAILURE_STATUSES = {"failed", "conflict", "outcome_unknown", "compensation_required"}
