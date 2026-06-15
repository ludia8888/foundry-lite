from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import NotFound, ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters import DuckDBComputeAdapter
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite_api import main as api_main
from foundry_lite_api.main import _header_or_request, app, healthz
from foundry_lite_cli.main import _dispatch, _fresh_supply_chain_demo, _json_arg, _params, _storage_root_for_args, main
from sqlalchemy import create_engine, insert, select

from tests.conftest import prepare_indexed_demo


def test_api_healthz_returns_ok() -> None:
    assert healthz() == {"status": "ok"}


def test_api_without_role_headers_cannot_apply_action() -> None:
    response = TestClient(app).post(
        "/api/actions/ApproveOrder/apply",
        headers={"Idempotency-Key": "anonymous-denied"},
        json={
            "target": {"objectType": "Order", "objectId": "O-1001"},
            "expectedObjectVersion": 1,
            "params": {"reason": "Inventory confirmed"},
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "PERMISSION_DENIED"


def test_api_action_target_shape_is_validated_before_core(monkeypatch) -> None:
    class FailingCore:
        def apply_action(self, *_args, **_kwargs):
            raise AssertionError("invalid request should not reach core")

    monkeypatch.setattr(api_main, "core", FailingCore())
    response = TestClient(app).post(
        "/api/actions/ApproveOrder/apply",
        headers={"Idempotency-Key": "invalid-target-shape"},
        json={
            "target": {"objectType": "Order"},
            "expectedObjectVersion": 1,
            "params": {"reason": "Inventory confirmed"},
        },
    )

    assert response.status_code == 422
    assert response.headers["X-Request-ID"]


def test_action_expected_object_version_required(monkeypatch) -> None:
    class FailingCore:
        def apply_action(self, *_args, **_kwargs):
            raise AssertionError("invalid request should not reach core")

    monkeypatch.setattr(api_main, "core", FailingCore())
    response = TestClient(app).post(
        "/api/actions/ApproveOrder/apply",
        headers={"Idempotency-Key": "missing-expected-version"},
        json={
            "target": {"objectType": "Order", "objectId": "O-1001"},
            "params": {"reason": "Inventory confirmed"},
        },
    )

    assert response.status_code == 422
    assert response.headers["X-Request-ID"]


def test_cli_demo_seed_smoke(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_HOME", str(tmp_path / "cli"))
    main(["demo", "seed"])
    assert os.path.exists("examples/supply-chain-demo/README.md")


@pytest.mark.integration_scenario("closed_loop_repeatability")
def test_cli_supply_chain_demo_repeats_with_parseable_json_output(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FOUNDRY_LITE_HOME", raising=False)

    main(["demo", "run-supply-chain"])
    first = json.loads(capsys.readouterr().out)

    main(["demo", "run-supply-chain"])
    second = json.loads(capsys.readouterr().out)

    assert first["action"]["status"] == "succeeded"
    assert second["action"]["status"] == "succeeded"
    assert second["customer"]["properties"]["approvedOrderCount"] == 2
    assert (tmp_path / ".foundry-lite-demo" / "foundry-lite.db").exists()


def test_api_object_set_create_and_query(core, monkeypatch) -> None:
    ctx = prepare_indexed_demo(core)
    monkeypatch.setattr(api_main, "core", core)

    response = TestClient(app).post(
        "/api/object-sets",
        headers={"X-User-ID": ctx.actor_user_id, "X-Roles": ",".join(ctx.roles)},
        json={
            "name": "Pending Orders",
            "objectType": "Order",
            "setType": "dynamic",
            "filter": {"property": "status", "op": "eq", "value": "PENDING"},
        },
    )

    assert response.status_code == 200
    assert response.json()["objectIds"] == ["O-1001"]

    listing = TestClient(app).get(
        "/api/object-sets",
        headers={"X-User-ID": ctx.actor_user_id, "X-Roles": ",".join(ctx.roles)},
        params={"objectType": "Order"},
    )
    assert listing.status_code == 200
    assert listing.json()["items"][0]["name"] == "Pending Orders"

    set_id = response.json()["id"]
    fetched = TestClient(app).get(
        f"/api/object-sets/{set_id}",
        headers={"X-User-ID": ctx.actor_user_id, "X-Roles": ",".join(ctx.roles)},
    )
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Pending Orders"


def test_api_webhook_ingest_verifies_signature_and_appends_dataset(core, monkeypatch) -> None:
    ctx = demo_admin_context()
    secret = "local-webhook-secret"
    body = b'{"order_id":"O-9001","status":"PENDING"}'
    timestamp = _webhook_timestamp()
    headers = {
        "Content-Type": "application/json",
        "X-Foundry-Lite-Signature": _webhook_signature(body, secret, timestamp),
        "X-Foundry-Lite-Timestamp": timestamp,
        "X-Foundry-Lite-Event-ID": "evt-order-9001",
        "X-User-ID": ctx.actor_user_id,
        "X-Roles": ",".join(ctx.roles),
    }
    core.datasets.ensure("raw.webhook_orders", ctx=ctx, primary_key=["event_id"])
    monkeypatch.setattr(api_main, "core", core)
    monkeypatch.setenv(api_main.WEBHOOK_SIGNING_KEY_ENV, secret)
    client = TestClient(app)

    response = client.post(
        "/api/connectors/webhooks/mock_saas/orders",
        params={"datasetRef": "raw.webhook_orders"},
        headers=headers,
        content=body,
    )
    duplicate = client.post(
        "/api/connectors/webhooks/mock_saas/orders",
        params={"datasetRef": "raw.webhook_orders"},
        headers=headers,
        content=body,
    )
    denied = client.post(
        "/api/connectors/webhooks/mock_saas/orders",
        params={"datasetRef": "raw.webhook_orders"},
        headers={**headers, "X-Foundry-Lite-Signature": "sha256=bad"},
        content=body,
    )
    rejected_shape_body = b"[]"
    rejected_shape = client.post(
        "/api/connectors/webhooks/mock_saas/orders",
        params={"datasetRef": "raw.webhook_orders"},
        headers={**headers, "X-Foundry-Lite-Signature": _webhook_signature(rejected_shape_body, secret, timestamp)},
        content=rejected_shape_body,
    )

    preview = core.datasets.preview("raw.webhook_orders", ctx=ctx)
    transactions = _dataset_transactions(core.engine)
    assert response.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json()["version_id"] == response.json()["version_id"]
    assert response.json()["row_count"] == 1
    assert preview[0]["event_id"] == "evt-order-9001"
    assert preview[0]["connector"] == "mock_saas"
    assert '"order_id":"O-9001"' in preview[0]["payload_json"]
    assert transactions[0]["tx_type"] == "APPEND"
    assert len([tx for tx in transactions if tx["tx_type"] == "APPEND"]) == 1
    assert denied.status_code == 403
    assert rejected_shape.status_code == 422
    deny_events = core.operations.query_runs(ctx=ctx, run_type="audit", status="deny")["auditEvents"]
    assert deny_events[0]["action"] == "webhook:ingest"


def test_webhook_same_event_id_different_payload_is_deduped(core, monkeypatch) -> None:
    ctx = demo_admin_context()
    secret = "local-webhook-secret"
    event_id = "evt-order-volatile"
    first_body = b'{"order_id":"O-9002","status":"PENDING","timestamp":"2026-06-15T01:00:00Z"}'
    duplicate_body = b'{"order_id":"O-9002","status":"PENDING","timestamp":"2026-06-15T01:00:05Z"}'
    changed_body = b'{"order_id":"O-9002","status":"SHIPPED","timestamp":"2026-06-15T01:00:06Z"}'
    core.datasets.ensure("raw.webhook_dedupe_orders", ctx=ctx, primary_key=["event_id"])
    monkeypatch.setattr(api_main, "core", core)
    monkeypatch.setenv(api_main.WEBHOOK_SIGNING_KEY_ENV, secret)
    client = TestClient(app)

    def headers(body: bytes) -> dict[str, str]:
        timestamp = _webhook_timestamp()
        return {
            "Content-Type": "application/json",
            "X-Foundry-Lite-Signature": _webhook_signature(body, secret, timestamp),
            "X-Foundry-Lite-Timestamp": timestamp,
            "X-Foundry-Lite-Event-ID": event_id,
            "X-User-ID": ctx.actor_user_id,
            "X-Roles": ",".join(ctx.roles),
        }

    first = client.post(
        "/api/connectors/webhooks/mock_saas/orders",
        params={"datasetRef": "raw.webhook_dedupe_orders"},
        headers=headers(first_body),
        content=first_body,
    )
    duplicate = client.post(
        "/api/connectors/webhooks/mock_saas/orders",
        params={"datasetRef": "raw.webhook_dedupe_orders"},
        headers=headers(duplicate_body),
        content=duplicate_body,
    )
    changed = client.post(
        "/api/connectors/webhooks/mock_saas/orders",
        params={"datasetRef": "raw.webhook_dedupe_orders"},
        headers=headers(changed_body),
        content=changed_body,
    )

    transactions = _dataset_transactions(core.engine)
    append_transactions = [tx for tx in transactions if tx["tx_type"] == "APPEND"]
    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json()["version_id"] == first.json()["version_id"]
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "CONFLICT"
    assert len(append_transactions) == 1


def test_webhook_signature_replay_and_clock_skew_policy(core, monkeypatch) -> None:
    ctx = demo_admin_context()
    secret = "local-webhook-secret"
    body = b'{"order_id":"O-9004","status":"PENDING"}'
    stale_timestamp = "2000-01-01T00:00:00+00:00"
    headers = {
        "Content-Type": "application/json",
        "X-Foundry-Lite-Signature": _webhook_signature(body, secret, stale_timestamp),
        "X-Foundry-Lite-Timestamp": stale_timestamp,
        "X-Foundry-Lite-Event-ID": "evt-order-stale-signature",
        "X-User-ID": ctx.actor_user_id,
        "X-Roles": ",".join(ctx.roles),
    }
    core.datasets.ensure("raw.webhook_replay_orders", ctx=ctx, primary_key=["event_id"])
    monkeypatch.setattr(api_main, "core", core)
    monkeypatch.setenv(api_main.WEBHOOK_SIGNING_KEY_ENV, secret)
    client = TestClient(app)

    replay = client.post(
        "/api/connectors/webhooks/mock_saas/orders",
        params={"datasetRef": "raw.webhook_replay_orders"},
        headers=headers,
        content=body,
    )

    deny_events = [
        event
        for event in core.operations.list_runs(ctx=ctx)["auditEvents"]
        if event["event_type"] == "permission.denied" and event["resource_id"] == "mock_saas:orders"
    ]
    assert replay.status_code == 403
    assert replay.json()["detail"]["code"] == "PERMISSION_DENIED"
    assert core.datasets.list_versions("raw.webhook_replay_orders", ctx=ctx) == []
    assert deny_events


def test_webhook_ack_not_sent_before_append_commit_or_has_replay_strategy(tmp_path, monkeypatch) -> None:
    ctx = demo_admin_context()
    secret = "local-webhook-secret"
    body = b'{"order_id":"O-9003","status":"PENDING"}'
    timestamp = _webhook_timestamp()
    headers = {
        "Content-Type": "application/json",
        "X-Foundry-Lite-Signature": _webhook_signature(body, secret, timestamp),
        "X-Foundry-Lite-Timestamp": timestamp,
        "X-Foundry-Lite-Event-ID": "evt-order-commit-fail",
        "X-User-ID": ctx.actor_user_id,
        "X-Roles": ",".join(ctx.roles),
    }

    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    failing_core = FoundryLite(
        dependencies=replace(dependencies, compute_adapter=_RowsToParquetFailingComputeAdapter())
    )
    failing_core.datasets.ensure("raw.webhook_commit_fail_orders", ctx=ctx, primary_key=["event_id"])
    monkeypatch.setattr(api_main, "core", failing_core)
    monkeypatch.setenv(api_main.WEBHOOK_SIGNING_KEY_ENV, secret)

    response = TestClient(app).post(
        "/api/connectors/webhooks/mock_saas/orders",
        params={"datasetRef": "raw.webhook_commit_fail_orders"},
        headers=headers,
        content=body,
    )

    transactions = _dataset_transactions(failing_core.engine)
    append_transactions = [tx for tx in transactions if tx["tx_type"] == "APPEND"]
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_FAILED"
    assert not [tx for tx in append_transactions if tx["status"] == "COMMITTED"]
    assert [tx for tx in append_transactions if tx["status"] == "ABORTED"]


def test_api_operations_runs_cursor_pages_action_runs(core, monkeypatch) -> None:
    ctx = prepare_indexed_demo(core)
    monkeypatch.setattr(api_main, "core", core)
    client = TestClient(app)
    headers = {"X-User-ID": ctx.actor_user_id, "X-Roles": ",".join(ctx.roles)}

    first_order = core.objects.get("Order", "O-1001", ctx=ctx)
    second_order = core.objects.get("Order", "O-1002", ctx=ctx)
    core.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=first_order["objectVersion"],
        params={"reason": "Ops page one"},
        idempotency_key="api-operations-page-one",
        ctx=ctx,
    )
    core.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1002",
        expected_object_version=second_order["objectVersion"],
        params={"reason": "Ops page two"},
        idempotency_key="api-operations-page-two",
        ctx=ctx,
    )

    first_page = client.get(
        "/api/operations/runs",
        headers=headers,
        params={"runType": "action", "status": "succeeded", "limit": 1},
    )
    assert first_page.status_code == 200
    first_payload = first_page.json()
    assert len(first_payload["actionRuns"]) == 1
    assert isinstance(first_payload["nextCursor"], str)

    second_page = client.get(
        "/api/operations/runs",
        headers=headers,
        params={
            "runType": "action",
            "status": "succeeded",
            "limit": 1,
            "cursor": first_payload["nextCursor"],
        },
    )
    second_payload = second_page.json()
    assert second_page.status_code == 200
    assert len(second_payload["actionRuns"]) == 1
    assert second_payload["actionRuns"][0]["id"] != first_payload["actionRuns"][0]["id"]

    bad_cursor = client.get(
        "/api/operations/runs",
        headers=headers,
        params={"runType": "action", "cursor": "orc1.not-valid-base64"},
    )
    assert bad_cursor.status_code == 400


def test_api_security_roles_mask_and_audit_denials(core, monkeypatch) -> None:
    ctx = prepare_indexed_demo(core)
    monkeypatch.setattr(api_main, "core", core)
    client = TestClient(app)
    viewer_headers = {"X-Tenant-ID": ctx.tenant_id, "X-User-ID": "viewer-api", "X-Roles": "viewer"}
    finance_headers = {"X-Tenant-ID": ctx.tenant_id, "X-User-ID": "finance-api", "X-Roles": "finance"}
    ops_headers = {"X-Tenant-ID": ctx.tenant_id, "X-User-ID": "ops-api", "X-Roles": "ops_manager"}
    other_tenant_headers = {
        "X-Tenant-ID": "tenant-other",
        "X-User-ID": "other-api",
        "X-Roles": "admin,data_engineer,ops_manager,finance",
    }

    preview = client.get("/api/datasets/clean/orders/preview", headers=viewer_headers)
    assert preview.status_code == 200
    assert preview.json()[0]["order_id"] == "O-1001"

    viewer_order = client.get("/api/objects/Order/O-1001", headers=viewer_headers)
    finance_order = client.get("/api/objects/Order/O-1001", headers=finance_headers)
    assert viewer_order.status_code == 200
    assert finance_order.status_code == 200
    assert viewer_order.json()["properties"]["margin"] == "***MASKED***"
    assert finance_order.json()["properties"]["margin"] != "***MASKED***"

    other_tenant_order = client.get("/api/objects/Order/O-1001", headers=other_tenant_headers)
    assert other_tenant_order.status_code == 404

    denied = client.post(
        "/api/actions/ApproveOrder/apply",
        headers={**viewer_headers, "Idempotency-Key": "api-viewer-denied"},
        json={
            "target": {"objectType": "Order", "objectId": "O-1001"},
            "expectedObjectVersion": viewer_order.json()["objectVersion"],
            "params": {"reason": "Inventory confirmed"},
        },
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "PERMISSION_DENIED"
    assert "request_id" in denied.json()["detail"]

    runs = client.get("/api/operations/runs?runType=audit", headers=ops_headers)
    assert any(row["decision"] == "deny" for row in runs.json()["auditEvents"])


def test_api_dataset_object_action_and_metrics_smoke(core, monkeypatch) -> None:
    ctx = prepare_indexed_demo(core)
    monkeypatch.setattr(api_main, "core", core)
    client = TestClient(app)
    headers = {
        "X-Tenant-ID": ctx.tenant_id,
        "X-User-ID": ctx.actor_user_id,
        "X-Roles": ",".join(ctx.roles),
    }

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "foundry_lite_http_requests_total" in metrics.text

    preview = client.get("/api/datasets/clean/orders/preview", headers=headers)
    assert preview.status_code == 200
    assert preview.json()[0]["order_id"] == "O-1001"

    order = client.get("/api/objects/Order/O-1001", headers=headers, params={"explain": "true"})
    assert order.status_code == 200
    order_payload = order.json()
    assert order_payload["objectId"] == "O-1001"
    source_run = _source_run_link(order_payload["explain"]["sourceRunChain"], run_type="index")
    assert source_run["relation"] == "indexed_from"
    assert source_run["operationPath"] == f"/api/operations/runs/index/{source_run['runId']}"

    source_run_detail = client.get(source_run["operationPath"], headers=headers)
    assert source_run_detail.status_code == 200
    assert source_run_detail.json()["runType"] == "index"
    assert source_run_detail.json()["runId"] == source_run["runId"]

    query = client.post(
        "/api/objects/Order/query",
        headers=headers,
        json={"filter": {"property": "status", "op": "eq", "value": "PENDING"}, "limit": 5},
    )
    assert query.status_code == 200
    assert query.json()["items"][0]["objectId"] == "O-1001"

    action = client.post(
        "/api/actions/ApproveOrder/apply",
        headers={**headers, "Idempotency-Key": "api-approve-o-1001"},
        json={
            "target": {"objectType": "Order", "objectId": "O-1001"},
            "expectedObjectVersion": order_payload["objectVersion"],
            "params": {"reason": "Inventory confirmed"},
        },
    )
    assert action.status_code == 200
    assert action.json()["status"] == "succeeded"

    operation_runs = client.get("/api/operations/runs", headers=headers)
    assert operation_runs.status_code == 200
    runs = operation_runs.json()
    assert "deadLetterEvents" in runs
    assert any(row["target_object_id"] == "O-1001" for row in runs["actionRuns"])

    filtered_runs = client.get(
        "/api/operations/runs",
        headers=headers,
        params={"runType": "action", "status": "succeeded"},
    )
    assert filtered_runs.status_code == 200
    filtered = filtered_runs.json()
    assert filtered["transformRuns"] == []
    action_run = next(row for row in filtered["actionRuns"] if row["target_object_id"] == "O-1001")

    run_detail = client.get(f"/api/operations/runs/action/{action_run['id']}", headers=headers)
    assert run_detail.status_code == 200
    detail = run_detail.json()
    assert detail["runType"] == "action"
    assert detail["runId"] == action_run["id"]
    assert detail["status"] == "succeeded"
    assert detail["correlationId"] == action_run["id"]
    assert detail["references"]["target_object_id"] == "O-1001"
    assert any(event["event_type"] == "action.run.committed" for event in detail["relatedOutboxEvents"])
    assert any(event["event_type"] == "action.run.committed" for event in detail["relatedAuditEvents"])
    assert any(edit["action_run_id"] == action_run["id"] for edit in detail["relatedObjectEdits"])

    index_replay = client.post("/api/operations/index/Order/replay", headers=headers)
    assert index_replay.status_code == 200
    replay = index_replay.json()
    assert replay["object_type"] == "Order"
    assert replay["objects_upserted"] >= 1
    index_detail = client.get(f"/api/operations/runs/index/{replay['index_run_id']}", headers=headers)
    assert index_detail.status_code == 200
    replay_detail = index_detail.json()
    assert replay_detail["runType"] == "index"
    assert replay_detail["runId"] == replay["index_run_id"]
    assert replay_detail["status"] == "succeeded"

    source_ref = _seed_failed_index_run(core.engine, tenant_id=ctx.tenant_id, run_id="index_api_failed")
    failed_run_replay = client.post("/api/operations/runs/index/index_api_failed/replay", headers=headers)
    assert failed_run_replay.status_code == 200
    failed_replay = failed_run_replay.json()
    assert failed_replay["object_type"] == "Order"
    replay_runs = client.get("/api/operations/runs?runType=index", headers=headers).json()
    replay_row = _runtime_row(replay_runs["indexRuns"], failed_replay["index_run_id"])
    assert replay_row["trigger_type"] == "failed_run_replay"
    assert replay_row["source_ref"] == {**source_ref, "replay_of_run_id": "index_api_failed"}

    input_versions = _seed_failed_transform_run(core.engine, tenant_id=ctx.tenant_id, run_id="transform_api_failed")
    failed_transform_runs = client.get(
        "/api/operations/runs",
        headers=headers,
        params={
            "runType": "transform",
            "status": "FAILED",
            "since": "2026-06-10T00:03:30Z",
            "until": "2026-06-10T00:04:30Z",
        },
    )
    assert failed_transform_runs.status_code == 200
    failed_transform_listing = failed_transform_runs.json()
    assert failed_transform_listing["actionRuns"] == []
    assert _runtime_row(failed_transform_listing["transformRuns"], "transform_api_failed")["status"] == "FAILED"

    failed_transform_detail = client.get("/api/operations/runs/transform/transform_api_failed", headers=headers)
    assert failed_transform_detail.status_code == 200
    failure_detail = failed_transform_detail.json()
    assert failure_detail["errorMessage"] == "transform failed"
    assert failure_detail["references"]["input_versions"] == input_versions
    assert failure_detail["investigation"]["summary"].endswith("transform failed")
    assert (
        "POST /api/operations/runs/transform/transform_api_failed/retry"
        in failure_detail["investigation"]["suggestedActions"]
    )

    transform_retry = client.post("/api/operations/runs/transform/transform_api_failed/retry", headers=headers)
    assert transform_retry.status_code == 200
    transform_retry_payload = transform_retry.json()
    assert transform_retry_payload["original_run_id"] == "transform_api_failed"
    transform_runs = client.get("/api/operations/runs?runType=transform", headers=headers).json()
    retry_transform_row = _runtime_row(transform_runs["transformRuns"], transform_retry_payload["transform_run_id"])
    assert retry_transform_row["status"] == "SUCCESS"
    assert retry_transform_row["input_versions"] == input_versions
    transform_detail = client.get(
        f"/api/operations/runs/transform/{transform_retry_payload['transform_run_id']}",
        headers=headers,
    )
    assert transform_detail.status_code == 200
    assert any(
        row["event_type"] == "transform.run.retry_requested" for row in transform_detail.json()["relatedAuditEvents"]
    )

    missing = client.get("/api/objects/Order/NOPE", headers=headers)
    assert missing.status_code == 404
    assert "request_id" in missing.json()["detail"]

    missing_preview = client.get("/api/datasets/clean/missing/preview", headers=headers)
    assert missing_preview.status_code == 404

    missing_set = client.get("/api/object-sets/missing", headers=headers)
    assert missing_set.status_code == 404


def test_api_operations_retry_dead_letter_event(core, monkeypatch) -> None:
    ctx = prepare_indexed_demo(core)
    _approve_order_for_materialization(core, ctx, idempotency_key="api-dlq-materialization")
    _seed_dead_letter_event(core.engine, tenant_id=ctx.tenant_id, outbox_id="outbox_api_retry", dlq_id="dlq_api")
    monkeypatch.setattr(api_main, "core", core)
    client = TestClient(app)
    headers = {"X-Tenant-ID": ctx.tenant_id, "X-User-ID": ctx.actor_user_id, "X-Roles": "ops_manager"}

    retry = client.post("/api/operations/dead-letter-events/dlq_api/retry", headers=headers)
    runs = client.get("/api/operations/runs", headers=headers).json()
    retry_payload = retry.json()
    materialization_result = retry_payload["materializationResult"]

    assert retry.status_code == 200
    assert retry_payload["outboxEventId"] == "outbox_api_retry"
    assert retry_payload["payload"] == {"materialization": "action_log"}
    assert retry_payload["status"] == "pending"
    assert materialization_result["apiName"] == "action_log"
    assert materialization_result["datasetRef"] == "ops.action_log"
    assert materialization_result["rowCount"] > 0
    assert [row["id"] for row in runs["deadLetterEvents"]] == []
    assert _runtime_row(runs["outboxEvents"], "outbox_api_retry")["attempts"] == 0
    assert _runtime_row(runs["outboxEvents"], "outbox_api_retry")["status"] == "pending"
    materialization_run = next(
        row
        for row in runs["materializationRuns"]
        if row["target_dataset_version_id"] == materialization_result["versionId"]
    )
    assert materialization_run["status"] == "succeeded"
    assert any(row["event_type"] == "dead_letter_event.retry_requested" for row in runs["auditEvents"])


def test_api_operations_retry_dead_letter_event_keeps_dlq_when_reprocess_fails(core, monkeypatch) -> None:
    ctx = prepare_indexed_demo(core)
    # The dead-letter event names a materialization that does not exist, so the
    # reprocess step fails before the DLQ row is consumed. A failed reprocess
    # must keep the dead-letter event and leave the source outbox event failed
    # rather than silently reporting success (failure must not look like success).
    _seed_dead_letter_event(
        core.engine,
        tenant_id=ctx.tenant_id,
        outbox_id="outbox_api_failed",
        dlq_id="dlq_api_failed",
        materialization="ops_missing_materialization",
    )
    monkeypatch.setattr(api_main, "core", core)
    client = TestClient(app)
    headers = {"X-Tenant-ID": ctx.tenant_id, "X-User-ID": ctx.actor_user_id, "X-Roles": "ops_manager"}

    retry = client.post("/api/operations/dead-letter-events/dlq_api_failed/retry", headers=headers)
    runs = client.get("/api/operations/runs", headers=headers).json()

    assert retry.status_code == 404
    assert retry.json()["detail"]["code"] == "NOT_FOUND"
    assert _runtime_row(runs["deadLetterEvents"], "dlq_api_failed")["source_event_id"] == "outbox_api_failed"
    outbox = _runtime_row(runs["outboxEvents"], "outbox_api_failed")
    assert outbox["status"] == "failed"
    assert outbox["attempts"] == 3


def test_api_operation_errors_preserve_request_id(monkeypatch) -> None:
    class _Operations:
        def query_runs(self, **_kwargs):
            raise ValidationFailed("invalid run filter")

        def run_detail(self, *_args, **_kwargs):
            raise NotFound("operations run not found")

        def retry_dead_letter_event(self, *_args, **_kwargs):
            raise ValidationFailed("dead-letter event is not retryable")

    class _Objects:
        def reindex(self, *_args, **_kwargs):
            raise NotFound("object type not found")

        def replay_index_run(self, *_args, **_kwargs):
            raise ValidationFailed("index run is not failed")

    class _Transforms:
        def retry_run(self, *_args, **_kwargs):
            raise ValidationFailed("transform run is not failed")

    class FailingCore:
        operations = _Operations()
        objects = _Objects()
        transforms = _Transforms()

    monkeypatch.setattr(api_main, "core", FailingCore())
    client = TestClient(app)

    listing = client.get("/api/operations/runs", params={"runType": "missing"})
    assert listing.status_code == 400
    assert "request_id" in listing.json()["detail"]

    detail = client.get("/api/operations/runs/action/missing")
    assert detail.status_code == 404
    assert "request_id" in detail.json()["detail"]

    retry = client.post("/api/operations/dead-letter-events/missing/retry")
    assert retry.status_code == 400
    assert "request_id" in retry.json()["detail"]

    replay = client.post("/api/operations/index/Missing/replay")
    assert replay.status_code == 404
    assert "request_id" in replay.json()["detail"]

    replay_run = client.post("/api/operations/runs/index/not-failed/replay")
    assert replay_run.status_code == 400
    assert "request_id" in replay_run.json()["detail"]

    transform_retry = client.post("/api/operations/runs/transform/not-failed/retry")
    assert transform_retry.status_code == 400
    assert "request_id" in transform_retry.json()["detail"]


def test_api_object_set_errors_preserve_request_id(monkeypatch) -> None:
    class _Objects:
        def query_sets(self, **_kwargs):
            raise NotFound("object type not found")

        def create_set(self, *_args, **_kwargs):
            raise ValidationFailed("invalid object set")

    class FailingCore:
        objects = _Objects()

    monkeypatch.setattr(api_main, "core", FailingCore())
    client = TestClient(app)

    query = client.get("/api/object-sets", params={"objectType": "Missing"})
    assert query.status_code == 404
    assert "request_id" in query.json()["detail"]

    create = client.post(
        "/api/object-sets",
        json={
            "name": "Bad",
            "objectType": "Order",
            "setType": "dynamic",
            "filter": {"property": "missing", "op": "eq", "value": "x"},
        },
    )
    assert create.status_code == 400
    assert "request_id" in create.json()["detail"]


def test_cli_object_set_smoke(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_HOME", str(tmp_path / "cli-object-set"))
    main(["demo", "run-supply-chain"])
    main(
        [
            "object-set",
            "create-dynamic",
            "Pending Orders",
            "Order",
            "--filter-json",
            '{"property":"status","op":"eq","value":"PENDING"}',
        ]
    )
    main(["object-set", "list", "--object-type", "Order"])


def test_cli_dataset_lineage_operations_and_materialize_smoke(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_HOME", str(tmp_path / "cli-flow"))

    main(["demo", "run-supply-chain"])
    demo = json.loads(capsys.readouterr().out)

    commands = [
        ["dataset", "create", "raw.extra_orders", "--primary-key", "order_id"],
        [
            "dataset",
            "upload",
            "raw.extra_orders",
            "examples/supply-chain-demo/data/orders.csv",
        ],
        ["dataset", "versions", "raw.erp_orders"],
        ["dataset", "preview", "clean.orders", "--limit", "1"],
        ["dataset", "inspect", "clean.orders"],
        ["object", "get", "Order", "O-1001", "--explain"],
        ["object", "links", "Order", "O-1001", "OrderCustomer"],
        ["action", "apply", "ApproveOrder", "--object", "Order/O-1002", "--param", "reason=Manual"],
        ["lineage", demo["cleanOrdersVersion"]],
        ["operations", "runs"],
        ["materialize", "run", "action_log"],
        ["object-set", "create-static", "Reviewed Orders", "Order", "--id", "O-1001"],
        ["object-set", "cleanup-expired"],
    ]

    for command in commands:
        main(command)
        assert json.loads(capsys.readouterr().out) is not None

    main(["object", "get", "Order", "O-1001", "--explain"])
    object_detail = json.loads(capsys.readouterr().out)
    source_run = _source_run_link(object_detail["explain"]["sourceRunChain"], run_type="index")
    main(["operations", "run", str(source_run["runType"]), str(source_run["runId"])])
    source_run_detail = json.loads(capsys.readouterr().out)
    assert source_run_detail["runType"] == "index"
    assert source_run_detail["runId"] == source_run["runId"]

    main(["operations", "runs", "--type", "action", "--status", "succeeded"])
    action_runs = json.loads(capsys.readouterr().out)
    assert action_runs["transformRuns"] == []
    action_run_id = next(row["id"] for row in action_runs["actionRuns"] if row["target_object_id"] == "O-1001")

    main(["operations", "runs", "--type", "action", "--status", "succeeded", "--limit", "1"])
    first_action_page = json.loads(capsys.readouterr().out)
    assert isinstance(first_action_page["nextCursor"], str)
    main(
        [
            "operations",
            "runs",
            "--type",
            "action",
            "--status",
            "succeeded",
            "--limit",
            "1",
            "--cursor",
            first_action_page["nextCursor"],
        ]
    )
    second_action_page = json.loads(capsys.readouterr().out)
    assert second_action_page["actionRuns"][0]["id"] != first_action_page["actionRuns"][0]["id"]

    main(["operations", "run", "action", action_run_id])
    action_detail = json.loads(capsys.readouterr().out)
    assert action_detail["runType"] == "action"
    assert action_detail["runId"] == action_run_id
    assert action_detail["references"]["target_object_id"] == "O-1001"
    assert any(event["event_type"] == "action.run.committed" for event in action_detail["relatedOutboxEvents"])

    main(["index", "replay", "Order"])
    replay = json.loads(capsys.readouterr().out)
    assert replay["object_type"] == "Order"

    main(["operations", "run", "index", replay["index_run_id"]])
    index_detail = json.loads(capsys.readouterr().out)
    assert index_detail["runType"] == "index"
    assert index_detail["runId"] == replay["index_run_id"]

    engine = create_engine(f"sqlite:///{tmp_path / 'cli-flow' / 'foundry-lite.db'}", future=True)
    source_ref = _seed_failed_index_run(engine, tenant_id="tenant-demo", run_id="index_cli_failed")
    main(["index", "replay-run", "index_cli_failed"])
    failed_replay = json.loads(capsys.readouterr().out)
    assert failed_replay["object_type"] == "Order"
    with engine.begin() as conn:
        replay_row = (
            conn.execute(select(db.index_runs).where(db.index_runs.c.id == failed_replay["index_run_id"]))
            .mappings()
            .one()
        )
    assert replay_row["trigger_type"] == "failed_run_replay"
    assert replay_row["source_ref"] == {**source_ref, "replay_of_run_id": "index_cli_failed"}

    input_versions = _seed_failed_transform_run(engine, tenant_id="tenant-demo", run_id="transform_cli_failed")
    main(
        [
            "operations",
            "runs",
            "--type",
            "transform",
            "--status",
            "FAILED",
            "--since",
            "2026-06-10T00:03:30Z",
            "--until",
            "2026-06-10T00:04:30Z",
        ]
    )
    failed_transform_runs = json.loads(capsys.readouterr().out)
    assert failed_transform_runs["actionRuns"] == []
    assert _runtime_row(failed_transform_runs["transformRuns"], "transform_cli_failed")["status"] == "FAILED"

    main(["operations", "run", "transform", "transform_cli_failed"])
    failed_transform_detail = json.loads(capsys.readouterr().out)
    assert failed_transform_detail["errorMessage"] == "transform failed"
    assert failed_transform_detail["references"]["input_versions"] == input_versions
    assert failed_transform_detail["investigation"]["summary"].endswith("transform failed")
    assert "flite transform retry transform_cli_failed" in failed_transform_detail["investigation"]["suggestedActions"]

    main(["transform", "retry", "transform_cli_failed"])
    transform_retry = json.loads(capsys.readouterr().out)
    assert transform_retry["original_run_id"] == "transform_cli_failed"
    with engine.begin() as conn:
        retry_transform_row = (
            conn.execute(select(db.transform_runs).where(db.transform_runs.c.id == transform_retry["transform_run_id"]))
            .mappings()
            .one()
        )
    assert retry_transform_row["status"] == "SUCCESS"
    assert retry_transform_row["input_versions"] == input_versions


def test_cli_outbox_retry_smoke(tmp_path, monkeypatch, capsys) -> None:
    storage_root = tmp_path / "cli-dlq"
    monkeypatch.setenv("FOUNDRY_LITE_HOME", str(storage_root))
    main(["demo", "run-supply-chain"])
    capsys.readouterr()
    engine = create_engine(f"sqlite:///{storage_root / 'foundry-lite.db'}", future=True)
    _seed_dead_letter_event(engine, tenant_id="tenant-demo", outbox_id="outbox_cli_retry", dlq_id="dlq_cli")

    main(["outbox", "retry", "dlq_cli"])
    retry = json.loads(capsys.readouterr().out)
    materialization_result = retry["materializationResult"]

    with engine.begin() as conn:
        outbox = (
            conn.execute(select(db.outbox_events).where(db.outbox_events.c.id == "outbox_cli_retry")).mappings().one()
        )
        dlq_rows = conn.execute(select(db.dead_letter_events).where(db.dead_letter_events.c.id == "dlq_cli")).all()
        materialization_run = (
            conn.execute(
                select(db.materialization_runs).where(
                    db.materialization_runs.c.target_dataset_version_id == materialization_result["versionId"]
                )
            )
            .mappings()
            .one()
        )

    assert retry["outboxEventId"] == "outbox_cli_retry"
    assert retry["payload"] == {"materialization": "action_log"}
    assert materialization_result["apiName"] == "action_log"
    assert materialization_result["datasetRef"] == "ops.action_log"
    assert materialization_result["rowCount"] > 0
    assert outbox["status"] == "pending"
    assert outbox["attempts"] == 0
    assert dlq_rows == []
    assert materialization_run["status"] == "succeeded"


def test_cli_argument_helpers_reject_invalid_values(monkeypatch) -> None:
    assert _params(["reason=ok"]) == {"reason": "ok"}
    with pytest.raises(SystemExit):
        _params(["missing-equals"])
    with pytest.raises(SystemExit):
        _json_arg("[]")

    args = type("Args", (), {"group": "demo", "command": "run-supply-chain", "fresh": True, "reuse_state": True})()
    with pytest.raises(SystemExit):
        _fresh_supply_chain_demo(args)

    args = type("Args", (), {"group": "demo", "command": "run-supply-chain", "fresh": True, "reuse_state": False})()
    assert _fresh_supply_chain_demo(args) is True

    args = type("Args", (), {"group": "demo", "command": "run-supply-chain", "fresh": False, "reuse_state": True})()
    assert _fresh_supply_chain_demo(args) is False

    monkeypatch.setenv("FOUNDRY_LITE_HOME", "/tmp/flite-test")
    args = type("Args", (), {"group": "demo", "command": "run-supply-chain", "fresh": False, "reuse_state": False})()
    assert _fresh_supply_chain_demo(args) is False

    non_demo = type("Args", (), {"group": "dataset", "command": "versions"})()
    assert _fresh_supply_chain_demo(non_demo) is False
    assert _storage_root_for_args(non_demo) is None

    monkeypatch.delenv("FOUNDRY_LITE_HOME", raising=False)
    assert _storage_root_for_args(non_demo) == ".foundry-lite"
    assert _header_or_request("explicit", None, "X-Test") == "explicit"


class _RowsToParquetFailingComputeAdapter(DuckDBComputeAdapter):
    def rows_to_parquet(
        self,
        rows: Sequence[Mapping[str, object]],
        target_path: Path,
        fieldnames: list[str],
    ) -> None:
        del rows, target_path, fieldnames
        raise RuntimeError("webhook append commit failed")


def _approve_order_for_materialization(core, ctx, *, idempotency_key: str) -> None:
    order = core.objects.get("Order", "O-1001", ctx=ctx)
    core.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key=idempotency_key,
        ctx=ctx,
    )


def _webhook_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _webhook_signature(body: bytes, secret: str, timestamp: str) -> str:
    signed_payload = timestamp.encode("utf-8") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _dataset_transactions(engine) -> list[dict[str, object]]:
    with engine.begin() as conn:
        rows = conn.execute(select(db.dataset_transactions))
        return [dict(row) for row in rows.mappings().all()]


def _source_run_link(links: list[dict[str, object]], *, run_type: str) -> dict[str, object]:
    return next(link for link in links if link["runType"] == run_type)


def test_cli_dispatch_rejects_unsupported_command(core) -> None:
    args = type("Args", (), {"group": "unknown", "command": "nope"})()
    with pytest.raises(SystemExit):
        _dispatch(core, demo_admin_context(), args)


def _seed_dead_letter_event(
    engine, *, tenant_id: str, outbox_id: str, dlq_id: str, materialization: str = "action_log"
) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(db.outbox_events).values(
                id=outbox_id,
                tenant_id=tenant_id,
                event_type="materialization.requested",
                aggregate_type="materialization",
                aggregate_id=materialization,
                payload={"materialization": materialization},
                status="failed",
                attempts=3,
                idempotency_key=outbox_id,
                correlation_id=outbox_id,
                created_at="2026-06-10T00:00:00Z",
                published_at="2026-06-10T00:00:01Z",
            )
        )
        conn.execute(
            insert(db.dead_letter_events).values(
                id=dlq_id,
                tenant_id=tenant_id,
                source_event_id=outbox_id,
                event_type="materialization.requested",
                payload={"materialization": materialization},
                error={"message": "publisher failed"},
                failed_at="2026-06-10T00:00:02Z",
                retry_after=None,
            )
        )


def _seed_failed_index_run(engine, *, tenant_id: str, run_id: str) -> dict[str, object]:
    with engine.begin() as conn:
        source = (
            conn.execute(select(db.index_runs).where(db.index_runs.c.object_type_api_name == "Order"))
            .mappings()
            .first()
        )
        assert source is not None
        source_ref = dict(source["source_ref"])
        conn.execute(
            insert(db.index_runs).values(
                id=run_id,
                tenant_id=tenant_id,
                object_type_id=source["object_type_id"],
                object_type_api_name=source["object_type_api_name"],
                trigger_type="reindex",
                source_ref=source_ref,
                status="failed",
                cursor={},
                rows_read=0,
                objects_upserted=0,
                objects_deleted=0,
                links_upserted=0,
                error={"message": "index failed"},
                started_at="2026-06-10T00:03:00Z",
                completed_at="2026-06-10T00:03:01Z",
                created_at="2026-06-10T00:03:00Z",
            )
        )
        return source_ref


def _seed_failed_transform_run(engine, *, tenant_id: str, run_id: str) -> dict[str, str]:
    with engine.begin() as conn:
        source = (
            conn.execute(select(db.transform_runs).where(db.transform_runs.c.status == "SUCCESS")).mappings().first()
        )
        assert source is not None
        input_versions = dict(source["input_versions"])
        conn.execute(
            insert(db.transform_runs).values(
                id=run_id,
                tenant_id=tenant_id,
                transform_id=source["transform_id"],
                status="FAILED",
                input_versions=input_versions,
                output_version_id=None,
                transaction_id="dstx_failed_transform_seed",
                error={"message": "transform failed"},
                created_at="2026-06-10T00:04:00Z",
                completed_at="2026-06-10T00:04:01Z",
            )
        )
        return input_versions


def _runtime_row(rows: list[dict[str, object]], row_id: str) -> dict[str, object]:
    return next(row for row in rows if row["id"] == row_id)
