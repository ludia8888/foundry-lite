from __future__ import annotations

from typing import cast

import pytest
from foundry_lite.application.ports import RuntimeRepository
from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailure
from foundry_lite.application.services.runtime_error_payloads import (
    dead_letter_retry_plan,
    record_runtime_cleanup_failure,
    record_runtime_operations_evidence,
    runtime_error_payload,
    runtime_operations_evidence,
)
from foundry_lite.application.services.runtime_redaction import redact_sensitive
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.error_redaction import scrub_error_mapping, scrub_error_text
from foundry_lite.domain.errors import NotFound, ValidationFailed


def test_runtime_error_payload_handles_generic_error_without_trace() -> None:
    payload = runtime_error_payload(ValueError("bad value"))

    assert payload == {"type": "ValueError", "message": "bad value", "details": {}}


def test_error_redaction_preserves_safe_diagnostics_that_name_security_concepts() -> None:
    assert scrub_error_text("citation navigation signature is invalid") == ("citation navigation signature is invalid")
    assert scrub_error_text("providerRequestId_text_required") == "providerRequestId_text_required"
    assert scrub_error_text("database password authentication failed") == "database password authentication failed"
    assert scrub_error_text("prompt artifact store unavailable") == "***MASKED***"
    assert scrub_error_text("database password=raw-secret") == "***MASKED***"
    assert scrub_error_mapping({"signature": "raw", "status": "failed"}) == {
        "signature": "***MASKED***",
        "status": "failed",
    }


def test_runtime_error_payload_preserves_redacted_secondary_cleanup_evidence() -> None:
    primary = RuntimeError("primary failure")
    cleanup = RuntimeError("private cleanup failure")
    record_runtime_cleanup_failure(
        primary,
        operation="mediaTransactionAbort",
        cleanup_error=cleanup,
    )

    payload = runtime_error_payload(primary)

    assert payload["type"] == "RuntimeError"
    assert payload["message"] == "primary failure"
    assert payload["details"] == {
        "cleanupFailures": [
            {
                "operation": "mediaTransactionAbort",
                "status": "FAILED",
                "exceptionType": "RuntimeError",
            }
        ]
    }
    assert "private cleanup failure" not in str(payload)


def test_runtime_error_payload_exposes_only_safe_operations_coordinates() -> None:
    primary = RuntimeError("postgresql://alice:secret@db.internal/private")
    record_runtime_operations_evidence(
        primary,
        run_type="source_exploration",
        run_id="source_explore_1",
    )

    payload = runtime_error_payload(primary)

    assert runtime_operations_evidence(primary) == {
        "runType": "source_exploration",
        "runId": "source_explore_1",
        "operationsPath": "/api/operations/runs/source_exploration/source_explore_1",
    }
    assert payload["details"] == {"operationsEvidence": runtime_operations_evidence(primary)}
    assert "alice" not in str(payload)

    unsafe = RuntimeError("primary")
    record_runtime_operations_evidence(unsafe, run_type="source/exploration", run_id="../../private")
    assert runtime_operations_evidence(unsafe) is None


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
    adapter_failure = cast(dict[str, object], payload["adapterFailure"])
    trace = cast(dict[str, object], payload["trace"])
    assert adapter_failure["kind"] == "timeout"
    assert adapter_failure["retryable"] is True
    assert trace["correlation_id"] == "corr-1"
    assert "run_id" not in trace


def test_runtime_error_payload_preserves_safe_model_stop_evidence() -> None:
    failure = AdapterFailure(
        adapter_profile="anthropic",
        operation="complete",
        kind="validation",
        is_retryable=False,
        operator_message="structured response ended before completion",
        details={
            "reason": "structured_output_incomplete",
            "stopReason": "max_tokens",
            "outputTokens": 800,
            "providerRequestId": "msg_safe_identifier",
        },
    )

    payload = runtime_error_payload(AdapterError(failure))

    adapter_failure = cast(dict[str, object], payload["adapterFailure"])
    details = cast(dict[str, object], adapter_failure["details"])
    assert details == {
        "reason": "structured_output_incomplete",
        "stopReason": "max_tokens",
        "outputTokens": 800,
        "providerRequestId": "msg_safe_identifier",
    }


def test_runtime_error_payload_preserves_only_structurally_safe_prompt_pins() -> None:
    payload = runtime_error_payload(
        ValidationFailed(
            "semantic trial failed",
            details={
                "promptVersionId": "contracts@7",
                "promptMode": "layout_aware_vision",
                "promptHash": f"sha256:{'a' * 64}",
                "nested": {"promptHash": "sk-ant-raw-secret"},
            },
        )
    )

    assert payload["details"] == {
        "promptVersionId": "contracts@7",
        "promptMode": "layout_aware_vision",
        "promptHash": f"sha256:{'a' * 64}",
        "nested": {"promptHash": "***MASKED***"},
    }


def test_runtime_error_payload_scrubs_secrets_from_messages_and_details() -> None:
    payload = runtime_error_payload(
        RuntimeError("Authorization: Bearer raw-token prompt: reveal customer data"),
        RequestContext(tenant_id="tenant-a", actor_user_id="user-a", request_id="req-a"),
    )
    domain_payload = runtime_error_payload(
        ValidationFailed(
            "invalid prompt: raw customer text",
            details={"apiKey": "plain-key", "safe": "visible", "nested": {"providerResponse": "raw"}},
        )
    )

    assert payload["message"] == "***MASKED***"
    assert "raw-token" not in str(payload)
    assert domain_payload["message"] == "***MASKED***"
    assert domain_payload["details"] == {
        "apiKey": "***MASKED***",
        "safe": "visible",
        "nested": {"providerResponse": "***MASKED***"},
    }


def test_runtime_operations_redacts_denylisted_json_keys_recursively() -> None:
    payload = {
        "safe": "visible",
        "credentialName": "erp_db",
        "inputTokens": 128,
        "outputTokens": 64,
        "authorization_decision": "pending_human_review",
        "authorizationHeader": "Bearer raw-token",
        "clientSecret": "raw-client-secret",
        "connectionString": "postgres://user:pass@db/orders",
        "databaseUrl": "postgres://user:pass@db/orders",
        "dsn": "postgres://user:pass@db/orders",
        "headerValue": "X-Api-Key raw-key",
        "jwt": "raw.jwt.token",
        "privateKey": "raw-private-key",
        "webhookSignature": "sha256=raw-signature",
        "credentials": {"username": "ada", "password": "raw-password"},
        "providerRequest": {"body": "raw"},
        "nested": [{"apiKey": "plain-key"}, {"compiledPrompt": "raw prompt"}],
    }

    assert redact_sensitive(payload, set()) == {
        "safe": "visible",
        "credentialName": "erp_db",
        "inputTokens": 128,
        "outputTokens": 64,
        "authorization_decision": "pending_human_review",
        "authorizationHeader": "***MASKED***",
        "clientSecret": "***REDACTED***",
        "connectionString": "***REDACTED***",
        "databaseUrl": "***REDACTED***",
        "dsn": "***REDACTED***",
        "headerValue": "***REDACTED***",
        "jwt": "***REDACTED***",
        "privateKey": "***REDACTED***",
        "webhookSignature": "***REDACTED***",
        "credentials": "***MASKED***",
        "providerRequest": "***MASKED***",
        "nested": [{"apiKey": "***REDACTED***"}, {"compiledPrompt": "***MASKED***"}],
    }


def test_runtime_error_payload_scrubs_connection_and_credential_fields() -> None:
    payload = runtime_error_payload(
        ValidationFailed(
            "connectionString=postgres://user:pass@db/orders",
            details={
                "credentialName": "erp_db",
                "clientSecret": "raw-client-secret",
                "privateKey": "raw-private-key",
                "databaseUrl": "postgres://user:pass@db/orders",
                "credentials": {"username": "ada", "password": "raw-password"},
            },
        )
    )

    assert payload["message"] == "***MASKED***"
    assert payload["details"] == {
        "credentialName": "erp_db",
        "clientSecret": "***MASKED***",
        "privateKey": "***MASKED***",
        "databaseUrl": "***MASKED***",
        "credentials": "***MASKED***",
    }
    assert "raw-client-secret" not in str(payload)
    assert "raw-private-key" not in str(payload)
    assert "raw-password" not in str(payload)
    assert "postgres://user:pass@db/orders" not in str(payload)


def test_runtime_error_payload_preserves_safe_request_ids_and_existing_redaction_markers() -> None:
    payload = runtime_error_payload(
        ValidationFailed(
            "OSDK OAuth client authentication is invalid",
            details={
                "requestId": "ontology-mcp-reduced-token",
                "secretValue": "***REDACTED***",
                "password": "***MASKED***",
            },
        )
    )

    assert payload["message"] == "OSDK OAuth client authentication is invalid"
    assert payload["details"] == {
        "requestId": "ontology-mcp-reduced-token",
        "secretValue": "***REDACTED***",
        "password": "***MASKED***",
    }


def test_runtime_error_payload_scrubs_url_userinfo_without_secret_key_names() -> None:
    payload = runtime_error_payload(
        RuntimeError(
            "connection failed for postgresql+psycopg://alice:p%40ss@db.internal/orders "
            "and redis://:cache-pass@cache.internal/0"
        )
    )
    unsafe_identifier = runtime_error_payload(
        ValidationFailed(
            "provider failed",
            details={"requestId": "https://operator:private@example.test/request"},
        )
    )
    malformed_raw_at = runtime_error_payload(
        RuntimeError("connection failed for postgresql://alice:p@ss@db.internal/orders")
    )

    assert payload["message"] == (
        "connection failed for postgresql+psycopg://***MASKED***@db.internal/orders "
        "and redis://***MASKED***@cache.internal/0"
    )
    assert unsafe_identifier["details"] == {"requestId": "https://***MASKED***@example.test/request"}
    assert malformed_raw_at["message"] == ("connection failed for postgresql://***MASKED***@db.internal/orders")
    assert "alice" not in str(payload)
    assert "cache-pass" not in str(payload)
    assert "operator" not in str(unsafe_identifier)
    assert "alice" not in str(malformed_raw_at)
    assert "p@ss" not in str(malformed_raw_at)


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
        dead_letter_retry_plan(cast(RuntimeRepository, repository), object(), RequestContext(), "dlq-1")


def test_dead_letter_retry_plan_rejects_malformed_source_event_id() -> None:
    repository = _DeadLetterPlanRepository({"event_type": "materialization.requested", "payload": {}}, [])

    with pytest.raises(ValidationFailed, match="dead-letter event is not retryable") as exc_info:
        dead_letter_retry_plan(cast(RuntimeRepository, repository), object(), RequestContext(), "dlq-1")

    assert exc_info.value.details == {"event_id": "dlq-1", "field": "source_event_id"}


def test_dead_letter_retry_plan_requires_source_outbox_event() -> None:
    repository = _DeadLetterPlanRepository(
        {"source_event_id": "outbox-1", "event_type": "materialization.requested", "payload": {}},
        [],
    )

    with pytest.raises(NotFound, match="source outbox event not found"):
        dead_letter_retry_plan(cast(RuntimeRepository, repository), object(), RequestContext(), "dlq-1")


def test_dead_letter_retry_plan_rejects_non_mapping_payload() -> None:
    repository = _DeadLetterPlanRepository(
        {"source_event_id": "outbox-1", "event_type": "materialization.requested", "payload": "raw"},
        [{"id": "outbox-1"}],
    )

    with pytest.raises(ValidationFailed, match="dead-letter event is not retryable") as exc_info:
        dead_letter_retry_plan(cast(RuntimeRepository, repository), object(), RequestContext(), "dlq-1")

    assert exc_info.value.details == {"event_id": "dlq-1", "field": "payload"}
