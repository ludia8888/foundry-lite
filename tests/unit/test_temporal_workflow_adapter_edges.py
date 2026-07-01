from __future__ import annotations

import asyncio
from typing import Any

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.workflow_adapter import WorkflowStartRequest, workflow_run_id
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


class _StartClient:
    def __init__(self) -> None:
        self.started_ids: list[str] = []

    async def start_workflow(self, _workflow_name: str, _input: dict[str, object], **kwargs: object) -> object:
        workflow_id = kwargs["id"]
        assert isinstance(workflow_id, str)
        self.started_ids.append(workflow_id)
        return _CompletedHandle()


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


class _CancelledHandle:
    def __init__(self) -> None:
        self.cancelled = False

    async def cancel(self) -> None:
        self.cancelled = True

    async def describe(self) -> _Description:
        return _Description(WorkflowExecutionStatus.CANCELED)

    async def result(self) -> dict[str, object]:
        return {}


def test_sync_workflow_run_bridge_uses_async_core() -> None:
    adapter = TemporalWorkflowAdapter(client=_LookupClient(_CompletedHandle()))
    request = _workflow_request("tenant-demo", "EdgeWorkflow", "sync-lookup")

    run = adapter.workflow_run(request.tenant_id, workflow_run_id(request))

    assert run is not None
    assert run.status == "succeeded"
    assert run.output == {"ok": True}


def test_workflow_run_rejects_cross_tenant_lookup_before_temporal_call() -> None:
    class _ExplodingClient:
        def get_workflow_handle(self, _run_id: str) -> object:
            raise AssertionError("cross-tenant lookup should not reach Temporal")

    adapter = TemporalWorkflowAdapter(client=_ExplodingClient())
    request = _workflow_request("tenant-a", "EdgeWorkflow", "daily-sync")

    assert adapter.workflow_run("tenant-b", workflow_run_id(request)) is None


def test_start_workflow_id_namespaces_tenant_and_workflow() -> None:
    async def body() -> None:
        client = _StartClient()
        adapter = TemporalWorkflowAdapter(client=client)
        request_a = _workflow_request("tenant-a", "ConnectorSyncWorkflow", "daily-sync")
        request_b = _workflow_request("tenant-b", "ConnectorSyncWorkflow", "daily-sync")
        request_c = _workflow_request("tenant-a", "DifferentWorkflow", "daily-sync")

        run_a = await adapter.start_workflow_async(request_a)
        run_b = await adapter.start_workflow_async(request_b)
        run_c = await adapter.start_workflow_async(request_c)

        assert run_a.run_id == client.started_ids[0]
        assert len({run_a.run_id, run_b.run_id, run_c.run_id}) == 3
        assert all(run_id.startswith("flite:") for run_id in client.started_ids)

    asyncio.run(body())


def test_workflow_run_result_fetch_failure_raises_retryable_adapter_error() -> None:
    async def body() -> None:
        adapter = TemporalWorkflowAdapter(client=_LookupClient(_CompletedButResultUnavailableHandle()))
        request = _workflow_request("tenant-demo", "EdgeWorkflow", "result-down")

        with pytest.raises(AdapterError) as raised:
            await adapter.workflow_run_async(request.tenant_id, workflow_run_id(request))

        failure = raised.value.failure
        assert failure.operation == "workflow_run"
        assert failure.kind == "unavailable"
        assert failure.is_retryable is True
        assert failure.details["workflowId"] == workflow_run_id(request)

    asyncio.run(body())


def test_cancel_workflow_returns_operator_cancellation_evidence() -> None:
    async def body() -> None:
        handle = _CancelledHandle()
        adapter = TemporalWorkflowAdapter(client=_LookupClient(handle))
        request = _workflow_request("tenant-demo", "EdgeWorkflow", "cancel-me")

        assert await adapter.cancel_workflow_async("other-tenant", workflow_run_id(request), reason="ignored") is None
        run = await adapter.cancel_workflow_async(request.tenant_id, workflow_run_id(request), reason="operator asked")

        assert handle.cancelled is True
        assert run is not None
        assert run.status == "cancelled"
        assert run.output["cancellation"]["reason"] == "operator asked"
        assert run.output["cancellation"]["cleanup"]["datasetStaging"] == "adapter_confirmed"

    asyncio.run(body())


def test_terminal_lookup_failure_returns_durable_error_payload() -> None:
    async def body() -> None:
        adapter = TemporalWorkflowAdapter(client=_LookupClient(_FailedButResultUnavailableHandle()))
        request = _workflow_request("tenant-demo", "EdgeWorkflow", "terminal-down")

        run = await adapter.workflow_run_async(request.tenant_id, workflow_run_id(request))

        assert run is not None
        assert run.status == "failed"
        assert run.error is not None
        assert run.error["operation"] == "workflow_run"
        assert run.error["kind"] == "unavailable"
        assert run.error["retryable"] is True
        assert run.error["details"]["workflowId"] == workflow_run_id(request)
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


def _workflow_request(tenant_id: str, workflow_name: str, idempotency_key: str) -> WorkflowStartRequest:
    return WorkflowStartRequest(
        workflow_name=workflow_name,
        tenant_id=tenant_id,
        request_id=f"req-{tenant_id}",
        idempotency_key=idempotency_key,
        input={"tenant": tenant_id},
    )
