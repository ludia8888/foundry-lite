from __future__ import annotations

from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailure
from foundry_lite.application.services.runtime_error_payloads import runtime_error_payload
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


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
