from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest
from foundry_lite_worker import pipeline_control, pipeline_dag, pipeline_node_subprocess


class _StopControlLoop(Exception):
    pass


class _FakeControl:
    def __init__(self) -> None:
        self.limits: list[int] = []

    def tick(self, *, limit: int) -> None:
        self.limits.append(limit)


class _FakePreview:
    def __init__(self) -> None:
        self.recovery_limits: list[int] = []
        self.executed: list[tuple[str, object]] = []
        self.cancelled: list[tuple[str, object]] = []

    def recover_preview_dispatches(self, *, limit: int) -> None:
        self.recovery_limits.append(limit)

    def execute_preview_run(self, run_id: str, *, ctx: object) -> dict[str, object]:
        self.executed.append((run_id, ctx))
        return {"runId": run_id, "status": "succeeded"}

    def cancel_preview_run(self, run_id: str, *, ctx: object) -> dict[str, object]:
        self.cancelled.append((run_id, ctx))
        return {"runId": run_id, "status": "cancelled"}


class _FakeWorker:
    instances: list[_FakeWorker] = []

    def __init__(self, _client: object, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.did_run = False
        self.instances.append(self)

    async def run(self) -> None:
        self.did_run = True


def test_pipeline_control_loop_ticks_both_recovery_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = _FakeControl()
    preview = _FakePreview()
    foundry = SimpleNamespace(
        _services=SimpleNamespace(
            pipelines=SimpleNamespace(control=control, preview=preview),
        )
    )
    monkeypatch.setattr(pipeline_control, "create_runtime_core_dependencies", lambda **_kwargs: object())
    monkeypatch.setattr(pipeline_control, "FoundryLite", lambda **_kwargs: foundry)
    monkeypatch.setenv("FOUNDRY_LITE_PIPELINE_CONTROL_INTERVAL_SECONDS", "0")

    def stop_after_tick(interval: float) -> None:
        assert interval == 1.0
        raise _StopControlLoop

    monkeypatch.setattr(pipeline_control.time, "sleep", stop_after_tick)
    with pytest.raises(_StopControlLoop):
        pipeline_control.run_control_loop()

    assert control.limits == [100]
    assert preview.recovery_limits == [100]

    called: list[bool] = []
    monkeypatch.setattr(pipeline_control, "run_control_loop", lambda: called.append(True))
    pipeline_control.main()
    assert called == [True]


def test_pipeline_dag_worker_registers_control_and_capability_queues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview = _FakePreview()
    foundry = SimpleNamespace(
        _services=SimpleNamespace(
            pipelines=SimpleNamespace(
                distributed_node=SimpleNamespace(drive=lambda payload: payload),
                preview=preview,
            )
        )
    )
    client = object()
    connection_calls: list[tuple[str, str]] = []

    async def connect(address: str, *, namespace: str) -> object:
        connection_calls.append((address, namespace))
        return client

    _FakeWorker.instances = []
    monkeypatch.setattr(pipeline_dag.Client, "connect", connect)
    monkeypatch.setattr(pipeline_dag, "Worker", _FakeWorker)
    monkeypatch.setattr(pipeline_dag, "create_runtime_core_dependencies", lambda **_kwargs: object())
    monkeypatch.setattr(pipeline_dag, "FoundryLite", lambda **_kwargs: foundry)
    monkeypatch.setattr(pipeline_dag, "foundry_sandbox_runner", lambda: "sandbox")
    monkeypatch.setenv("FOUNDRY_LITE_TEMPORAL_ADDRESS", "temporal:7233")
    monkeypatch.setenv("FOUNDRY_LITE_TEMPORAL_NAMESPACE", "foundry")
    monkeypatch.setenv("FOUNDRY_LITE_PIPELINE_DAG_TASK_QUEUE", "pipeline-q")
    monkeypatch.setenv(
        "FOUNDRY_LITE_PIPELINE_DAG_CAPABILITIES",
        "media_pipeline_runtime,tabular_v1_compiler",
    )
    monkeypatch.setenv("FOUNDRY_LITE_WORKER_ID", "worker-a")

    asyncio.run(pipeline_dag.run_worker())

    assert connection_calls == [("temporal:7233", "foundry")]
    assert [worker.kwargs["task_queue"] for worker in _FakeWorker.instances] == [
        "pipeline-q",
        "pipeline-q.capability.media_pipeline_runtime",
        "pipeline-q.capability.tabular_v1_compiler",
    ]
    assert all(worker.did_run for worker in _FakeWorker.instances)
    assert _FakeWorker.instances[0].kwargs["workflow_runner"] == "sandbox"


def test_pipeline_dag_preview_routes_and_capability_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview = _FakePreview()
    foundry = SimpleNamespace(
        _services=SimpleNamespace(pipelines=SimpleNamespace(preview=preview)),
    )
    payload = {
        "run_id": "preview-a",
        "tenant_id": "tenant-a",
        "request_id": "request-a",
    }

    executed = pipeline_dag._drive_preview(foundry, payload)
    cancelled = pipeline_dag._drive_preview(foundry, {**payload, "operation": "cancel_preview"})
    ctx = pipeline_dag._worker_context(payload)

    assert executed["status"] == "succeeded"
    assert cancelled["status"] == "cancelled"
    assert (ctx.tenant_id, ctx.actor_user_id, ctx.request_id) == (
        "tenant-a",
        "pipeline-preview-worker",
        "request-a",
    )
    monkeypatch.delenv("FOUNDRY_LITE_PIPELINE_DAG_CAPABILITIES", raising=False)
    assert set(pipeline_dag._worker_capabilities()) == set(pipeline_dag.DEFAULT_PIPELINE_RUNTIME_CAPABILITIES)
    monkeypatch.setenv("FOUNDRY_LITE_PIPELINE_DAG_CAPABILITIES", "unknown")
    with pytest.raises(ValueError, match="unknown Pipeline DAG worker capabilities"):
        pipeline_dag._worker_capabilities()


def test_pipeline_node_subprocess_main_and_preview_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driven: list[dict[str, object]] = []
    preview = _FakePreview()
    foundry = SimpleNamespace(
        _services=SimpleNamespace(
            pipelines=SimpleNamespace(
                distributed_node=SimpleNamespace(
                    drive=lambda payload: driven.append(payload) or {"status": "succeeded"}
                ),
                preview=preview,
            )
        )
    )
    payload = {
        "run_id": "run-a",
        "tenant_id": "tenant-a",
        "request_id": "request-a",
        "operation": "execute_node",
    }

    assert pipeline_node_subprocess._drive(foundry, payload) == {"status": "succeeded"}
    assert pipeline_node_subprocess._drive(
        foundry,
        {**payload, "run_id": "preview-a", "is_commit_forbidden": True},
    ) == {"runId": "preview-a", "status": "succeeded"}
    assert driven == [payload]

    output = io.StringIO()
    monkeypatch.setattr(pipeline_node_subprocess, "create_runtime_core_dependencies", lambda **_kwargs: object())
    monkeypatch.setattr(pipeline_node_subprocess, "FoundryLite", lambda **_kwargs: foundry)
    monkeypatch.setattr(pipeline_node_subprocess.sys, "stdin", io.StringIO('{"operation":"begin"}'))
    monkeypatch.setattr(pipeline_node_subprocess.sys, "stdout", output)
    pipeline_node_subprocess.main()
    assert output.getvalue().startswith("FOUNDRY_LITE_PIPELINE_RESULT=")


def test_pipeline_node_subprocess_rejects_non_object_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline_node_subprocess.sys, "stdin", io.StringIO("[]"))
    with pytest.raises(TypeError, match="payload must be an object"):
        pipeline_node_subprocess.main()


def test_pipeline_dag_main_runs_async_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_worker() -> None:
        calls.append("run")

    monkeypatch.setattr(pipeline_dag, "run_worker", fake_worker)
    pipeline_dag.main()
    assert calls == ["run"]
