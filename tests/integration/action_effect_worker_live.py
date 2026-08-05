"""PostgreSQL multi-process proof for fenced Action-effect takeover."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Lock, Thread
from typing import cast
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from foundry_lite.application.action_async_execution_types import (
    ActionAsyncRunRecord,
    ActionEffectReceiptRecord,
    ActionRunStepRecord,
)
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.action_runtime.action_contract import action_contract_payload, compile_action_contract
from foundry_lite.infrastructure.local_runtime import create_runtime_core_dependencies
from foundry_lite.security.tenant_context import tenant_context
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

HELPER = Path(__file__).parent / "helpers" / "action_effect_worker_live.py"
TENANT_ID = "tenant-effect-live"


class _ProviderState:
    def __init__(self) -> None:
        self.lock = Lock()
        self.calls: list[str] = []

    def record(self, idempotency_key: str) -> int:
        with self.lock:
            self.calls.append(idempotency_key)
            return len(self.calls)

    def counts(self) -> Counter[str]:
        with self.lock:
            return Counter(self.calls)


class _ProviderHandler(BaseHTTPRequestHandler):
    state: _ProviderState

    def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP handler contract.
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        call_number = self.state.record(self.headers["Idempotency-Key"])
        response = json.dumps({"id": f"provider-{call_number}", "accepted": True}).encode("utf-8")
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, _format: str, *args: object) -> None:
        del args


@pytest.fixture
def effect_live_database() -> Iterator[tuple[Engine, str]]:
    admin_url = os.getenv(
        "FOUNDRY_LITE_ACTION_LIVE_ADMIN_DB_URL",
        "postgresql+psycopg://foundry_lite:foundry_lite@127.0.0.1:15433/postgres",
    )
    endpoint = urlsplit(admin_url)
    _wait_port(endpoint.hostname or "127.0.0.1", endpoint.port or 15433, 30)
    database_name = f"foundry_lite_effect_{uuid4().hex[:12]}"
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


def test_effect_workers_take_over_without_duplicate_remote_delivery(
    effect_live_database: tuple[Engine, str], tmp_path: Path
) -> None:
    _engine, db_url = effect_live_database
    foundry = FoundryLite(dependencies=create_runtime_core_dependencies(db_url=db_url, storage_root=tmp_path / "api"))
    _seed_run(foundry)
    _seed_receipt(foundry, "before-dispatch")
    _seed_before_run(foundry, "run-before-pre")
    _seed_before_receipt(foundry, "run-before-pre")
    _seed_before_run(foundry, "run-before-post")
    state = _ProviderState()
    server, thread = _start_provider(state)
    provider_url = f"http://127.0.0.1:{server.server_port}/effects"
    try:
        _prove_pre_dispatch_takeover(foundry, db_url, tmp_path, provider_url, state)
        _seed_receipt(foundry, "after-dispatch")
        _prove_post_dispatch_ambiguity(foundry, db_url, tmp_path, provider_url, state)
        _prove_before_effect_pre_dispatch_takeover(foundry, db_url, tmp_path, provider_url, state)
        _prove_before_effect_post_dispatch_ambiguity(foundry, db_url, tmp_path, provider_url, state)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _prove_pre_dispatch_takeover(
    foundry: FoundryLite,
    db_url: str,
    marker_dir: Path,
    provider_url: str,
    state: _ProviderState,
) -> None:
    receipt_id = "effect-before-dispatch-crash"
    first = _spawn("claim-block", receipt_id, "effect-worker-a", db_url, marker_dir, provider_url)
    marker = _wait_marker(marker_dir / f"claimed-{receipt_id}.json", 10)
    _kill_marked_process(first, marker)
    _wait_lease_expired(foundry, receipt_id, 5)
    second = _spawn("deliver", receipt_id, "effect-worker-b", db_url, marker_dir, provider_url)
    _wait_success(second, 10)
    receipt = _receipt(foundry, receipt_id)

    assert receipt["status"] == "succeeded"
    assert receipt["attempt_count"] == 2
    assert receipt["fencing_token"] == 2
    assert state.counts()["action-effect:run-effect-live:before-dispatch"] == 1


def _prove_post_dispatch_ambiguity(
    foundry: FoundryLite,
    db_url: str,
    marker_dir: Path,
    provider_url: str,
    state: _ProviderState,
) -> None:
    receipt_id = "effect-after-dispatch-crash"
    first = _spawn(
        "deliver",
        receipt_id,
        "effect-worker-a",
        db_url,
        marker_dir,
        provider_url,
        should_block_after_dispatch=True,
    )
    marker = _wait_marker(marker_dir / f"dispatched-{receipt_id}.json", 10)
    _kill_marked_process(first, marker)
    _wait_lease_expired(foundry, receipt_id, 5)
    second = _spawn("deliver", receipt_id, "effect-worker-b", db_url, marker_dir, provider_url)
    _wait_success(second, 10)
    receipt = _receipt(foundry, receipt_id)

    assert receipt["status"] == "outcome_unknown"
    assert receipt["attempt_count"] == 2
    assert receipt["fencing_token"] == 2
    error = receipt["error"]
    assert isinstance(error, Mapping) and error["kind"] == "outcome_unknown"
    assert state.counts()["action-effect:run-effect-live:after-dispatch"] == 1


def _prove_before_effect_pre_dispatch_takeover(
    foundry: FoundryLite,
    db_url: str,
    marker_dir: Path,
    provider_url: str,
    state: _ProviderState,
) -> None:
    run_id = "run-before-pre"
    receipt_id = f"{run_id}:effect:before-provider"
    first = _spawn("claim-block", receipt_id, "before-worker-a", db_url, marker_dir, provider_url)
    marker = _wait_marker(marker_dir / f"claimed-{receipt_id}.json", 10)
    _kill_marked_process(first, marker)
    _wait_lease_expired(foundry, receipt_id, 5)
    second = _spawn("execute-before", run_id, "before-worker-b", db_url, marker_dir, provider_url)
    _wait_success(second, 10)
    receipt = _receipt(foundry, receipt_id)

    assert receipt["status"] == "succeeded"
    assert receipt["attempt_count"] == 2
    assert receipt["fencing_token"] == 2
    assert state.counts()[f"action-effect:{run_id}:before-provider"] == 1


def _prove_before_effect_post_dispatch_ambiguity(
    foundry: FoundryLite,
    db_url: str,
    marker_dir: Path,
    provider_url: str,
    state: _ProviderState,
) -> None:
    run_id = "run-before-post"
    receipt_id = f"{run_id}:effect:before-provider"
    first = _spawn(
        "execute-before",
        run_id,
        "before-worker-a",
        db_url,
        marker_dir,
        provider_url,
        should_block_after_dispatch=True,
    )
    marker = _wait_marker(marker_dir / f"dispatched-{receipt_id}.json", 10)
    _kill_marked_process(first, marker)
    _wait_lease_expired(foundry, receipt_id, 8)
    second = _spawn("execute-before", run_id, "before-worker-b", db_url, marker_dir, provider_url)
    _wait_success(second, 10)
    receipt = _receipt(foundry, receipt_id)

    assert receipt["status"] == "outcome_unknown"
    assert receipt["attempt_count"] == 2
    assert receipt["fencing_token"] == 2
    assert state.counts()[f"action-effect:{run_id}:before-provider"] == 1


def _seed_run(foundry: FoundryLite) -> None:
    repository = foundry._services.action_effects.action_execution_repository
    created_at = datetime.now(UTC).isoformat()
    run = ActionAsyncRunRecord(
        "run-effect-live",
        TENANT_ID,
        "action-effect-live",
        "DeliverLiveEffect",
        "user-effect-live",
        "otype-effect-live",
        "LiveObject",
        "object-1",
        1,
        {},
        "effect-live-run",
        "sha256:effect-live-request",
        "sha256:effect-live-definition",
        "sha256:effect-live-plan",
        {},
        created_at,
    )
    with tenant_context(TENANT_ID), foundry.engine.begin() as transaction:
        repository.insert_run(
            transaction=transaction,
            record=run,
            steps=(ActionRunStepRecord("step-effect-live", TENANT_ID, run.run_id, "commit", "commit", {}, created_at),),
        )


def _seed_receipt(foundry: FoundryLite, suffix: str) -> None:
    repository = foundry._services.action_effects.action_execution_repository
    created_at = datetime.now(UTC).isoformat()
    effect_id = f"{suffix}-effect"
    with tenant_context(TENANT_ID), foundry.engine.begin() as transaction:
        inserted = repository.insert_effect_receipt(
            transaction=transaction,
            record=ActionEffectReceiptRecord(
                f"effect-{suffix}-crash",
                TENANT_ID,
                "run-effect-live",
                effect_id,
                "after_commit",
                "event",
                "topic:live-provider",
                f"action-effect:run-effect-live:{suffix}",
                3,
                {
                    "effect": {
                        "effectId": effect_id,
                        "kind": "event",
                        "phase": "after_commit",
                        "targetRef": "topic:live-provider",
                        "maxAttempts": 3,
                    },
                    "actorUserId": "user-effect-live",
                    "requestId": f"seed-{suffix}",
                    "parameters": {},
                    "committedResult": {},
                },
                created_at,
            ),
        )
        assert inserted is not None


def _seed_before_run(foundry: FoundryLite, run_id: str) -> None:
    repository = foundry._services.action_effects.action_execution_repository
    created_at = datetime.now(UTC).isoformat()
    run = ActionAsyncRunRecord(
        run_id,
        TENANT_ID,
        f"action-{run_id}",
        "BeforeEffectLive",
        "user-effect-live",
        "otype-effect-live",
        "LiveObject",
        "object-1",
        1,
        {},
        f"idempotency-{run_id}",
        f"sha256:request-{run_id}",
        "sha256:before-effect-definition",
        "sha256:before-effect-plan",
        {"contract": _before_effect_contract()},
        created_at,
    )
    with tenant_context(TENANT_ID), foundry.engine.begin() as transaction:
        repository.insert_run(
            transaction=transaction,
            record=run,
            steps=(
                ActionRunStepRecord(
                    f"step-{run_id}",
                    TENANT_ID,
                    run_id,
                    "commit",
                    "commit",
                    {},
                    created_at,
                ),
            ),
        )


def _seed_before_receipt(foundry: FoundryLite, run_id: str) -> None:
    repository = foundry._services.action_effects.action_execution_repository
    created_at = datetime.now(UTC).isoformat()
    effects = cast(list[dict[str, object]], _before_effect_contract()["effects"])
    effect = effects[0]
    with tenant_context(TENANT_ID), foundry.engine.begin() as transaction:
        inserted = repository.insert_effect_receipt(
            transaction=transaction,
            record=ActionEffectReceiptRecord(
                f"{run_id}:effect:before-provider",
                TENANT_ID,
                run_id,
                "before-provider",
                "before_commit",
                "webhook",
                "connector:live/provider",
                f"action-effect:{run_id}:before-provider",
                1,
                {
                    "effect": effect,
                    "actorUserId": "user-effect-live",
                    "requestId": f"seed-{run_id}",
                    "parameters": {},
                    "committedResult": {},
                },
                created_at,
            ),
        )
        assert inserted is not None


def _before_effect_contract() -> dict[str, object]:
    contract = compile_action_contract(
        {
            "apiName": "BeforeEffectLive",
            "contractVersion": 3,
            "target": "LiveObject",
            "riskLevel": "high",
            "agentExecutionPolicy": "approval_required",
            "permissions": {"allowedRoles": ["admin"]},
            "rules": [],
            "effects": [
                {
                    "effectId": "before-provider",
                    "kind": "webhook",
                    "phase": "before_commit",
                    "targetRef": "connector:live/provider",
                    "timeoutSeconds": 1,
                }
            ],
        }
    )
    return action_contract_payload(contract)


def _receipt(foundry: FoundryLite, receipt_id: str) -> dict[str, object]:
    repository = foundry._services.action_effects.action_execution_repository
    with tenant_context(TENANT_ID), foundry.engine.begin() as transaction:
        row = repository.effect_receipt_by_id(
            transaction=transaction,
            tenant_id=TENANT_ID,
            receipt_id=receipt_id,
        )
    if row is None:
        raise AssertionError("live Action effect receipt disappeared")
    return cast(dict[str, object], row)


def _wait_lease_expired(foundry: FoundryLite, receipt_id: str, timeout: float) -> None:
    def expired() -> bool:
        lease_expires_at = _receipt(foundry, receipt_id)["lease_expires_at"]
        return isinstance(lease_expires_at, str) and lease_expires_at < datetime.now(UTC).isoformat()

    _wait_until(expired, timeout)


def _start_provider(state: _ProviderState) -> tuple[ThreadingHTTPServer, Thread]:
    handler = type("LiveEffectProviderHandler", (_ProviderHandler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _spawn(
    mode: str,
    receipt_id: str,
    worker_id: str,
    db_url: str,
    marker_dir: Path,
    provider_url: str,
    *,
    should_block_after_dispatch: bool = False,
) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "FOUNDRY_LITE_DB_URL": db_url,
        "FOUNDRY_LITE_STORAGE_ROOT": str(marker_dir / worker_id),
        "FOUNDRY_LITE_LIVE_MARKER_DIR": str(marker_dir),
        "FOUNDRY_LITE_EFFECT_PROVIDER_URL": provider_url,
        "FOUNDRY_LITE_EFFECT_BLOCK_AFTER_DISPATCH": "1" if should_block_after_dispatch else "0",
        "PYTHONPATH": ".:libs:apps/worker",
    }
    return subprocess.Popen(  # nosec B603 - fixed local live-test helper argv.
        [sys.executable, str(HELPER), mode, TENANT_ID, receipt_id, worker_id],
        cwd=Path(__file__).parents[2],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _kill_marked_process(process: subprocess.Popen[bytes], marker: dict[str, object]) -> None:
    pid = marker.get("pid")
    if not isinstance(pid, int) or pid != process.pid:
        raise AssertionError("live Action effect marker has an invalid process id")
    os.kill(pid, signal.SIGKILL)
    process.wait(timeout=5)


def _wait_success(process: subprocess.Popen[bytes], timeout: float) -> None:
    stdout, stderr = process.communicate(timeout=timeout)
    if process.returncode != 0:
        raise AssertionError(f"Action effect worker failed:\n{stdout.decode()}\n{stderr.decode()}")


def _wait_marker(path: Path, timeout: float) -> dict[str, object]:
    _wait_until(path.exists, timeout)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("invalid live Action effect marker")
    return value


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
    raise AssertionError("timed out waiting for live Action-effect evidence")
