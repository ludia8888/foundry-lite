"""Contract for ``ToolExecutor`` (AIP-lite P0e ToolBroker executor port, §8.8)."""

from __future__ import annotations

from foundry_lite.application.ports.tool_executor import ToolExecutionRequest, ToolExecutor
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure.adapters.fake_tool_executor import FakeToolExecutor


def test_fake_tool_executor_is_runtime_checkable() -> None:
    executor = FakeToolExecutor()

    assert isinstance(executor, ToolExecutor)


def test_fake_tool_executor_runs_as_invoking_user_context() -> None:
    executor = FakeToolExecutor()
    ctx = RequestContext(tenant_id="tenant-demo", actor_user_id="user-1")

    result = executor.execute(
        ctx,
        ToolExecutionRequest(
            tool_id="ontology.get_object",
            version="2026-06-25",
            input_json={"object_type": "PurchaseOrder", "object_id": "PO-1042"},
            ai_run_id="ai-run-1",
            request_id="req-1",
            idempotency_key="idem-1",
        ),
    )

    assert result.status == "succeeded"
    assert result.output_json["tenant_id"] == "tenant-demo"
    assert result.output_json["actor_user_id"] == "user-1"
    assert result.output_json["tool_id"] == "ontology.get_object"
    assert result.result_ref == "fake-tool://ontology.get_object"


def test_tool_executor_failure_contract_declares_validation_mode() -> None:
    contract = FakeToolExecutor().failure_contract()

    assert contract.adapter_profile == "fake-tool-executor"
    assert {mode.operation for mode in contract.modes} == {"execute"}
