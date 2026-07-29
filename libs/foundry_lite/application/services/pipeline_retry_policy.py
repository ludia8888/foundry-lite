"""Pinned retry classification for distributed Pipeline node attempts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

_PERMANENT_KINDS = frozenset(
    {
        "authorization",
        "validation",
        "security",
        "cost_limit",
        "out_of_memory",
        "cancellation",
        "commit_outcome_unknown",
        "reconciliation_required",
    }
)
_RETRYABLE_KINDS = frozenset({"worker_lost", "adapter_transient", "safe_timeout"})


@dataclass(frozen=True, slots=True)
class PipelineRetryDecision:
    """Deterministic decision returned after classifying a node failure."""

    is_retryable: bool
    error_kind: str
    retry_at: str | None
    backoff_seconds: int | None


def pipeline_retry_decision(
    error: Mapping[str, object],
    *,
    attempt_number: int,
    maximum_attempts: int,
    requires_stable_idempotency: bool,
    has_stable_idempotency: bool,
    now: datetime | None = None,
) -> PipelineRetryDecision:
    """Classify a failure and calculate its bounded deterministic retry time."""

    kind = _error_kind(error)
    if kind in _PERMANENT_KINDS or kind not in _RETRYABLE_KINDS:
        return PipelineRetryDecision(False, kind, None, None)
    if requires_stable_idempotency and not has_stable_idempotency:
        return PipelineRetryDecision(False, "idempotency_unproven", None, None)
    if attempt_number >= maximum_attempts:
        return PipelineRetryDecision(False, "attempts_exhausted", None, None)
    backoff = min(30, 2 ** max(0, attempt_number - 1))
    retry_at = (now or datetime.now(UTC)) + timedelta(seconds=backoff)
    return PipelineRetryDecision(True, kind, retry_at.isoformat().replace("+00:00", "Z"), backoff)


def _error_kind(error: Mapping[str, object]) -> str:
    value = error.get("kind") or error.get("errorKind") or error.get("code")
    return str(value or "unknown").strip().casefold()
