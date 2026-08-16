"""Temporal worker entrypoint for distributed Pipeline DAG node activities."""

from __future__ import annotations

import asyncio
import os
import socket
import sys

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.services.pipeline_runtime_capabilities import (
    DEFAULT_PIPELINE_RUNTIME_CAPABILITIES,
)
from foundry_lite.domain.context import DEMO_ADMIN_ROLES, RequestContext
from foundry_lite.infrastructure.adapters.temporal_workflows import (
    PIPELINE_DAG_TASK_QUEUE,
    PipelineDagActivities,
    PipelineDagWorkflow,
    foundry_sandbox_runner,
    pipeline_capability_task_queue,
)
from foundry_lite.infrastructure.local_runtime import create_runtime_core_dependencies
from temporalio.client import Client
from temporalio.worker import Worker


async def run_worker() -> None:
    client = await Client.connect(
        os.getenv("FOUNDRY_LITE_TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=os.getenv("FOUNDRY_LITE_TEMPORAL_NAMESPACE", "default"),
    )
    foundry = FoundryLite(
        dependencies=create_runtime_core_dependencies(
            db_url=os.getenv("FOUNDRY_LITE_DB_URL"),
            storage_root=os.getenv("FOUNDRY_LITE_STORAGE_ROOT"),
        )
    )
    try:
        worker_id = os.getenv("FOUNDRY_LITE_WORKER_ID", f"{socket.gethostname()}:{os.getpid()}")
        activities = PipelineDagActivities(
            foundry._services.pipelines.distributed_node.drive,
            worker_id=worker_id,
            preview_driver=lambda payload: _drive_preview(foundry, payload),
            node_subprocess_argv=(sys.executable, "-m", "foundry_lite_worker.pipeline_node_subprocess"),
        )
        task_queue = os.getenv("FOUNDRY_LITE_PIPELINE_DAG_TASK_QUEUE", PIPELINE_DAG_TASK_QUEUE)
        control_worker = Worker(
            client,
            task_queue=task_queue,
            workflows=[PipelineDagWorkflow],
            activities=[
                activities.begin,
                activities.execute_node,
                activities.finalize,
                activities.execute_preview,
            ],
            workflow_runner=foundry_sandbox_runner(),
        )
        capability_workers = [
            Worker(
                client,
                task_queue=pipeline_capability_task_queue(task_queue, capability),
                activities=[activities.execute_node],
            )
            for capability in _worker_capabilities()
        ]
        await asyncio.gather(control_worker.run(), *(worker.run() for worker in capability_workers))
    finally:
        foundry.close()


def main() -> None:
    asyncio.run(run_worker())


def _drive_preview(foundry: FoundryLite, payload: dict[str, object]) -> dict[str, object]:
    preview_run_id = str(payload["run_id"])
    ctx = _worker_context(payload)
    if payload.get("operation") == "cancel_preview":
        return foundry._services.pipelines.preview.cancel_preview_run(preview_run_id, ctx=ctx)
    return foundry._services.pipelines.preview.execute_preview_run(preview_run_id, ctx=ctx)


def _worker_context(payload: dict[str, object]) -> RequestContext:
    return RequestContext(
        tenant_id=str(payload["tenant_id"]),
        actor_user_id="pipeline-preview-worker",
        request_id=str(payload["request_id"]),
        roles=DEMO_ADMIN_ROLES,
    )


def _worker_capabilities() -> tuple[str, ...]:
    configured = os.getenv("FOUNDRY_LITE_PIPELINE_DAG_CAPABILITIES")
    requested = (
        DEFAULT_PIPELINE_RUNTIME_CAPABILITIES
        if configured is None
        else frozenset(value.strip() for value in configured.split(",") if value.strip())
    )
    unknown = sorted(requested - DEFAULT_PIPELINE_RUNTIME_CAPABILITIES)
    if unknown:
        raise ValueError(f"unknown Pipeline DAG worker capabilities: {', '.join(unknown)}")
    return tuple(sorted(requested))


if __name__ == "__main__":
    main()
