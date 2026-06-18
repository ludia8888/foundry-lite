from __future__ import annotations

import asyncio
from typing import Any

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.infrastructure.adapters import temporal_workflow as temporal_mod
from foundry_lite.infrastructure.adapters.temporal_workflow import TemporalWorkflowAdapter
from temporalio.client import WorkflowExecutionStatus


class _Description:
    def __init__(self, status: Any, *, workflow_type: str = "EdgeWorkflow", run_id: str = "temporal-run-1") -> None:
        self.status = status
        self.workflow_type = workflow_type
        self.run_id = run_id


class _LookupClient:
    def __init__(self, handle: object) -> None:
        self._handle = handle

    def get_workflow_handle(self, _run_id: str) -> object:
        return self._handle


class _CompletedHandle:
    async def describe(self) -> _Description:
        return _Description(WorkflowExecutionStatus.COMPLETED)

    async def result(self) -> dict[str, object]:
        return {"ok": True}


class _CompletedButResultUnavailableHandle:
    async def describe(self) -> _Description:
        return _Description(WorkflowExecutionStatus.COMPLETED)

    async def result(self) -> dict[str, object]:
        raise ConnectionError("result fetch unavailable")


class _FailedButResultUnavailableHandle:
    async def describe(self) -> _Description:
        return _Description(WorkflowExecutionStatus.FAILED)

    async def result(self) -> dict[str, object]:
        raise ConnectionError("terminal failure lookup unavailable")


def test_sync_workflow_run_bridge_uses_async_core() -> None:
    adapter = TemporalWorkflowAdapter(client=_LookupClient(_CompletedHandle()))

    run = adapter.workflow_run("wf-sync-lookup")

    assert run is not None
    assert run.status == "succeeded"
    assert run.output == {"ok": True}


def test_workflow_run_result_fetch_failure_raises_retryable_adapter_error() -> None:
    async def body() -> None:
        adapter = TemporalWorkflowAdapter(client=_LookupClient(_CompletedButResultUnavailableHandle()))

        with pytest.raises(AdapterError) as raised:
            await adapter.workflow_run_async("wf-result-down")

        failure = raised.value.failure
        assert failure.operation == "workflow_run"
        assert failure.kind == "unavailable"
        assert failure.is_retryable is True
        assert failure.details["workflowId"] == "wf-result-down"

    asyncio.run(body())


def test_terminal_lookup_failure_returns_durable_error_payload() -> None:
    async def body() -> None:
        adapter = TemporalWorkflowAdapter(client=_LookupClient(_FailedButResultUnavailableHandle()))

        run = await adapter.workflow_run_async("wf-terminal-down")

        assert run is not None
        assert run.status == "failed"
        assert run.error is not None
        assert run.error["operation"] == "workflow_run"
        assert run.error["kind"] == "unavailable"
        assert run.error["retryable"] is True
        assert run.error["details"]["workflowId"] == "wf-terminal-down"
        assert run.error["details"]["workflowName"] == "EdgeWorkflow"

    asyncio.run(body())


def test_temporal_failure_helpers_preserve_timeout_details() -> None:
    failure = temporal_mod._temporal_adapter_failure(
        "temporal",
        "start_workflow",
        TimeoutError("deadline exceeded"),
        idempotency_key="idem-1",
        workflow_id="wf-timeout",
        workflow_name="TimeoutWorkflow",
        timeout_seconds=7,
    )

    payload = failure.to_payload()
    assert payload["kind"] == "timeout"
    assert payload["retryable"] is True
    assert payload["timeoutSeconds"] == 7
    assert payload["idempotencyKey"] == "idem-1"
    assert payload["details"]["workflowId"] == "wf-timeout"
    assert payload["details"]["workflowName"] == "TimeoutWorkflow"


def test_temporal_failure_helpers_preserve_unknown_root_message() -> None:
    inner = RuntimeError("deep temporal cause")
    outer = RuntimeError("outer temporal wrapper")
    outer.cause = inner  # type: ignore[attr-defined]

    failure = temporal_mod._temporal_adapter_failure("temporal", "workflow_run", outer)

    payload = failure.to_payload()
    assert payload["kind"] == "unknown"
    assert payload["retryable"] is False
    assert "deep temporal cause" in payload["operatorMessage"]
