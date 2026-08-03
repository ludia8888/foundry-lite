"""Public-path proof for function-backed durable Action execution."""

from __future__ import annotations

from pathlib import Path
from threading import Event

import yaml
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure import schema as db
from sqlalchemy import update


def _edit_batch(expected_version: int) -> dict[str, object]:
    return {
        "edits": [
            {
                "kind": "modifyObject",
                "objectType": "Order",
                "objectId": "O-1",
                "expectedVersion": expected_version,
                "patch": {"status": "APPROVED"},
            }
        ],
        "readSetVersions": {"Order:O-1": expected_version},
        "provenance": {"adapter": "logic_dag", "test": "durable-action"},
    }


def _definition(expected_version: int) -> dict[str, object]:
    edit_batch = _edit_batch(expected_version)
    return {
        "objectTypes": [
            {
                "apiName": "Order",
                "primaryKey": "orderId",
                "backing": {
                    "dataset": "clean.async_orders",
                    "mode": "snapshot",
                    "primaryKeyColumns": ["order_id"],
                },
                "properties": [
                    {
                        "apiName": "orderId",
                        "column": "order_id",
                        "type": "string",
                        "nullable": False,
                        "indexed": True,
                    },
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
                "inputs": [],
                "output": {"type": "ontology_edit_batch"},
                "permissions": {"allowedRoles": ["admin"]},
                "definition": {
                    "tools": [],
                    "blocks": [
                        {"blockId": "batch", "kind": "Input", "inputs": edit_batch},
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
                "riskLevel": "high",
                "agentExecutionPolicy": "approval_required",
                "permissions": {"allowedRoles": ["admin"]},
                "function": {"apiName": "approveOrderEdits", "version": "1.0.0"},
            }
        ],
    }


def _prepare(foundry: FoundryLite, tmp_path: Path) -> int:
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,status\nO-1,PENDING\n", encoding="utf-8")
    foundry.datasets.ensure("clean.async_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("clean.async_orders", str(csv_path), ctx=ctx)
    foundry.ontology.apply_text(yaml.safe_dump(_definition(1), sort_keys=False), ctx=ctx)
    foundry.objects.reindex("Order", ctx=ctx)
    return int(foundry.objects.get("Order", "O-1", ctx=ctx)["objectVersion"])


def test_function_action_runs_once_through_durable_local_orchestrator(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    version = _prepare(foundry, tmp_path)
    assert version == 1

    started = foundry.actions.start_run(
        "ApproveOrderAsync",
        object_type="Order",
        object_id="O-1",
        expected_object_version=version,
        params={},
        idempotency_key="approve-order-async-1",
        wait_seconds=5,
        ctx=ctx,
    )
    replay = foundry.actions.start_run(
        "ApproveOrderAsync",
        object_type="Order",
        object_id="O-1",
        expected_object_version=version,
        params={},
        idempotency_key="approve-order-async-1",
        wait_seconds=0,
        ctx=ctx,
    )

    assert started["status"] == "succeeded"
    assert replay["actionRunId"] == started["actionRunId"]
    assert replay["status"] == "succeeded"
    assert foundry.objects.get("Order", "O-1", ctx=ctx)["properties"]["status"] == "APPROVED", (
        started["result"],
        started["attempts"][0]["output"],
    )
    assert started["steps"][0]["status"] == "succeeded"
    assert len(started["attempts"]) == 1
    assert started["attempts"][0]["fencingToken"] == 1
    assert started["attempts"][0]["externalExecutionId"] == f"{started['actionRunId']}:function"

    events = foundry.actions.events(str(started["actionRunId"]), ctx=ctx)["events"]
    assert [event["id"] for event in events] == list(range(1, len(events) + 1))
    assert events[-1]["event"] == "action.run.succeeded"


def test_transient_function_failures_retry_with_new_fencing_tokens(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    version = _prepare(foundry, tmp_path)
    calls = 0

    def flaky_driver(_request) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ConnectionError("temporary function adapter outage")
        return {"output": _edit_batch(version), "logicRunId": "stable-remote-execution"}

    foundry._services.action.distributed.action_function_executor.register_driver(flaky_driver)
    run = foundry.actions.start_run(
        "ApproveOrderAsync",
        object_type="Order",
        object_id="O-1",
        expected_object_version=version,
        params={},
        idempotency_key="approve-order-retry-1",
        wait_seconds=8,
        ctx=ctx,
    )

    assert run["status"] == "succeeded"
    assert calls == 3
    assert [attempt["status"] for attempt in run["attempts"]] == ["failed", "failed", "succeeded"]
    assert [attempt["fencingToken"] for attempt in run["attempts"]] == [1, 2, 3]
    assert [attempt["errorKind"] for attempt in run["attempts"][:2]] == [
        "transient_adapter",
        "transient_adapter",
    ]


def test_running_function_cancellation_blocks_the_ontology_commit(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    version = _prepare(foundry, tmp_path)
    entered = Event()
    release = Event()

    def blocking_driver(_request) -> dict[str, object]:
        entered.set()
        assert release.wait(5)
        return {"output": _edit_batch(version), "logicRunId": "cancelled-remote-execution"}

    foundry._services.action.distributed.action_function_executor.register_driver(blocking_driver)
    started = foundry.actions.start_run(
        "ApproveOrderAsync",
        object_type="Order",
        object_id="O-1",
        expected_object_version=version,
        params={},
        idempotency_key="approve-order-cancel-1",
        wait_seconds=0,
        ctx=ctx,
    )
    assert entered.wait(2)
    cancelling = foundry.actions.cancel(
        str(started["actionRunId"]), idempotency_key="cancel-action-1", reason="operator request", ctx=ctx
    )
    assert cancelling["status"] == "cancelling"
    release.set()
    terminal = foundry.actions.start_run(
        "ApproveOrderAsync",
        object_type="Order",
        object_id="O-1",
        expected_object_version=version,
        params={},
        idempotency_key="approve-order-cancel-1",
        wait_seconds=5,
        ctx=ctx,
    )

    assert terminal["status"] == "cancelled"
    assert terminal["attempts"][0]["status"] == "cancelled"
    assert foundry.objects.get("Order", "O-1", ctx=ctx)["properties"]["status"] == "PENDING"


def test_control_worker_takes_over_expired_cancelled_attempt(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    version = _prepare(foundry, tmp_path)
    entered = Event()
    release = Event()

    def crashed_driver(_request) -> dict[str, object]:
        entered.set()
        assert release.wait(5)
        return {"output": _edit_batch(version), "logicRunId": "late-worker-result"}

    foundry._services.action.distributed.action_function_executor.register_driver(crashed_driver)
    started = foundry.actions.start_run(
        "ApproveOrderAsync",
        object_type="Order",
        object_id="O-1",
        expected_object_version=version,
        params={},
        idempotency_key="approve-order-cancel-takeover-1",
        wait_seconds=0,
        ctx=ctx,
    )
    assert entered.wait(2)
    run_id = str(started["actionRunId"])
    foundry.actions.cancel(run_id, idempotency_key="cancel-takeover-1", reason="worker lost", ctx=ctx)
    with foundry.engine.begin() as transaction:
        transaction.execute(
            update(db.action_step_attempts)
            .where(db.action_step_attempts.c.tenant_id == ctx.tenant_id)
            .values(lease_expires_at="2000-01-01T00:00:00Z")
        )

    recovered = foundry._services.action.distributed.recover_all_cancellations(
        worker_id="action-control-test", limit=100
    )
    terminal = foundry.actions.get_run(run_id, ctx=ctx)
    release.set()

    assert recovered == {"cancelled": 1}
    assert terminal["status"] == "cancelled"
    assert [attempt["status"] for attempt in terminal["attempts"]] == ["lost", "cancelled"]
    assert [attempt["fencingToken"] for attempt in terminal["attempts"]] == [1, 2]
    assert foundry.objects.get("Order", "O-1", ctx=ctx)["properties"]["status"] == "PENDING"
