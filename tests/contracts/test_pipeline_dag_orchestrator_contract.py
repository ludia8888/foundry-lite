from __future__ import annotations

from threading import Event

from foundry_lite.application.ports.pipeline_dag_orchestrator import (
    PipelineDagDispatchRequest,
    PipelineDagDispatchResult,
    PipelineDagOrchestrator,
    UnavailablePipelineDagOrchestrator,
)
from foundry_lite.infrastructure.adapters.pipeline_dag_orchestrator import (
    LocalPipelineDagOrchestrator,
)


def test_local_pipeline_dag_orchestrator_contract_is_non_blocking_and_idempotent() -> None:
    orchestrator = LocalPipelineDagOrchestrator()
    completed = Event()
    dispatched: list[str] = []

    def drive(request: PipelineDagDispatchRequest) -> None:
        dispatched.append(request.run_id)
        completed.set()

    orchestrator.register_driver(drive)
    request = _request()

    first = _dispatch(orchestrator, request)
    replay = _dispatch(orchestrator, request)

    assert completed.wait(2)
    assert first.status == "dispatched"
    assert replay.status == "already_dispatched"
    assert replay.workflow_run_id == first.workflow_run_id
    assert dispatched == [request.run_id]
    assert orchestrator.cancel("another-tenant", first.workflow_run_id) is False
    assert orchestrator.cancel(request.tenant_id, first.workflow_run_id, reason="operator") is True


def test_unavailable_pipeline_dag_orchestrator_contract_fails_closed() -> None:
    orchestrator = UnavailablePipelineDagOrchestrator()

    result = _dispatch(orchestrator, _request())

    assert result.status == "unknown"
    assert result.task_queue == "unavailable"
    assert orchestrator.cancel("tenant-a", result.workflow_run_id) is False


def _dispatch(
    orchestrator: PipelineDagOrchestrator,
    request: PipelineDagDispatchRequest,
) -> PipelineDagDispatchResult:
    return orchestrator.dispatch(request)


def _request() -> PipelineDagDispatchRequest:
    return PipelineDagDispatchRequest(
        tenant_id="tenant-a",
        run_id="run-a",
        pipeline_id="pipeline-a",
        version_id="version-a",
        request_id="request-a",
        idempotency_key="run-a",
        execution_plan={"nodes": [], "edges": []},
        target_node_ids=(),
    )
