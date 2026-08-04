"""Public-path proof for function-backed durable Action execution."""

from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import cast

import yaml
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.action_effect_executor import (
    ActionEffectExecutionResult,
    ActionEffectPermanentError,
    ActionEffectTransientError,
)
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.action_effect_executor import AllowlistedActionEffectExecutor
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


def _prepare(foundry: FoundryLite, tmp_path: Path, definition: dict[str, object] | None = None) -> int:
    ctx = demo_admin_context()
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("order_id,status\nO-1,PENDING\n", encoding="utf-8")
    foundry.datasets.ensure("clean.async_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("clean.async_orders", str(csv_path), ctx=ctx)
    foundry.ontology.apply_text(yaml.safe_dump(definition or _definition(1), sort_keys=False), ctx=ctx)
    foundry.objects.reindex("Order", ctx=ctx)
    return int(foundry.objects.get("Order", "O-1", ctx=ctx)["objectVersion"])


def _effect_definition(expected_version: int) -> dict[str, object]:
    definition = _definition(expected_version)
    action = cast(list[dict[str, object]], definition["actionTypes"])[0]
    action["apiName"] = "ApproveOrderWithEffects"
    action["effects"] = [
        {
            "effectId": "erp-write",
            "kind": "webhook",
            "phase": "before_commit",
            "targetRef": "connector:erp/orders",
        },
        {
            "effectId": "order-event",
            "kind": "event",
            "phase": "after_commit",
            "targetRef": "topic:order-approved",
            "maxAttempts": 3,
        },
    ]
    return definition


def _register_effect_connector(foundry: FoundryLite) -> None:
    ctx = demo_admin_context()
    foundry.connectors.create_connection(
        connector_name="erp",
        display_name="ERP Action effects",
        base_url="https://example.com",
        auth={"mode": "none"},
        idempotency_key="create-action-effect-erp",
        ctx=ctx,
    )
    foundry.connectors.upsert_resource(
        "erp",
        "orders",
        dataset_ref="raw.action_effect_receipts",
        resource_path="/orders/actions",
        pagination={"strategy": "cursor"},
        schema_columns=["id"],
        primary_key=["id"],
        idempotency_key="upsert-action-effect-orders",
        ctx=ctx,
    )


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


def test_governed_before_and_after_effects_keep_commit_and_receipts_separate(
    foundry: FoundryLite, tmp_path: Path
) -> None:
    ctx = demo_admin_context()
    version = _prepare(foundry, tmp_path, _effect_definition(1))
    _register_effect_connector(foundry)
    adapter = AllowlistedActionEffectExecutor()
    calls: list[str] = []
    function_effect_outputs: list[dict[str, object]] = []

    def delivered(request) -> ActionEffectExecutionResult:
        calls.append(request.effect.effect_id)
        return ActionEffectExecutionResult("delivered", f"remote:{request.effect.effect_id}", {"status": 202}, {})

    adapter.register_target("connector:erp/orders", delivered, allowed_kinds=frozenset({"webhook"}))
    adapter.register_target("topic:order-approved", delivered, allowed_kinds=frozenset({"event"}))
    foundry._services.action_effects.action_effect_executor = adapter

    def function_driver(request) -> dict[str, object]:
        function_effect_outputs.append(dict(request.effect_outputs))
        return {"output": _edit_batch(version), "logicRunId": "effect-aware-function"}

    foundry._services.action.distributed.action_function_executor.register_driver(function_driver)

    run = foundry.actions.start_run(
        "ApproveOrderWithEffects",
        object_type="Order",
        object_id="O-1",
        expected_object_version=version,
        params={},
        idempotency_key="effect-test-one",
        wait_seconds=5,
        ctx=ctx,
    )

    assert run["status"] == "succeeded"
    assert calls == ["erp-write"]
    assert function_effect_outputs == [
        {
            "effectId": "erp-write",
            "receiptId": f"{run['actionRunId']}:effect:erp-write",
            "response": {"status": 202, "networkEvidence": {}},
        }
    ]
    assert [effect["status"] for effect in run["effects"]] == ["succeeded", "pending"]
    assert foundry.objects.get("Order", "O-1", ctx=ctx)["properties"]["status"] == "APPROVED"

    delivered_result = foundry._services.action_effects.deliver_all(worker_id="effect-worker-test")
    refreshed = foundry.actions.get_run(str(run["actionRunId"]), ctx=ctx)
    assert delivered_result["succeeded"] == 1
    assert calls == ["erp-write", "order-event"]
    assert [effect["status"] for effect in refreshed["effects"]] == ["succeeded", "succeeded"]


def test_ambiguous_before_effect_never_commits_or_replays_provider_call(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    version = _prepare(foundry, tmp_path, _effect_definition(1))
    _register_effect_connector(foundry)
    adapter = AllowlistedActionEffectExecutor()
    calls = 0

    def ambiguous(_request) -> ActionEffectExecutionResult:
        nonlocal calls
        calls += 1
        return ActionEffectExecutionResult("ambiguous", None, {"timeout": True}, {})

    adapter.register_target("connector:erp/orders", ambiguous, allowed_kinds=frozenset({"webhook"}))
    foundry._services.action_effects.action_effect_executor = adapter

    run = foundry.actions.start_run(
        "ApproveOrderWithEffects",
        object_type="Order",
        object_id="O-1",
        expected_object_version=version,
        params={},
        idempotency_key="ambiguous-test",
        wait_seconds=5,
        ctx=ctx,
    )
    replay = foundry.actions.start_run(
        "ApproveOrderWithEffects",
        object_type="Order",
        object_id="O-1",
        expected_object_version=version,
        params={},
        idempotency_key="ambiguous-test",
        wait_seconds=0,
        ctx=ctx,
    )

    assert run["status"] == "outcome_unknown"
    assert replay["actionRunId"] == run["actionRunId"]
    assert calls == 1
    assert run["effects"][0]["status"] == "outcome_unknown"
    assert foundry.objects.get("Order", "O-1", ctx=ctx)["properties"]["status"] == "PENDING"


def test_known_before_action_effect_failure_fails_without_commit_or_replay(
    foundry: FoundryLite, tmp_path: Path
) -> None:
    ctx = demo_admin_context()
    version = _prepare(foundry, tmp_path, _effect_definition(1))
    _register_effect_connector(foundry)
    adapter = AllowlistedActionEffectExecutor()
    calls = 0

    def rejected(_request) -> ActionEffectExecutionResult:
        nonlocal calls
        calls += 1
        raise ActionEffectPermanentError("provider rejected the command")

    adapter.register_target("connector:erp/orders", rejected, allowed_kinds=frozenset({"webhook"}))
    foundry._services.action_effects.action_effect_executor = adapter
    run = foundry.actions.start_run(
        "ApproveOrderWithEffects",
        object_type="Order",
        object_id="O-1",
        expected_object_version=version,
        params={},
        idempotency_key="approve-rejected-effect-1",
        wait_seconds=5,
        ctx=ctx,
    )
    replay = foundry.actions.start_run(
        "ApproveOrderWithEffects",
        object_type="Order",
        object_id="O-1",
        expected_object_version=version,
        params={},
        idempotency_key="approve-rejected-effect-1",
        wait_seconds=0,
        ctx=ctx,
    )

    assert run["status"] == "failed"
    assert replay["actionRunId"] == run["actionRunId"]
    assert calls == 1
    assert run["effects"][0]["status"] == "dead_letter"
    assert foundry.objects.get("Order", "O-1", ctx=ctx)["properties"]["status"] == "PENDING"


def test_after_action_effect_retries_only_a_typed_transient_failure(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    version = _prepare(foundry, tmp_path, _effect_definition(1))
    _register_effect_connector(foundry)
    adapter = AllowlistedActionEffectExecutor()
    after_calls = 0

    def delivered(request) -> ActionEffectExecutionResult:
        nonlocal after_calls
        if request.effect.effect_id == "order-event":
            after_calls += 1
            if after_calls == 1:
                raise ActionEffectTransientError("provider requested a safe retry")
        return ActionEffectExecutionResult("delivered", None, {"status": 202}, {})

    adapter.register_target("connector:erp/orders", delivered, allowed_kinds=frozenset({"webhook"}))
    adapter.register_target("topic:order-approved", delivered, allowed_kinds=frozenset({"event"}))
    foundry._services.action_effects.action_effect_executor = adapter
    run = foundry.actions.start_run(
        "ApproveOrderWithEffects",
        object_type="Order",
        object_id="O-1",
        expected_object_version=version,
        params={},
        idempotency_key="approve-effect-retry-1",
        wait_seconds=5,
        ctx=ctx,
    )

    first = foundry._services.action_effects.deliver_all(worker_id="effect-worker-first")
    with foundry.engine.begin() as transaction:
        transaction.execute(
            update(db.action_effect_receipts)
            .where(db.action_effect_receipts.c.action_run_id == run["actionRunId"])
            .values(retry_at="2000-01-01T00:00:00Z")
        )
    second = foundry._services.action_effects.deliver_all(worker_id="effect-worker-second")
    refreshed = foundry.actions.get_run(str(run["actionRunId"]), ctx=ctx)

    assert first["retry_wait"] == 1
    assert second["succeeded"] == 1
    assert after_calls == 2
    assert refreshed["effects"][1]["attemptCount"] == 2
    assert refreshed["effects"][1]["fencingToken"] == 2


def test_after_action_effect_permanent_failure_moves_directly_to_dlq(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    version = _prepare(foundry, tmp_path, _effect_definition(1))
    _register_effect_connector(foundry)
    adapter = AllowlistedActionEffectExecutor()

    def delivered(request) -> ActionEffectExecutionResult:
        if request.effect.effect_id == "order-event":
            raise ActionEffectPermanentError("provider rejected the registered command")
        return ActionEffectExecutionResult("delivered", None, {"status": 202}, {})

    adapter.register_target("connector:erp/orders", delivered, allowed_kinds=frozenset({"webhook"}))
    adapter.register_target("topic:order-approved", delivered, allowed_kinds=frozenset({"event"}))
    foundry._services.action_effects.action_effect_executor = adapter
    run = foundry.actions.start_run(
        "ApproveOrderWithEffects",
        object_type="Order",
        object_id="O-1",
        expected_object_version=version,
        params={},
        idempotency_key="dlq-test-one",
        wait_seconds=5,
        ctx=ctx,
    )

    delivered_result = foundry._services.action_effects.deliver_all(worker_id="effect-worker-dlq")
    refreshed = foundry.actions.get_run(str(run["actionRunId"]), ctx=ctx)

    assert delivered_result["dead_letter"] == 1
    assert refreshed["status"] == "succeeded"
    assert refreshed["effects"][1]["status"] == "dead_letter"
    assert foundry.objects.get("Order", "O-1", ctx=ctx)["properties"]["status"] == "APPROVED"
