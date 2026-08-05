"""Temporal worker entrypoint for durable Action runs."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from pathlib import Path

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.infrastructure.adapters.action_run_orchestrator import ACTION_RUN_TASK_QUEUE
from foundry_lite.infrastructure.adapters.action_temporal_workflows import (
    ACTION_RUN_RESULT_PREFIX,
    ActionRunActivities,
    ActionRunWorkflow,
    action_run_dispatch_request_from_payload,
)
from foundry_lite.infrastructure.adapters.temporal_workflows import foundry_sandbox_runner
from foundry_lite.infrastructure.local_runtime import create_runtime_core_dependencies
from temporalio.client import Client
from temporalio.worker import Worker


async def run_worker() -> None:
    client = await Client.connect(
        os.getenv("FOUNDRY_LITE_TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=os.getenv("FOUNDRY_LITE_TEMPORAL_NAMESPACE", "default"),
    )
    worker_id = os.getenv("FOUNDRY_LITE_WORKER_ID", f"{socket.gethostname()}:{os.getpid()}")
    activities = ActionRunActivities(
        None,
        worker_id=worker_id,
        activity_subprocess_argv=(sys.executable, str(Path(__file__).resolve()), "activity"),
        termination_grace_seconds=float(os.getenv("FOUNDRY_LITE_ACTION_TERMINATION_GRACE_SECONDS", "5")),
    )
    worker = Worker(
        client,
        task_queue=os.getenv("FOUNDRY_LITE_ACTION_RUN_TASK_QUEUE", ACTION_RUN_TASK_QUEUE),
        workflows=[ActionRunWorkflow],
        activities=[activities.drive],
        workflow_runner=foundry_sandbox_runner(),
    )
    await worker.run()


def run_activity() -> None:
    payload = json.loads(sys.stdin.buffer.read())
    if not isinstance(payload, dict):
        raise ValueError("Action activity payload must be an object")
    worker_id = str(payload.pop("worker_id"))
    foundry = FoundryLite(
        dependencies=create_runtime_core_dependencies(
            db_url=os.getenv("FOUNDRY_LITE_DB_URL"),
            storage_root=os.getenv("FOUNDRY_LITE_STORAGE_ROOT"),
        ),
        should_initialize_schema=False,
    )
    request = action_run_dispatch_request_from_payload(payload)
    result = foundry._services.action.distributed.drive(request, worker_id=worker_id)
    sys.stdout.buffer.write(ACTION_RUN_RESULT_PREFIX + json.dumps(result, sort_keys=True).encode() + b"\n")


def main() -> None:
    if sys.argv[1:] == ["activity"]:
        run_activity()
        return
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
