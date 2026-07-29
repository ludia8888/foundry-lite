from __future__ import annotations

import asyncio
from typing import Any

from foundry_lite.infrastructure.adapters.temporal_workflows import (
    PIPELINE_DAG_BEGIN_ACTIVITY_NAME,
    PIPELINE_DAG_FINALIZE_ACTIVITY_NAME,
    PIPELINE_DAG_NODE_ACTIVITY_NAME,
    PIPELINE_DAG_TASK_QUEUE,
    PipelineDagWorkflow,
    foundry_sandbox_runner,
    pipeline_capability_task_queue,
)
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

_ATTEMPTS: dict[str, int] = {}
_STARTED: list[str] = []
_BRANCH_GATE: asyncio.Event | None = None


def test_runtime_capability_has_a_deterministic_isolated_task_queue() -> None:
    assert pipeline_capability_task_queue(PIPELINE_DAG_TASK_QUEUE, "") == PIPELINE_DAG_TASK_QUEUE
    assert (
        pipeline_capability_task_queue(
            PIPELINE_DAG_TASK_QUEUE,
            "media_pipeline_runtime",
        )
        == "foundry-lite-pipeline-dag.capability.media_pipeline_runtime"
    )


@activity.defn(name=PIPELINE_DAG_BEGIN_ACTIVITY_NAME)
async def _begin(payload: dict[str, Any]) -> dict[str, Any]:
    return {"runId": payload["run_id"], "status": "running"}


@activity.defn(name=PIPELINE_DAG_NODE_ACTIVITY_NAME)
async def _execute_node(payload: dict[str, Any]) -> dict[str, Any]:
    node = dict(payload["node"])
    node_id = str(node["nodeId"])
    _ATTEMPTS[node_id] = _ATTEMPTS.get(node_id, 0) + 1
    _STARTED.append(node_id)
    if payload.get("mode") == "retry" and node_id == "node-a" and _ATTEMPTS[node_id] < 3:
        raise RuntimeError("adapter_transient")
    if payload.get("mode") == "permanent" and node_id == "node-a":
        return {"nodeId": node_id, "status": "failed", "error": {"kind": "validation"}}
    if node_id in {"node-a", "node-b"} and _BRANCH_GATE is not None:
        if {"node-a", "node-b"}.issubset(_STARTED):
            _BRANCH_GATE.set()
        await _BRANCH_GATE.wait()
    return {"nodeId": node_id, "status": "succeeded", "outputs": []}


@activity.defn(name=PIPELINE_DAG_FINALIZE_ACTIVITY_NAME)
async def _finalize(payload: dict[str, Any]) -> dict[str, Any]:
    return {"status": "done", "nodeOutcomes": payload["nodeOutcomes"]}


def test_fork_join_runs_independent_branches_before_downstream() -> None:
    async def scenario() -> None:
        global _BRANCH_GATE
        _reset()
        _BRANCH_GATE = asyncio.Event()
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with _worker(env):
                result = await _run_workflow(env, _payload("fork"))

        statuses = {row["nodeId"]: row["status"] for row in result["nodeOutcomes"]}
        assert statuses == {"node-a": "succeeded", "node-b": "succeeded", "node-c": "succeeded"}
        assert _STARTED.index("node-c") > _STARTED.index("node-a")
        assert _STARTED.index("node-c") > _STARTED.index("node-b")

    asyncio.run(scenario())


def test_retryable_activity_fails_twice_then_succeeds() -> None:
    async def scenario() -> None:
        _reset()
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with _worker(env):
                result = await _run_workflow(env, _payload("retry", single=True))

        assert result["status"] == "done"
        assert _ATTEMPTS["node-a"] == 3

    asyncio.run(scenario())


def test_permanent_failure_is_not_retried_and_blocks_downstream() -> None:
    async def scenario() -> None:
        _reset()
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with _worker(env):
                result = await _run_workflow(env, _payload("permanent", single=True))

        outcomes = {row["nodeId"]: row for row in result["nodeOutcomes"]}
        assert _ATTEMPTS["node-a"] == 1
        assert outcomes["node-a"]["status"] == "failed"
        assert outcomes["node-c"]["status"] == "skipped"
        assert "node-c" not in _STARTED

    asyncio.run(scenario())


def test_completed_history_replays_without_executing_node_again() -> None:
    async def scenario() -> None:
        _reset()
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with _worker(env):
                handle = await env.client.start_workflow(
                    PipelineDagWorkflow.run,
                    _payload("replay", single=True),
                    id="pipeline-replay",
                    task_queue=PIPELINE_DAG_TASK_QUEUE,
                )
                await handle.result()
                history = await handle.fetch_history()
        attempts_before_replay = dict(_ATTEMPTS)
        await Replayer(
            workflows=[PipelineDagWorkflow],
            workflow_runner=foundry_sandbox_runner(),
        ).replay_workflow(history)
        assert _ATTEMPTS == attempts_before_replay

    asyncio.run(scenario())


def test_nodes_are_dispatched_to_the_runtime_capability_task_queue() -> None:
    async def scenario() -> None:
        _reset()
        payload = _payload("capability", single=True)
        for node in payload["execution_plan"]["nodes"]:
            node["runtimeCapability"] = "media_pipeline_runtime"
        capability_queue = pipeline_capability_task_queue(
            PIPELINE_DAG_TASK_QUEUE,
            "media_pipeline_runtime",
        )
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client,
                task_queue=PIPELINE_DAG_TASK_QUEUE,
                workflows=[PipelineDagWorkflow],
                activities=[_begin, _finalize],
                workflow_runner=foundry_sandbox_runner(),
            ):
                async with Worker(
                    env.client,
                    task_queue=capability_queue,
                    activities=[_execute_node],
                ):
                    result = await _run_workflow(env, payload)

        assert result["status"] == "done"
        assert _ATTEMPTS == {"node-a": 1, "node-c": 1}

    asyncio.run(scenario())


def _worker(env: WorkflowEnvironment) -> Worker:
    return Worker(
        env.client,
        task_queue=PIPELINE_DAG_TASK_QUEUE,
        workflows=[PipelineDagWorkflow],
        activities=[_begin, _execute_node, _finalize],
        workflow_runner=foundry_sandbox_runner(),
    )


async def _run_workflow(env: WorkflowEnvironment, payload: dict[str, Any]) -> dict[str, Any]:
    return await env.client.execute_workflow(
        PipelineDagWorkflow.run,
        payload,
        id=f"pipeline-{payload['mode']}",
        task_queue=PIPELINE_DAG_TASK_QUEUE,
    )


def _payload(mode: str, *, single: bool = False) -> dict[str, Any]:
    nodes = [
        _node("node-a"),
        *([] if single else [_node("node-b")]),
        _node("node-c"),
    ]
    sources = ["node-a"] if single else ["node-a", "node-b"]
    edges = [{"edgeId": f"{source}-node-c", "sourceNodeId": source, "targetNodeId": "node-c"} for source in sources]
    return {
        "tenant_id": "tenant-a",
        "request_id": f"request-{mode}",
        "run_id": f"run-{mode}",
        "mode": mode,
        "execution_plan": {"nodes": nodes, "edges": edges},
    }


def _node(node_id: str) -> dict[str, Any]:
    return {
        "nodeId": node_id,
        "executionPolicy": {
            "maximumAttempts": 3,
            "initialBackoffSeconds": 1,
            "maximumBackoffSeconds": 30,
            "timeoutSeconds": 30,
        },
    }


def _reset() -> None:
    global _BRANCH_GATE
    _ATTEMPTS.clear()
    _STARTED.clear()
    _BRANCH_GATE = None
