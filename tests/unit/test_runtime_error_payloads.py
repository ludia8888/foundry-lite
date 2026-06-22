from __future__ import annotations

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailure
from foundry_lite.application.services.runtime_error_payloads import dead_letter_retry_plan, runtime_error_payload
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed


def test_runtime_error_payload_handles_generic_error_without_trace() -> None:
    payload = runtime_error_payload(ValueError("bad value"))

    assert payload == {"type": "ValueError", "message": "bad value", "details": {}}


def test_runtime_error_payload_uses_request_context_when_run_is_missing() -> None:
    ctx = RequestContext(tenant_id="tenant-a", actor_user_id="user-a", request_id="req-a")

    payload = runtime_error_payload(RuntimeError("boom"), ctx)

    assert payload["trace"] == {
        "tenant_id": "tenant-a",
        "actor_user_id": "user-a",
        "request_id": "req-a",
        "correlation_id": "req-a",
    }


def test_runtime_error_payload_adds_domain_error_and_run_trace() -> None:
    ctx = RequestContext(tenant_id="tenant-a", actor_user_id="user-a", request_id="req-a")

    payload = runtime_error_payload(
        ValidationFailed("invalid order", details={"field": "amount"}),
        ctx,
        run_id="run-1",
        adapter="cdc",
    )

    assert payload["type"] == "VALIDATION_FAILED"
    assert payload["details"] == {"field": "amount"}
    assert payload["trace"] == {
        "tenant_id": "tenant-a",
        "actor_user_id": "user-a",
        "request_id": "req-a",
        "run_id": "run-1",
        "correlation_id": "run-1",
        "adapter": "cdc",
    }


def test_runtime_error_payload_prefers_explicit_correlation_for_adapter_error() -> None:
    failure = AdapterFailure(
        adapter_profile="elastic.local",
        operation="bulk_index",
        kind="timeout",
        is_retryable=True,
        operator_message="search adapter timed out",
        timeout_seconds=30,
        idempotency_key="index-run-1",
        details={"host": "localhost"},
    )

    payload = runtime_error_payload(
        AdapterError(failure),
        RequestContext(tenant_id="tenant-a", actor_user_id="user-a", request_id="req-a"),
        correlation_id="corr-1",
    )

    assert payload["type"] == "ADAPTER_FAILURE"
    assert payload["adapterFailure"]["kind"] == "timeout"
    assert payload["adapterFailure"]["retryable"] is True
    assert payload["trace"]["correlation_id"] == "corr-1"
    assert "run_id" not in payload["trace"]


class _DeadLetterPlanRepository:
    def __init__(self, dead_letter: dict[str, object] | None, outbox_rows: list[dict[str, object]]) -> None:
        self.dead_letter = dead_letter
        self.outbox_rows = outbox_rows

    def dead_letter_event_by_id(self, **_kwargs: object) -> dict[str, object] | None:
        return self.dead_letter

    def rows_for_tenant(self, **kwargs: object) -> list[dict[str, object]]:
        assert kwargs["table"] == "outbox_events"
        return self.outbox_rows


def test_dead_letter_retry_plan_rejects_missing_dead_letter() -> None:
    repository = _DeadLetterPlanRepository(None, [])

    with pytest.raises(NotFound, match="dead-letter event not found"):
        dead_letter_retry_plan(repository, object(), RequestContext(), "dlq-1")


def test_dead_letter_retry_plan_rejects_malformed_source_event_id() -> None:
    repository = _DeadLetterPlanRepository({"event_type": "materialization.requested", "payload": {}}, [])

    with pytest.raises(ValidationFailed, match="dead-letter event is not retryable") as exc_info:
        dead_letter_retry_plan(repository, object(), RequestContext(), "dlq-1")

    assert exc_info.value.details == {"event_id": "dlq-1", "field": "source_event_id"}


def test_dead_letter_retry_plan_requires_source_outbox_event() -> None:
    repository = _DeadLetterPlanRepository(
        {"source_event_id": "outbox-1", "event_type": "materialization.requested", "payload": {}},
        [],
    )

    with pytest.raises(NotFound, match="source outbox event not found"):
        dead_letter_retry_plan(repository, object(), RequestContext(), "dlq-1")


def test_dead_letter_retry_plan_rejects_non_mapping_payload() -> None:
    repository = _DeadLetterPlanRepository(
        {"source_event_id": "outbox-1", "event_type": "materialization.requested", "payload": "raw"},
        [{"id": "outbox-1"}],
    )

    with pytest.raises(ValidationFailed, match="dead-letter event is not retryable") as exc_info:
        dead_letter_retry_plan(repository, object(), RequestContext(), "dlq-1")

    assert exc_info.value.details == {"event_id": "dlq-1", "field": "payload"}
