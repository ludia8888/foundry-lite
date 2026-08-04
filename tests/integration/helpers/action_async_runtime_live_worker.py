"""Real Temporal Action worker with a crashable isolated activity subprocess."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from collections.abc import Mapping
from pathlib import Path
from threading import Event
from typing import Any, cast

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.action_function_executor import LogicDagActionFunctionExecutor
from foundry_lite.infrastructure.adapters.action_run_orchestrator import ACTION_RUN_TASK_QUEUE
from foundry_lite.infrastructure.adapters.action_temporal_workflows import (
    ACTION_RUN_RESULT_PREFIX,
    ActionRunActivities,
    ActionRunWorkflow,
    action_run_dispatch_request_from_payload,
)
from foundry_lite.infrastructure.adapters.temporal_workflows import foundry_sandbox_runner
from foundry_lite.infrastructure.local_runtime import create_runtime_core_dependencies
from sqlalchemy import select
from temporalio.client import Client
from temporalio.worker import Worker


async def run_worker() -> None:
    client = await Client.connect(os.environ["FOUNDRY_LITE_TEMPORAL_ADDRESS"])
    worker_id = os.getenv("FOUNDRY_LITE_WORKER_ID", f"{socket.gethostname()}:{os.getpid()}")
    activities = ActionRunActivities(
        None,
        worker_id=worker_id,
        activity_subprocess_argv=(sys.executable, str(Path(__file__).resolve()), "activity"),
        termination_grace_seconds=1,
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
        raise ValueError("Action live payload must be an object")
    worker_id = str(payload.pop("worker_id"))
    foundry = FoundryLite(
        dependencies=create_runtime_core_dependencies(
            db_url=os.environ["FOUNDRY_LITE_DB_URL"],
            storage_root=os.environ["FOUNDRY_LITE_STORAGE_ROOT"],
        )
    )
    executor = cast(
        LogicDagActionFunctionExecutor,
        foundry._services.action.distributed.action_function_executor,
    )
    executor.register_driver(lambda request: _function_result(foundry, request.run_id, request.inputs, worker_id))
    request = action_run_dispatch_request_from_payload(payload)
    result = foundry._services.action.distributed.drive(request, worker_id=worker_id)
    sys.stdout.buffer.write(ACTION_RUN_RESULT_PREFIX + json.dumps(result, sort_keys=True).encode() + b"\n")


def _function_result(
    foundry: FoundryLite, run_id: str, inputs: Mapping[str, object], worker_id: str
) -> dict[str, object]:
    attempt_number = _latest_attempt_number(foundry, run_id)
    if inputs.get("shouldBlock") is True and attempt_number == 1:
        _write_marker(run_id, worker_id)
        Event().wait(30)
    object_id = str(inputs["objectId"])
    expected_version = inputs["expectedVersion"]
    if not isinstance(expected_version, int):
        raise ValueError("expectedVersion must be an integer")
    return {
        "logicRunId": f"{run_id}:logic",
        "output": {
            "edits": [
                {
                    "kind": "modifyObject",
                    "objectType": "Order",
                    "objectId": object_id,
                    "expectedVersion": expected_version,
                    "patch": {"status": "APPROVED"},
                }
            ],
            "readSetVersions": {f"Order:{object_id}": expected_version},
            "provenance": {"adapter": "live-temporal", "workerId": worker_id},
        },
    }


def _latest_attempt_number(foundry: FoundryLite, run_id: str) -> int:
    with foundry.engine.begin() as transaction:
        value = (
            cast(Any, transaction)
            .execute(
                select(db.action_step_attempts.c.attempt_number)
                .select_from(
                    db.action_step_attempts.join(
                        db.action_run_steps, db.action_step_attempts.c.step_id == db.action_run_steps.c.id
                    )
                )
                .where(db.action_run_steps.c.run_id == run_id)
                .order_by(db.action_step_attempts.c.attempt_number.desc())
                .limit(1)
            )
            .scalar_one()
        )
    return int(value)


def _write_marker(run_id: str, worker_id: str) -> None:
    marker_dir = Path(os.environ["FOUNDRY_LITE_LIVE_MARKER_DIR"])
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / f"{run_id}.json").write_text(
        json.dumps({"pid": os.getpid(), "workerId": worker_id}), encoding="utf-8"
    )


def main() -> None:
    if sys.argv[1:] == ["activity"]:
        run_activity()
        return
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
