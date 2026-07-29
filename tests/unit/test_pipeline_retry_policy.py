from __future__ import annotations

from datetime import UTC, datetime

import pytest
from foundry_lite.application.services.pipeline_retry_policy import pipeline_retry_decision


@pytest.mark.parametrize(
    "kind",
    [
        "authorization",
        "validation",
        "security",
        "cost_limit",
        "out_of_memory",
        "cancellation",
        "commit_outcome_unknown",
        "reconciliation_required",
    ],
)
def test_permanent_failures_are_never_retried(kind: str) -> None:
    decision = pipeline_retry_decision(
        {"kind": kind},
        attempt_number=1,
        maximum_attempts=3,
        requires_stable_idempotency=False,
        has_stable_idempotency=False,
    )

    assert decision.is_retryable is False
    assert decision.retry_at is None


def test_transient_failure_uses_deterministic_exponential_backoff() -> None:
    decision = pipeline_retry_decision(
        {"kind": "adapter_transient"},
        attempt_number=2,
        maximum_attempts=3,
        requires_stable_idempotency=False,
        has_stable_idempotency=False,
        now=datetime(2026, 7, 29, tzinfo=UTC),
    )

    assert decision.is_retryable is True
    assert decision.backoff_seconds == 2
    assert decision.retry_at == "2026-07-29T00:00:02Z"


def test_external_model_retry_requires_stable_idempotency_coordinates() -> None:
    decision = pipeline_retry_decision(
        {"kind": "safe_timeout"},
        attempt_number=1,
        maximum_attempts=3,
        requires_stable_idempotency=True,
        has_stable_idempotency=False,
    )

    assert decision.is_retryable is False
    assert decision.error_kind == "idempotency_unproven"


def test_retry_budget_is_bounded() -> None:
    decision = pipeline_retry_decision(
        {"kind": "worker_lost"},
        attempt_number=3,
        maximum_attempts=3,
        requires_stable_idempotency=False,
        has_stable_idempotency=False,
    )

    assert decision.is_retryable is False
    assert decision.error_kind == "attempts_exhausted"
