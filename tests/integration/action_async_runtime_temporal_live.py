"""PostgreSQL + real Temporal + two-worker Action fault and recovery proof."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import cast
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import yaml
from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.action_run_orchestrator import (
    ActionRunDispatchRequest,
    ActionRunDispatchResult,
)
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.action_run_orchestrator import action_run_workflow_id
from foundry_lite.infrastructure.local_runtime import create_runtime_core_dependencies
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine

HELPER = Path(__file__).parent / "helpers" / "action_async_runtime_live_worker.py"
CONTROL = Path(__file__).parents[2] / "apps" / "worker" / "foundry_lite_worker" / "action_control.py"
TASK_QUEUE = "foundry-lite-action-runs-live"


class UnknownActionRunOrchestrator:
    profile_name = "live-dispatch-unknown"

    def dispatch(self, request: ActionRunDispatchRequest) -> ActionRunDispatchResult:
        return ActionRunDispatchResult(action_run_workflow_id(request.tenant_id, request.run_id), "unknown", TASK_QUEUE)

    def cancel(self, tenant_id: str, workflow_run_id: str, *, reason: str | None = None) -> bool:
        del tenant_id, workflow_run_id, reason
        return False


@pytest.fixture
def action_live_database() -> Iterator[tuple[Engine, str]]:
    admin_url = os.getenv(
        "FOUNDRY_LITE_ACTION_LIVE_ADMIN_DB_URL",
        "postgresql+psycopg://foundry_lite:foundry_lite@127.0.0.1:15433/postgres",
    )
    endpoint = urlsplit(admin_url)
    _wait_port(endpoint.hostname or "127.0.0.1", endpoint.port or 15433, 30)
    _wait_port("127.0.0.1", 7233, 60)
    database_name = f"foundry_lite_action_{uuid4().hex[:12]}"
    admin = create_engine(admin_url, future=True, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    db_url = admin_url.rsplit("/", 1)[0] + f"/{database_name}"
    engine = create_engine(db_url, future=True)
    try:
        yield engine, db_url
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin.dispose()


def test_action_workers_take_over_cancel_and_recover_unknown_dispatch(
    action_live_database: tuple[Engine, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, db_url = action_live_database
    _configure_runtime(monkeypatch, db_url, tmp_path)
    foundry = FoundryLite(dependencies=create_runtime_core_dependencies(db_url=db_url, storage_root=tmp_path / "api"))
    versions = _prepare_catalog(foundry, tmp_path)
    processes = _start_processes(db_url, tmp_path)
    try:
        _run_scenarios(foundry, engine, versions, processes, db_url, tmp_path)
    finally:
        _stop_processes(processes)


def _run_scenarios(
    foundry: FoundryLite,
    engine: Engine,
    versions: dict[str, int],
    processes: dict[str, subprocess.Popen[bytes]],
    db_url: str,
    tmp_path: Path,
) -> None:
    _prove_takeover(foundry, engine, versions["O-1"], processes, tmp_path)
    _prove_cancellation(foundry, engine, versions["O-2"], tmp_path)
    _prove_dispatch_recovery(foundry, engine, versions["O-3"], processes, db_url, tmp_path)


def _prove_takeover(
    foundry: FoundryLite,
    engine: Engine,
    version: int,
    processes: dict[str, subprocess.Popen[bytes]],
    marker_dir: Path,
) -> None:
    run = foundry.actions.start_run(
        "ApproveOrderAsync",
        object_type="Order",
        object_id="O-1",
        expected_object_version=version,
        params={"objectId": "O-1", "expectedVersion": version, "shouldBlock": True},
        idempotency_key="live-action-takeover",
        wait_seconds=0,
        ctx=demo_admin_context(),
    )
    run_id = str(run["actionRunId"])
    marker = _wait_marker(marker_dir / f"{run_id}.json", 20)
    owner = str(marker["workerId"])
    marker_pid = marker["pid"]
    assert isinstance(marker_pid, int)
    os.kill(marker_pid, signal.SIGKILL)
    processes[owner].kill()
    processes[owner].wait(timeout=5)
    terminal = _wait_terminal(foundry, run_id, 45)

    assert terminal["status"] == "succeeded"
    attempts = cast(list[dict[str, object]], terminal["attempts"])
    assert [item["status"] for item in attempts] == ["lost", "succeeded"]
    assert [item["fencingToken"] for item in attempts] == [1, 2]
    _assert_single_commit(foundry, engine, run_id, "O-1", expected_status="APPROVED")


def _prove_cancellation(foundry: FoundryLite, engine: Engine, version: int, marker_dir: Path) -> None:
    run = foundry.actions.start_run(
        "ApproveOrderAsync",
        object_type="Order",
        object_id="O-2",
        expected_object_version=version,
        params={"objectId": "O-2", "expectedVersion": version, "shouldBlock": True},
        idempotency_key="live-action-cancel",
        wait_seconds=0,
        ctx=demo_admin_context(),
    )
    run_id = str(run["actionRunId"])
    marker = _wait_marker(marker_dir / f"{run_id}.json", 20)
    foundry.actions.cancel(
        run_id, idempotency_key="live-action-cancel-request", reason="operator stop", ctx=demo_admin_context()
    )
    terminal = _wait_terminal(foundry, run_id, 30)

    assert terminal["status"] == "cancelled"
    marker_pid = marker["pid"]
    assert isinstance(marker_pid, int)
    _wait_process_exit(marker_pid, 10)
    attempts = cast(list[dict[str, object]], terminal["attempts"])
    assert [item["status"] for item in attempts] == ["lost", "cancelled"]
    _assert_single_commit(foundry, engine, run_id, "O-2", expected_status="PENDING", expected_edits=0)


def _prove_dispatch_recovery(
    foundry: FoundryLite,
    engine: Engine,
    version: int,
    processes: dict[str, subprocess.Popen[bytes]],
    db_url: str,
    tmp_path: Path,
) -> None:
    control = processes["control"]
    control.terminate()
    control.wait(timeout=5)
    base = create_runtime_core_dependencies(db_url=db_url, storage_root=tmp_path / "unknown-api")
    dependencies = CoreDependencies(
        paths=base.paths,
        security=base.security,
        action=replace(base.action, action_run_orchestrator=UnknownActionRunOrchestrator()),
        data=base.data,
        object_store=base.object_store,
        runtime=base.runtime,
        aip=base.aip,
        media=base.media,
        source=base.source,
        pipeline_dag_orchestrator=base.pipeline_dag_orchestrator,
        profile=base.profile,
    )
    uncertain_api = FoundryLite(dependencies=dependencies)
    run = uncertain_api.actions.start_run(
        "ApproveOrderAsync",
        object_type="Order",
        object_id="O-3",
        expected_object_version=version,
        params={"objectId": "O-3", "expectedVersion": version, "shouldBlock": False},
        idempotency_key="live-action-dispatch-recovery",
        wait_seconds=0,
        ctx=demo_admin_context(),
    )
    run_id = str(run["actionRunId"])
    orchestration = cast(dict[str, object], run["orchestration"])
    assert orchestration["dispatchStatus"] == "unknown"
    processes["control"] = _spawn([sys.executable, str(CONTROL)], db_url, tmp_path, "action-control-restarted")
    terminal = _wait_terminal(foundry, run_id, 30)

    assert terminal["status"] == "succeeded"
    orchestration = cast(dict[str, object], terminal["orchestration"])
    dispatch_attempts = orchestration["dispatchAttempts"]
    assert isinstance(dispatch_attempts, int) and dispatch_attempts >= 2
    _assert_single_commit(foundry, engine, run_id, "O-3", expected_status="APPROVED")


def _configure_runtime(monkeypatch: pytest.MonkeyPatch, db_url: str, tmp_path: Path) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_DB_URL", db_url)
    monkeypatch.setenv("FOUNDRY_LITE_TEMPORAL_ADDRESS", "127.0.0.1:7233")
    monkeypatch.setenv("FOUNDRY_LITE_WORKFLOW_PROFILE", "temporal")
    monkeypatch.setenv("FOUNDRY_LITE_ACTION_RUN_TASK_QUEUE", TASK_QUEUE)
    monkeypatch.setenv("FOUNDRY_LITE_ACTION_STEP_LEASE_SECONDS", "2")
    monkeypatch.setenv("FOUNDRY_LITE_ACTION_CONTROL_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("FOUNDRY_LITE_STORAGE_ROOT", str(tmp_path / "runtime"))


def _prepare_catalog(foundry: FoundryLite, tmp_path: Path) -> dict[str, int]:
    ctx = demo_admin_context()
    csv_path = tmp_path / "live-orders.csv"
    csv_path.write_text("order_id,status\nO-1,PENDING\nO-2,PENDING\nO-3,PENDING\n", encoding="utf-8")
    foundry.datasets.ensure("clean.action_live_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("clean.action_live_orders", str(csv_path), ctx=ctx)
    foundry.ontology.apply_text(yaml.safe_dump(_ontology_definition(), sort_keys=False), ctx=ctx)
    foundry.objects.reindex("Order", ctx=ctx)
    return {
        object_id: int(foundry.objects.get("Order", object_id, ctx=ctx)["objectVersion"])
        for object_id in ("O-1", "O-2", "O-3")
    }


def _ontology_definition() -> dict[str, object]:
    parameters = [
        {"apiName": "objectId", "type": "string", "required": True},
        {"apiName": "expectedVersion", "type": "integer", "required": True},
        {"apiName": "shouldBlock", "type": "boolean", "required": True},
    ]
    return {
        "objectTypes": [
            {
                "apiName": "Order",
                "primaryKey": "orderId",
                "backing": {
                    "dataset": "clean.action_live_orders",
                    "mode": "snapshot",
                    "primaryKeyColumns": ["order_id"],
                },
                "properties": [
                    {"apiName": "orderId", "column": "order_id", "type": "string", "nullable": False},
                    {
                        "apiName": "status",
                        "column": "status",
                        "type": "string",
                        "editable": True,
                        "editPolicy": "edit_wins",
                    },
                ],
            }
        ],
        "functionTypes": [
            {
                "apiName": "approveOrderEdits",
                "version": "1.0.0",
                "runtime": "logic_dag",
                "inputs": parameters,
                "output": {"type": "ontology_edit_batch"},
                "permissions": {"allowedRoles": ["admin"]},
                "definition": {
                    "tools": [],
                    "blocks": [
                        {"blockId": "batch", "kind": "Input", "inputs": {"edits": []}},
                        {
                            "blockId": "output",
                            "kind": "Output",
                            "dependsOn": ["batch"],
                            "inputs": {"fromBlock": "batch"},
                        },
                    ],
                },
            }
        ],
        "actionTypes": [
            {
                "apiName": "ApproveOrderAsync",
                "contractVersion": 3,
                "target": "Order",
                "parameters": parameters,
                "riskLevel": "high",
                "agentExecutionPolicy": "approval_required",
                "permissions": {"allowedRoles": ["admin"]},
                "function": {"apiName": "approveOrderEdits", "version": "1.0.0"},
            }
        ],
    }


def _start_processes(db_url: str, marker_dir: Path) -> dict[str, subprocess.Popen[bytes]]:
    processes: dict[str, subprocess.Popen[bytes]] = {}
    for worker_id in ("worker-a", "worker-b"):
        processes[worker_id] = _spawn([sys.executable, str(HELPER)], db_url, marker_dir, worker_id)
    processes["control"] = _spawn([sys.executable, str(CONTROL)], db_url, marker_dir, "action-control-live")
    _wait_until(lambda: all(process.poll() is None for process in processes.values()), 5)
    Event().wait(1)
    return processes


def _spawn(argv: list[str], db_url: str, marker_dir: Path, worker_id: str) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "FOUNDRY_LITE_DB_URL": db_url,
        "FOUNDRY_LITE_STORAGE_ROOT": str(marker_dir / worker_id),
        "FOUNDRY_LITE_WORKER_ID": worker_id,
        "FOUNDRY_LITE_LIVE_MARKER_DIR": str(marker_dir),
        "PYTHONPATH": ".:libs:apps/worker",
    }
    return subprocess.Popen(  # nosec B603 - fixed local test helper argv.
        argv,
        cwd=Path(__file__).parents[2],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _stop_processes(processes: dict[str, subprocess.Popen[bytes]]) -> None:
    for process in processes.values():
        if process.poll() is None:
            process.terminate()
    for process in processes.values():
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _assert_single_commit(
    foundry: FoundryLite,
    engine: Engine,
    run_id: str,
    object_id: str,
    *,
    expected_status: str,
    expected_edits: int = 1,
) -> None:
    obj = foundry.objects.get("Order", object_id, ctx=demo_admin_context())
    with engine.begin() as transaction:
        edits = transaction.execute(
            select(func.count()).select_from(db.object_edits).where(db.object_edits.c.action_run_id == run_id)
        ).scalar_one()
    assert obj["properties"]["status"] == expected_status
    assert int(edits) == expected_edits


def _wait_terminal(foundry: FoundryLite, run_id: str, timeout: float) -> dict[str, object]:
    terminal = {"succeeded", "failed", "cancelled", "conflict", "outcome_unknown"}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = foundry.actions.get_run(run_id, ctx=demo_admin_context())
        if snapshot["status"] in terminal:
            return snapshot
        Event().wait(0.1)
    raise AssertionError(f"Action run did not finish: {run_id}")


def _wait_marker(path: Path, timeout: float) -> dict[str, object]:
    _wait_until(path.exists, timeout)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("invalid live Action marker")
    return value


def _wait_process_exit(pid: int, timeout: float) -> None:
    def exited() -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False

    _wait_until(exited, timeout)


def _wait_port(host: str, port: int, timeout: float) -> None:
    def ready() -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return True
        except OSError:
            return False

    _wait_until(ready, timeout)


def _wait_until(predicate: Callable[[], bool], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        Event().wait(0.05)
    raise AssertionError("timed out waiting for live Action evidence")
