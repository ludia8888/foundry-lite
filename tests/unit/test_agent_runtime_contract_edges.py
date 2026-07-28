from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from foundry_lite.application.services.aip.agent_runtime_contracts import (
    AgentRuntimeError,
    AgentRuntimeRequest,
    _response_schema,
    error_payload,
    guard_context_budget,
    validate_request,
)
from foundry_lite.application.services.aip.agent_runtime_ports import (
    ContextCompilationError,
    ContextRetrievalError,
    RetrievedContextItem,
)
from foundry_lite.domain.context import RequestContext


def _request() -> AgentRuntimeRequest:
    return AgentRuntimeRequest(
        agent_run_id="agent-run-1",
        agent_version_id="agent-version-1",
        model_alias="fast",
        prompt_version_id="prompt-1",
        user_message="Explain the order",
        agent_instruction="Answer with evidence",
        security_partition="tenant-a:internal",
        allowed_security_partitions=("tenant-a:internal",),
        state_json={},
    )


def _context_item(context_id: str, token_estimate: int) -> RetrievedContextItem:
    return RetrievedContextItem(
        context_id=context_id,
        kind="object",
        text="Order",
        source_ref="Order:O-1",
        source_version="7",
        content_hash="sha256:order",
        relevance_score=1.0,
        retrieval_method="object_query",
        security_partition="tenant-a:internal",
        token_estimate=token_estimate,
    )


@pytest.mark.parametrize(
    "candidate",
    [
        replace(_request(), agent_run_id=""),
        replace(_request(), max_model_calls=2, max_loop_iterations=1),
        replace(_request(), max_tool_calls=2),
        replace(_request(), max_tool_calls=1),
    ],
)
def test_agent_runtime_request_rejects_missing_coordinates_and_unsupported_budgets(
    candidate: AgentRuntimeRequest,
) -> None:
    with pytest.raises(AgentRuntimeError):
        validate_request(RequestContext(tenant_id="tenant-a"), candidate)


def test_agent_runtime_context_budget_rejects_item_and_token_overflow() -> None:
    request = replace(_request(), max_context_items=1, max_context_tokens=10)
    items = (_context_item("ctx-1", 6), _context_item("ctx-2", 6))

    with pytest.raises(AgentRuntimeError) as item_error:
        guard_context_budget(request, items)
    assert item_error.value.args[0] == "context_item_budget_exceeded"

    with pytest.raises(AgentRuntimeError) as token_error:
        guard_context_budget(replace(request, max_context_items=2), items)
    assert token_error.value.args[0] == "context_token_budget_exceeded"


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (ContextCompilationError("compile_failed", "unsafe prompt"), "compile_failed"),
        (ContextRetrievalError("retrieval_failed", "stale source"), "retrieval_failed"),
    ],
)
def test_agent_runtime_error_payload_preserves_typed_context_failure(
    error: Exception,
    reason: str,
) -> None:
    assert error_payload(error)["reason"] == reason


def test_agent_runtime_error_payload_normalizes_adapter_failure_details() -> None:
    adapter_error_type = type("AdapterError", (Exception,), {})
    mapped = adapter_error_type("adapter failed")
    mapped.failure = SimpleNamespace(  # type: ignore[attr-defined]
        details={"reason": "credential_expired"},
        kind="unauthorized",
        operator_message="Refresh the connector credential.",
    )
    unmapped = adapter_error_type("adapter failed")
    unmapped.failure = SimpleNamespace(  # type: ignore[attr-defined]
        details=[],
        kind="unavailable",
        operator_message="Try again later.",
    )

    assert error_payload(mapped) == {
        "reason": "credential_expired",
        "detail": "Refresh the connector credential.",
    }
    assert error_payload(unmapped) == {
        "reason": "AdapterError",
        "detail": "Try again later.",
    }


def test_agent_runtime_response_schema_is_optional_and_canonical() -> None:
    assert _response_schema(None) is None
    assert _response_schema({}) is None
    assert _response_schema({"type": "object", "required": ["answer"]}) == ('{"required":["answer"],"type":"object"}')
