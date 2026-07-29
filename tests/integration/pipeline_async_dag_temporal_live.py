"""PostgreSQL + real Temporal + two-process Pipeline DAG fault evidence."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from threading import Event
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineNodeRunRecord,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.pipeline_execution_repository import (
    SqlAlchemyPipelineExecutionRepository,
)
from sqlalchemy import create_engine, func, insert, select, text, update
from sqlalchemy.engine import Engine
from temporalio.client import Client

TENANT = "pipeline-live-tenant"
NOW = "2026-07-29T00:00:00Z"
HELPER = Path(__file__).parent / "helpers" / "pipeline_async_dag_live_worker.py"


@pytest.fixture
def live_database() -> Iterator[tuple[Engine, str]]:
    admin_url = os.getenv(
        "FOUNDRY_LITE_PIPELINE_LIVE_ADMIN_DB_URL",
        "postgresql+psycopg://foundry_lite:foundry_lite@127.0.0.1:15433/postgres",
    )
    postgres = urlsplit(admin_url)
    _wait_port(postgres.hostname or "127.0.0.1", postgres.port or 15433, timeout=30)
    _wait_port("127.0.0.1", 7233, timeout=60)
    database_name = f"foundry_lite_pipeline_{uuid4().hex[:12]}"
    admin = create_engine(admin_url, future=True, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    db_url = admin_url.rsplit("/", 1)[0] + f"/{database_name}"
    engine = create_engine(db_url, future=True)
    db.create_database(engine)
    _seed_catalog(engine)
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


def test_temporal_workers_take_over_cancel_and_commit_each_plane_once(
    live_database: tuple[Engine, str],
    tmp_path: Path,
) -> None:
    engine, db_url = live_database
    workers = _start_workers(db_url, tmp_path)
    try:
        asyncio.run(_run_live_scenarios(engine, workers, tmp_path))
    finally:
        _stop_workers(workers)


async def _run_live_scenarios(
    engine: Engine,
    workers: dict[str, subprocess.Popen[bytes]],
    marker_dir: Path,
) -> None:
    client = await Client.connect("127.0.0.1:7233")
    await _prove_takeover(client, engine, workers, marker_dir)
    await _prove_cancellation(client, engine, marker_dir)


async def _prove_takeover(
    client: Client,
    engine: Engine,
    workers: dict[str, subprocess.Popen[bytes]],
    marker_dir: Path,
) -> None:
    run_id = f"live-takeover-{uuid4().hex[:10]}"
    plan = _seed_run(engine, run_id, is_cancellation=False)
    handle = await client.start_workflow(
        "PipelineDagWorkflow",
        _workflow_payload(run_id, plan),
        id=f"pipeline-live:{run_id}",
        task_queue="foundry-lite-pipeline-dag-live",
    )
    marker = marker_dir / f"{run_id}-data-output.json"
    owner = await asyncio.to_thread(_wait_marker_owner, marker, 20)
    await asyncio.to_thread(_wait_media_commit, engine, run_id, 20)
    workers[owner].kill()
    workers[owner].wait(timeout=5)
    result = await asyncio.wait_for(handle.result(), timeout=75)
    assert result["status"] == "succeeded"
    _assert_takeover_evidence(engine, run_id)


async def _prove_cancellation(
    client: Client,
    engine: Engine,
    marker_dir: Path,
) -> None:
    run_id = f"live-cancel-{uuid4().hex[:10]}"
    plan = _seed_run(engine, run_id, is_cancellation=True)
    handle = await client.start_workflow(
        "PipelineDagWorkflow",
        _workflow_payload(run_id, plan),
        id=f"pipeline-live:{run_id}",
        task_queue="foundry-lite-pipeline-dag-live",
    )
    marker = marker_dir / f"{run_id}-data-output.json"
    marker_payload = await asyncio.to_thread(_wait_marker, marker, 20)
    with engine.begin() as transaction:
        transaction.execute(
            update(db.pipeline_runs)
            .where(db.pipeline_runs.c.id == run_id)
            .values(status="cancelling", cancel_requested_at=NOW)
        )
    await handle.cancel()
    await asyncio.to_thread(_wait_cancelled_attempt, engine, run_id, 20)
    pid = marker_payload["pid"]
    assert isinstance(pid, int)
    await asyncio.to_thread(_wait_process_exit, pid, 10)
    _assert_cancellation_evidence(engine, run_id)


def _start_workers(
    db_url: str,
    marker_dir: Path,
) -> dict[str, subprocess.Popen[bytes]]:
    workers: dict[str, subprocess.Popen[bytes]] = {}
    for worker_id in ("worker-a", "worker-b"):
        env = {
            **os.environ,
            "FOUNDRY_LITE_DB_URL": db_url,
            "FOUNDRY_LITE_TEMPORAL_ADDRESS": "127.0.0.1:7233",
            "FOUNDRY_LITE_PIPELINE_DAG_TASK_QUEUE": "foundry-lite-pipeline-dag-live",
            "FOUNDRY_LITE_WORKER_ID": worker_id,
            "FOUNDRY_LITE_LIVE_MARKER_DIR": str(marker_dir),
            "PYTHONPATH": ".:libs:apps/worker",
        }
        workers[worker_id] = subprocess.Popen(  # nosec B603 - fixed test helper argv.
            [sys.executable, str(HELPER)],
            cwd=Path(__file__).parents[2],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    _wait_until(lambda: all(process.poll() is None for process in workers.values()), 5)
    Event().wait(1)
    return workers


def _stop_workers(workers: dict[str, subprocess.Popen[bytes]]) -> None:
    for process in workers.values():
        if process.poll() is None:
            process.terminate()
    for process in workers.values():
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _seed_catalog(engine: Engine) -> None:
    with engine.begin() as transaction:
        transaction.execute(
            insert(db.datasets).values(
                id="live-dataset",
                tenant_id=TENANT,
                namespace="live",
                name="dataset",
                storage_kind="local",
                status="active",
                primary_key=[],
                partition_spec=[],
                sort_order=[],
                created_at=NOW,
                updated_at=NOW,
            )
        )
        transaction.execute(
            insert(db.dataset_schemas).values(
                id="live-dataset-schema",
                dataset_id="live-dataset",
                version=1,
                schema_json={"fields": []},
                schema_hash="live-schema",
                created_at=NOW,
            )
        )
        transaction.execute(
            insert(db.media_sets).values(
                id="live-media-set",
                tenant_id=TENANT,
                namespace="live",
                name="media",
                schema_type="image",
                primary_format="png",
                allowed_input_formats=["png"],
                transaction_policy="append",
                storage_profile="local",
                processing_profile="none",
                classification="internal",
                is_virtual=False,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        transaction.execute(
            insert(db.media_items).values(
                id="live-media-item",
                tenant_id=TENANT,
                media_set_id="live-media-set",
                logical_path="live.png",
                is_deleted=False,
                created_at=NOW,
                updated_at=NOW,
            )
        )


def _seed_run(
    engine: Engine,
    run_id: str,
    *,
    is_cancellation: bool,
) -> dict[str, object]:
    repository = SqlAlchemyPipelineExecutionRepository(engine)
    nodes = [
        {"nodeId": "data-output", "plane": "data", "blockFirstAttempt": True},
        {"nodeId": "media-output", "plane": "media"},
    ]
    edges = (
        [{"edgeId": "after-data", "sourceNodeId": "data-output", "targetNodeId": "media-output"}]
        if is_cancellation
        else []
    )
    with engine.begin() as transaction:
        transaction.execute(
            insert(db.pipeline_runs).values(
                id=run_id,
                tenant_id=TENANT,
                pipeline_id="live-pipeline",
                version_id="live-version",
                status="queued",
                idempotency_key=run_id,
                timeline=[],
                created_by="live-test",
                started_at=NOW,
            )
        )
        for node in nodes:
            repository.insert_node_run(
                transaction=transaction,
                record=PipelineNodeRunRecord(
                    node_run_id=f"{run_id}:node:{node['nodeId']}",
                    tenant_id=TENANT,
                    run_id=run_id,
                    node_id=str(node["nodeId"]),
                    descriptor_id=f"output.{node['plane']}",
                    spec_version="1",
                    input_artifacts=[],
                    created_at=NOW,
                ),
            )
    return {
        "nodes": [
            {
                **node,
                "executionPolicy": {
                    "maximumAttempts": 3,
                    "initialBackoffSeconds": 1,
                    "maximumBackoffSeconds": 2,
                    "timeoutSeconds": 60,
                },
            }
            for node in nodes
        ],
        "edges": edges,
    }


def _workflow_payload(run_id: str, plan: dict[str, object]) -> dict[str, object]:
    return {
        "tenant_id": TENANT,
        "run_id": run_id,
        "pipeline_id": "live-pipeline",
        "version_id": "live-version",
        "request_id": f"request:{run_id}",
        "idempotency_key": run_id,
        "execution_plan": plan,
        "target_node_ids": [],
        "is_commit_forbidden": False,
    }


def _assert_takeover_evidence(engine: Engine, run_id: str) -> None:
    with engine.begin() as transaction:
        attempts = (
            transaction.execute(
                select(db.pipeline_node_attempts)
                .join(
                    db.pipeline_node_runs,
                    db.pipeline_node_runs.c.id == db.pipeline_node_attempts.c.node_run_id,
                )
                .where(
                    db.pipeline_node_runs.c.run_id == run_id,
                    db.pipeline_node_runs.c.node_id == "data-output",
                )
                .order_by(db.pipeline_node_attempts.c.attempt_number)
            )
            .mappings()
            .all()
        )
        assert [row["status"] for row in attempts] == ["LOST", "SUCCEEDED"]
        assert [row["fencing_token"] for row in attempts] == [1, 2]
        assert _count(transaction, db.dataset_versions, run_id) == 1
        assert _count(transaction, db.media_item_versions, run_id) == 1
        artifacts = transaction.execute(
            select(func.count())
            .select_from(db.pipeline_run_artifacts)
            .where(db.pipeline_run_artifacts.c.run_id == run_id)
        ).scalar_one()
        assert artifacts == 2


def _assert_cancellation_evidence(engine: Engine, run_id: str) -> None:
    with engine.begin() as transaction:
        run_status = transaction.execute(
            select(db.pipeline_runs.c.status).where(db.pipeline_runs.c.id == run_id)
        ).scalar_one()
        attempt_status = transaction.execute(
            select(db.pipeline_node_attempts.c.status)
            .join(
                db.pipeline_node_runs,
                db.pipeline_node_runs.c.id == db.pipeline_node_attempts.c.node_run_id,
            )
            .where(db.pipeline_node_runs.c.run_id == run_id)
        ).scalar_one()
        downstream = transaction.execute(
            select(db.pipeline_node_runs.c.status).where(db.pipeline_node_runs.c.id == f"{run_id}:node:media-output")
        ).scalar_one()
        assert run_status == "cancelled"
        assert attempt_status == "CANCELLED"
        assert downstream == "CANCELLED"
        assert _count(transaction, db.dataset_versions, run_id) == 0
        assert _count(transaction, db.media_item_versions, run_id) == 0


def _count(transaction: Any, table: Any, run_id: str) -> int:
    value = transaction.execute(
        select(func.count()).select_from(table).where(table.c.id.like(f"{run_id}:%"))
    ).scalar_one()
    return int(value)


def _wait_marker_owner(path: Path, timeout: float) -> str:
    return str(_wait_marker(path, timeout)["workerId"])


def _wait_marker(path: Path, timeout: float) -> dict[str, object]:
    _wait_until(path.exists, timeout)
    return json.loads(path.read_text(encoding="utf-8"))


def _wait_media_commit(engine: Engine, run_id: str, timeout: float) -> None:
    def committed() -> bool:
        with engine.begin() as transaction:
            return _count(transaction, db.media_item_versions, run_id) == 1

    _wait_until(committed, timeout)


def _wait_cancelled_attempt(engine: Engine, run_id: str, timeout: float) -> None:
    def cancelled() -> bool:
        with engine.begin() as transaction:
            status = transaction.execute(
                select(db.pipeline_node_attempts.c.status)
                .join(
                    db.pipeline_node_runs,
                    db.pipeline_node_runs.c.id == db.pipeline_node_attempts.c.node_run_id,
                )
                .where(db.pipeline_node_runs.c.run_id == run_id)
            ).scalar_one_or_none()
        return status == "CANCELLED"

    _wait_until(cancelled, timeout)


def _wait_process_exit(pid: int, timeout: float) -> None:
    def exited() -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False

    _wait_until(exited, timeout)


def _wait_port(host: str, port: int, *, timeout: float) -> None:
    def available() -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            return False

    _wait_until(available, timeout)


def _wait_until(predicate: Callable[[], bool], timeout: float) -> None:
    deadline = _monotonic() + timeout
    while _monotonic() < deadline:
        if predicate():
            return
        Event().wait(0.1)
    raise AssertionError("timed out waiting for live distributed evidence")


def _monotonic() -> float:
    import time

    return time.monotonic()
