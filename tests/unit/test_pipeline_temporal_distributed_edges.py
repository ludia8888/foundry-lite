from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from foundry_lite.application.ports.pipeline_dag_orchestrator import PipelineDagDispatchRequest
from foundry_lite.infrastructure.adapters import pipeline_dag_orchestrator as orchestrators
from foundry_lite.infrastructure.adapters import temporal_workflows as workflows
from foundry_lite.infrastructure.adapters.pipeline_dag_orchestrator import (
    LocalPipelineDagOrchestrator,
    TemporalPipelineDagConfig,
    TemporalPipelineDagOrchestrator,
    pipeline_dag_workflow_id,
    pipeline_dag_workflow_id_matches_tenant,
)
from temporalio.exceptions import WorkflowAlreadyStartedError


class _TemporalHandle:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.is_cancelled = False

    async def cancel(self) -> None:
        if self.should_fail:
            raise ConnectionError("cancel unavailable")
        self.is_cancelled = True


class _TemporalClient:
    def __init__(self, mode: str = "success") -> None:
        self.mode = mode
        self.starts: list[dict[str, object]] = []
        self.handle = _TemporalHandle(should_fail=mode == "cancel-failure")

    async def start_workflow(self, name: str, payload: object, **kwargs: object) -> None:
        self.starts.append({"name": name, "payload": payload, **kwargs})
        if self.mode == "duplicate":
            raise WorkflowAlreadyStartedError(str(kwargs["id"]), name)
        if self.mode == "failure":
            raise ConnectionError("Temporal unavailable")

    def get_workflow_handle(self, _workflow_run_id: str) -> _TemporalHandle:
        return self.handle


def test_temporal_pipeline_orchestrator_dispatches_deduplicates_and_fails_closed() -> None:
    request = _request()
    success_client = _TemporalClient()
    success = TemporalPipelineDagOrchestrator(
        TemporalPipelineDagConfig(task_queue="pipeline-q", execution_timeout_seconds=17),
        client=success_client,
    ).dispatch(request)
    duplicate = TemporalPipelineDagOrchestrator(client=_TemporalClient("duplicate")).dispatch(request)
    unknown = TemporalPipelineDagOrchestrator(client=_TemporalClient("failure")).dispatch(request)

    assert success.status == "dispatched"
    assert success.task_queue == "pipeline-q"
    assert success_client.starts[0]["id"] == pipeline_dag_workflow_id("tenant-a", "run-a")
    assert duplicate.status == "already_dispatched"
    assert unknown.status == "unknown"
    assert pipeline_dag_workflow_id_matches_tenant("tenant-a", success.workflow_run_id)
    assert not pipeline_dag_workflow_id_matches_tenant("tenant-b", success.workflow_run_id)


def test_temporal_pipeline_orchestrator_cancellation_is_tenant_scoped_and_safe() -> None:
    workflow_id = pipeline_dag_workflow_id("tenant-a", "run-a")
    client = _TemporalClient()
    orchestrator = TemporalPipelineDagOrchestrator(client=client)

    assert orchestrator.cancel("tenant-b", workflow_id) is False
    assert orchestrator.cancel("tenant-a", workflow_id, reason="operator") is True
    assert client.handle.is_cancelled is True
    assert (
        TemporalPipelineDagOrchestrator(client=_TemporalClient("cancel-failure")).cancel("tenant-a", workflow_id)
        is False
    )


def test_local_pipeline_orchestrator_handles_unknown_cancel_and_driver_failure() -> None:
    request = _request()
    orchestrator = LocalPipelineDagOrchestrator()
    unknown_id = pipeline_dag_workflow_id("tenant-a", "unknown")
    assert orchestrator.cancel("tenant-a", unknown_id) is False

    def fail_driver(_request: PipelineDagDispatchRequest) -> None:
        raise RuntimeError("driver failed")

    orchestrator.register_driver(fail_driver)
    result = orchestrator.dispatch(request)
    orchestrator._start_worker()
    orchestrator._queue.join()

    assert isinstance(orchestrator.failure_for(result.workflow_run_id), RuntimeError)
    assert orchestrator.dispatch(request).status == "already_dispatched"

    no_driver = LocalPipelineDagOrchestrator()
    no_driver.dispatch(
        PipelineDagDispatchRequest(
            tenant_id="tenant-a",
            run_id="run-no-driver",
            pipeline_id="pipeline-a",
            version_id="version-a",
            request_id="request-a",
            idempotency_key="run-no-driver",
            execution_plan={},
            target_node_ids=(),
        )
    )
    no_driver._queue.join()


def test_temporal_pipeline_orchestrator_connects_from_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = object()
    calls: list[tuple[str, str]] = []

    class _ClientFactory:
        @staticmethod
        async def connect(address: str, *, namespace: str) -> object:
            calls.append((address, namespace))
            return client

    monkeypatch.setattr(
        orchestrators,
        "import_module",
        lambda _name: SimpleNamespace(Client=_ClientFactory),
    )
    orchestrator = TemporalPipelineDagOrchestrator(
        TemporalPipelineDagConfig(address="temporal:7233", namespace="foundry")
    )

    assert asyncio.run(orchestrator._client_handle()) is client
    assert calls == [("temporal:7233", "foundry")]


def test_temporal_activity_bindings_enrich_driver_and_route_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def driver(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        return {"operation": payload["operation"]}

    def preview_driver(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        return {"preview": payload["operation"]}

    monkeypatch.setattr(
        workflows.activity,
        "info",
        lambda: SimpleNamespace(attempt=2, activity_id="activity-a", workflow_id="workflow-a"),
    )
    monkeypatch.setattr(workflows.activity, "heartbeat", lambda *_args, **_kwargs: None)
    activities = workflows.PipelineDagActivities(
        driver,
        worker_id="worker-a",
        preview_driver=preview_driver,
    )

    async def scenario() -> None:
        assert await activities.begin({"run_id": "run-a"}) == {"operation": "begin"}
        assert await activities.execute_node({"run_id": "run-a"}) == {"operation": "execute_node"}
        assert await activities.finalize({"run_id": "run-a"}) == {"operation": "finalize"}
        assert await activities.execute_preview({"run_id": "run-a"}) == {"preview": "execute_preview"}

    asyncio.run(scenario())
    assert calls[0]["temporalAttempt"] == 2
    assert calls[0]["externalExecutionId"] == "activity-a"
    assert calls[0]["workflowRunId"] == "workflow-a"
    assert calls[0]["workerIdentity"] == "worker-a"


def test_temporal_activity_cancellation_calls_worker_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    operations: list[str] = []

    def driver(payload: dict[str, Any]) -> dict[str, Any]:
        operation = str(payload["operation"])
        operations.append(operation)
        if operation == "execute_node":
            raise asyncio.CancelledError
        return {"operation": operation}

    monkeypatch.setattr(
        workflows.activity,
        "info",
        lambda: SimpleNamespace(attempt=1, activity_id="activity-a", workflow_id="workflow-a"),
    )
    monkeypatch.setattr(workflows.activity, "heartbeat", lambda *_args, **_kwargs: None)
    activities = workflows.PipelineDagActivities(driver)

    async def scenario() -> None:
        with pytest.raises(asyncio.CancelledError):
            await activities.execute_node({"run_id": "run-a"})

    asyncio.run(scenario())
    assert operations == ["execute_node", "cancel_node"]


def test_temporal_preview_workflow_and_cancellation_preserve_no_commit_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activity_calls: list[tuple[str, dict[str, Any]]] = []

    async def execute_activity(
        name: str,
        payload: dict[str, Any],
        **_kwargs: object,
    ) -> dict[str, Any]:
        activity_calls.append((name, payload))
        return {"status": "succeeded", "isCommitForbidden": payload["is_commit_forbidden"]}

    monkeypatch.setattr(workflows.workflow, "execute_activity", execute_activity)
    result = asyncio.run(workflows.PipelineDagWorkflow().run({"run_id": "preview-a", "is_commit_forbidden": True}))
    assert result == {"status": "succeeded", "isCommitForbidden": True}
    assert activity_calls[0][0] == workflows.PIPELINE_DAG_PREVIEW_ACTIVITY_NAME

    operations: list[str] = []

    def driver(payload: dict[str, Any]) -> dict[str, Any]:
        return {"operation": payload["operation"]}

    def preview_driver(payload: dict[str, Any]) -> dict[str, Any]:
        operation = str(payload["operation"])
        operations.append(operation)
        if operation == "execute_preview":
            raise asyncio.CancelledError
        return {"operation": operation}

    monkeypatch.setattr(
        workflows.activity,
        "info",
        lambda: SimpleNamespace(attempt=1, activity_id="activity-a", workflow_id="workflow-a"),
    )
    monkeypatch.setattr(workflows.activity, "heartbeat", lambda *_args, **_kwargs: None)
    activities = workflows.PipelineDagActivities(driver, preview_driver=preview_driver)

    async def scenario() -> None:
        with pytest.raises(asyncio.CancelledError):
            await activities.execute_preview({"run_id": "preview-a", "is_commit_forbidden": True})

    asyncio.run(scenario())
    assert operations == ["execute_preview", "cancel_preview"]


def test_default_product_activities_and_driver_bindings() -> None:
    async def scenario() -> None:
        assert await workflows.run_workflow_step({"value": 1}) == {"processed": True, "value": 1}
        assert await workflows.run_connector_sync_step({"value": 2}) == {"processed": True, "value": 2}
        connector = workflows.ConnectorSyncActivities(lambda payload: {"connector": payload["value"]})
        media = workflows.MediaProcessingActivities(lambda payload: {"media": payload["value"]})
        assert await connector.run_connector_sync_step({"value": 3}) == {"connector": 3}
        assert await media.run_media_processing_step({"value": 4}) == {"media": 4}

    asyncio.run(scenario())


def test_pipeline_node_subprocess_returns_result_and_surfaces_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def never_cancelled() -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(workflows.activity, "wait_for_cancelled", never_cancelled)
    success_script = (
        'import sys; sys.stdin.buffer.read(); print(\'FOUNDRY_LITE_PIPELINE_RESULT={"status":"succeeded"}\')'
    )
    failure_script = "import sys; sys.stdin.buffer.read(); sys.stderr.write('node exploded'); raise SystemExit(3)"

    async def scenario() -> None:
        success = workflows.PipelineDagActivities(
            lambda payload: payload,
            node_subprocess_argv=(sys.executable, "-c", success_script),
        )
        assert await success._run_node_subprocess({"node": "a"}) == {"status": "succeeded"}
        failure = workflows.PipelineDagActivities(
            lambda payload: payload,
            node_subprocess_argv=(sys.executable, "-c", failure_script),
        )
        with pytest.raises(RuntimeError, match="node exploded"):
            await failure._run_node_subprocess({"node": "a"})

    asyncio.run(scenario())


def test_subprocess_helpers_cover_validation_wait_and_forced_termination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workflows.activity, "heartbeat", lambda *_args, **_kwargs: None)
    assert workflows._subprocess_result(b'noise\nFOUNDRY_LITE_PIPELINE_RESULT={"ok": true}\n') == {"ok": True}
    assert workflows._subprocess_error(b"") == "pipeline node subprocess failed"
    assert workflows._subprocess_error(b"x" * 2100) == "x" * 2000
    with pytest.raises(RuntimeError, match="did not return"):
        workflows._subprocess_result(b"noise")
    with pytest.raises(RuntimeError, match="invalid result"):
        workflows._subprocess_result(b"FOUNDRY_LITE_PIPELINE_RESULT=[1]")

    async def scenario() -> None:
        communicate = asyncio.create_task(_completed_output())
        cancellation = asyncio.create_task(_never())
        assert await workflows._wait_for_subprocess(communicate, cancellation) == (b"out", b"")
        cancellation.cancel()
        await asyncio.gather(cancellation, return_exceptions=True)

        communicate = asyncio.create_task(_never_output())
        cancellation = asyncio.create_task(_completed_cancel())
        with pytest.raises(asyncio.CancelledError):
            await workflows._wait_for_subprocess(communicate, cancellation)
        communicate.cancel()
        await asyncio.gather(communicate, return_exceptions=True)

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, lambda *_: None); time.sleep(10)",
            start_new_session=True,
        )
        await workflows._terminate_process(process, 0.01)
        assert process.returncode is not None
        await workflows._terminate_process(process, 0.01)

    asyncio.run(scenario())


def test_temporal_dag_pure_helpers_cover_blocking_and_policy_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    nodes = {
        "node-a": {"nodeId": "node-a"},
        "node-b": {"nodeId": "node-b"},
    }
    edges = [{"edgeId": "edge-a", "sourceNodeId": "node-a", "targetNodeId": "node-b"}]
    outcomes = {"node-a": {"nodeId": "node-a", "status": "failed"}}

    assert workflows._ready_nodes(nodes, edges, {}) == [{"nodeId": "node-a"}]
    assert workflows._blocked_outcomes({"node-b": nodes["node-b"]}, edges, outcomes) == {
        "node-b": {"nodeId": "node-b", "status": "skipped", "blockedBy": ["node-a"]}
    }
    assert workflows._upstream_ids("node-b", edges) == ("node-a",)
    assert workflows._is_terminal_failure(outcomes["node-a"])
    assert not workflows._is_terminal_failure(None)
    assert workflows._plan_rows([{"nodeId": "node-a"}, {"bad": True}, "bad"]) == [{"nodeId": "node-a"}]
    assert workflows._plan_rows({}) == []
    assert workflows._node_execution_policy({}) == {
        "maximumAttempts": 3,
        "initialBackoffSeconds": 1,
        "maximumBackoffSeconds": 30,
        "timeoutSeconds": 300,
    }
    assert (
        workflows._node_execution_policy(
            {
                "executionPolicy": {
                    "maximumAttempts": 4,
                    "initialBackoffSeconds": 2,
                    "maximumBackoffSeconds": 8,
                    "timeoutSeconds": 10,
                }
            }
        )["maximumAttempts"]
        == 4
    )
    policy = workflows._control_retry_policy()
    assert policy.maximum_attempts == 3
    monkeypatch.setattr(workflows.workflow, "info", lambda: SimpleNamespace(task_queue="pipeline-q"))
    assert workflows._node_activity_task_queue({"runtimeCapability": "media"}) == "pipeline-q.capability.media"


async def _completed_output() -> tuple[bytes, bytes]:
    return b"out", b""


async def _never_output() -> tuple[bytes, bytes]:
    await asyncio.Event().wait()
    return b"", b""


async def _completed_cancel() -> None:
    return None


async def _never() -> None:
    await asyncio.Event().wait()


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
