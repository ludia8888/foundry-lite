"""Public-path evidence for Palantir-style Function-backed Action batching."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import demo_admin_context
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app


def _definition(execution_mode: str) -> dict[str, object]:
    parameters = [
        {"apiName": "objectId", "type": "string", "required": True},
        {"apiName": "status", "type": "string", "required": True},
    ]
    function_inputs = parameters
    function_ref: dict[str, object] = {
        "apiName": "batchOrderEdits",
        "version": "1.0.0",
        "executionMode": execution_mode,
    }
    if execution_mode == "batched":
        function_inputs = [
            {
                "apiName": "requests",
                "type": "array",
                "itemType": "struct",
                "required": True,
                "fields": parameters,
            }
        ]
        function_ref["batchInputName"] = "requests"
        function_ref["maxBatchSize"] = 10_000
    return {
        "objectTypes": [
            {
                "apiName": "Order",
                "primaryKey": "orderId",
                "backing": {
                    "dataset": "clean.function_batch_orders",
                    "mode": "snapshot",
                    "primaryKeyColumns": ["order_id"],
                },
                "properties": [
                    {"apiName": "orderId", "column": "order_id", "type": "string", "indexed": True},
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
                "apiName": "batchOrderEdits",
                "version": "1.0.0",
                "runtime": "logic_dag",
                "inputs": function_inputs,
                "output": {"type": "ontology_edit_batch"},
                "permissions": {"allowedRoles": ["admin"]},
                "definition": {
                    "tools": [],
                    "blocks": [
                        {"blockId": "input", "kind": "Input"},
                        {
                            "blockId": "output",
                            "kind": "Output",
                            "dependsOn": ["input"],
                            "inputs": {"fromBlock": "input"},
                        },
                    ],
                },
            }
        ],
        "actionTypes": [
            {
                "apiName": "UpdateOrdersFromFunction",
                "contractVersion": 3,
                "target": "Order",
                "parameters": parameters,
                "riskLevel": "high",
                "agentExecutionPolicy": "approval_required",
                "permissions": {"allowedRoles": ["admin"]},
                "function": function_ref,
            }
        ],
    }


def _prepare(foundry: FoundryLite, tmp_path: Path, execution_mode: str) -> dict[str, int]:
    ctx = demo_admin_context()
    csv_path = tmp_path / f"function-batch-{execution_mode}.csv"
    csv_path.write_text("order_id,status\nO-1,PENDING\nO-2,PENDING\n", encoding="utf-8")
    foundry.datasets.ensure("clean.function_batch_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("clean.function_batch_orders", str(csv_path), ctx=ctx)
    foundry.ontology.apply_text(yaml.safe_dump(_definition(execution_mode), sort_keys=False), ctx=ctx)
    foundry.objects.reindex("Order", ctx=ctx)
    return {
        object_id: int(foundry.objects.get("Order", object_id, ctx=ctx)["objectVersion"])
        for object_id in ("O-1", "O-2")
    }


def _items(versions: Mapping[str, int]) -> list[dict[str, object]]:
    return [
        {
            "objectId": object_id,
            "expectedObjectVersion": versions[object_id],
            "params": {"objectId": object_id, "status": "APPROVED"},
        }
        for object_id in ("O-1", "O-2")
    ]


def _edit_batch(inputs: list[Mapping[str, object]], versions: Mapping[str, int]) -> dict[str, object]:
    return {
        "edits": [
            {
                "kind": "modifyObject",
                "objectType": "Order",
                "objectId": str(item["objectId"]),
                "expectedVersion": versions[str(item["objectId"])],
                "patch": {"status": item["status"]},
            }
            for item in inputs
        ],
        "readSetVersions": {f"Order:{object_id}": version for object_id, version in versions.items()},
        "provenance": {"runtime": "test-function"},
    }


def test_per_request_function_batch_executes_sequentially_and_commits_once(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    versions = _prepare(foundry, tmp_path, "per_request")
    calls: list[dict[str, object]] = []

    def driver(request) -> dict[str, object]:
        inputs = dict(request.inputs)
        calls.append(inputs)
        object_id = str(inputs["objectId"])
        return {
            "output": _edit_batch([inputs], {object_id: versions[object_id]}),
            "logicRunId": f"logic:{object_id}",
        }

    foundry._services.action.distributed.action_function_executor.register_driver(driver)
    run = foundry.actions.start_batch_run(
        "UpdateOrdersFromFunction",
        object_type="Order",
        items=_items(versions),
        idempotency_key="function-batch-per-request-1",
        wait_seconds=5,
        ctx=ctx,
    )

    assert run["status"] == "succeeded"
    assert calls == [
        {"objectId": "O-1", "status": "APPROVED"},
        {"objectId": "O-2", "status": "APPROVED"},
    ]
    assert len(run["attempts"]) == 1
    assert run["result"]["plan"]["editCount"] == 2
    assert run["attempts"][0]["output"]["function"]["provenance"]["executionMode"] == "per_request"
    for object_id in versions:
        assert foundry.objects.get("Order", object_id, ctx=ctx)["properties"]["status"] == "APPROVED"


def test_batched_function_invokes_once_with_list_of_structs_and_commits_once(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    versions = _prepare(foundry, tmp_path, "batched")
    calls: list[dict[str, object]] = []

    def driver(request) -> dict[str, object]:
        inputs = dict(request.inputs)
        calls.append(inputs)
        requests = inputs["requests"]
        assert isinstance(requests, list)
        return {
            "output": _edit_batch(requests, versions),
            "logicRunId": "logic:batched-once",
        }

    foundry._services.action.distributed.action_function_executor.register_driver(driver)
    run = foundry.actions.start_batch_run(
        "UpdateOrdersFromFunction",
        object_type="Order",
        items=_items(versions),
        idempotency_key="function-batch-once-1",
        wait_seconds=5,
        ctx=ctx,
    )

    assert run["status"] == "succeeded"
    assert len(calls) == 1
    assert calls[0]["requests"] == [item["params"] for item in _items(versions)]
    assert run["result"]["plan"]["editCount"] == 2
    assert run["attempts"][0]["externalExecutionId"] == "logic:batched-once"


def test_single_action_call_uses_one_item_list_for_batched_function(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = demo_admin_context()
    versions = _prepare(foundry, tmp_path, "batched")
    calls: list[dict[str, object]] = []

    def driver(request) -> dict[str, object]:
        inputs = dict(request.inputs)
        calls.append(inputs)
        requests = inputs["requests"]
        assert isinstance(requests, list)
        return {
            "output": _edit_batch(requests, {"O-1": versions["O-1"]}),
            "logicRunId": "logic:single-as-batch",
        }

    foundry._services.action.distributed.action_function_executor.register_driver(driver)
    run = foundry.actions.start_run(
        "UpdateOrdersFromFunction",
        object_type="Order",
        object_id="O-1",
        expected_object_version=versions["O-1"],
        params={"objectId": "O-1", "status": "APPROVED"},
        idempotency_key="function-single-through-batch-mode-1",
        wait_seconds=5,
        ctx=ctx,
    )

    assert run["status"] == "succeeded"
    assert calls == [{"requests": [{"objectId": "O-1", "status": "APPROVED"}]}]
    assert foundry.objects.get("Order", "O-1", ctx=ctx)["properties"]["status"] == "APPROVED"


def test_function_batch_conflict_rolls_back_every_edit(foundry: FoundryLite, tmp_path: Path) -> None:
    ctx = demo_admin_context()
    versions = _prepare(foundry, tmp_path, "batched")

    def driver(request) -> dict[str, object]:
        requests = request.inputs["requests"]
        assert isinstance(requests, list)
        stale = {**versions, "O-2": versions["O-2"] + 1}
        return {"output": _edit_batch(requests, stale), "logicRunId": "logic:stale-batch"}

    foundry._services.action.distributed.action_function_executor.register_driver(driver)
    run = foundry.actions.start_batch_run(
        "UpdateOrdersFromFunction",
        object_type="Order",
        items=_items(versions),
        idempotency_key="function-batch-conflict-1",
        wait_seconds=5,
        ctx=ctx,
    )

    assert run["status"] == "conflict"
    assert len(run["attempts"]) == 1
    for object_id in versions:
        assert foundry.objects.get("Order", object_id, ctx=ctx)["properties"]["status"] == "PENDING"


def test_batch_runs_http_surface_returns_terminal_snapshot_with_bounded_wait(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    versions = _prepare(foundry, tmp_path, "batched")

    def driver(request) -> dict[str, object]:
        requests = request.inputs["requests"]
        assert isinstance(requests, list)
        return {"output": _edit_batch(requests, versions), "logicRunId": "logic:http-batch"}

    foundry._services.action.distributed.action_function_executor.register_driver(driver)
    api_runtime.foundry = foundry
    response = TestClient(app).post(
        "/api/actions/UpdateOrdersFromFunction/batch-runs?waitSeconds=5",
        headers={
            "X-Tenant-ID": "tenant-demo",
            "X-User-ID": "user-demo-admin",
            "X-Roles": "admin,data_engineer,ops_manager",
            "Idempotency-Key": "function-batch-http-1",
        },
        json={"objectType": "Order", "items": _items(versions)},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["attempts"][0]["externalExecutionId"] == "logic:http-batch"
