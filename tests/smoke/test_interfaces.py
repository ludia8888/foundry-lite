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
from foundry_lite.application.upload_limits import WEBHOOK_BODY_LIMIT_ENV
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import ExternalOutcomeUnknown, NotFound, ValidationFailed
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
            raise AssertionError("invalid request should not reach foundry")

    monkeypatch.setattr(api_main, "foundry", FailingCore())
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
            raise AssertionError("invalid request should not reach foundry")

    monkeypatch.setattr(api_main, "foundry", FailingCore())
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


def test_api_validation_errors_preserve_request_id_without_raw_input(monkeypatch) -> None:
    class FailingCore:
        def apply_action(self, *_args, **_kwargs):
            raise AssertionError("invalid request should not reach foundry")

    monkeypatch.setattr(api_main, "foundry", FailingCore())
    response = TestClient(app).post(
        "/api/actions/ApproveOrder/apply",
        headers={"Idempotency-Key": "invalid-params-shape"},
        json={
            "target": {"objectType": "Order", "objectId": "O-1001"},
            "expectedObjectVersion": 1,
            "params": "raw-secret-token",
        },
    )

    body = response.json()
    assert response.status_code == 422
    assert body["detail"]["code"] == "VALIDATION_FAILED"
    assert body["detail"]["request_id"] == response.headers["X-Request-ID"]
    assert body["detail"]["details"]["validation_errors"][0]["loc"] == ["body", "params"]
    assert "input" not in body["detail"]["details"]["validation_errors"][0]
    assert "raw-secret-token" not in response.text


def test_api_read_endpoint_domain_errors_preserve_request_id(monkeypatch) -> None:
    class FailingDatasets:
        def list_datasets(self, **_kwargs):
            raise ValidationFailed("dataset list failed")

        def inspect(self, *_args, **_kwargs):
            raise ValidationFailed("dataset inspect failed")

    class FailingOntology:
        def catalog(self, **_kwargs):
            raise ValidationFailed("ontology catalog failed")

    class FailingInsights:
        def list(self, **_kwargs):
            raise ValidationFailed("insight list failed")

        def get(self, *_args, **_kwargs):
            raise ValidationFailed("insight get failed")

        def assign(self, *_args, **_kwargs):
            raise ValidationFailed("insight assign failed")

    class FailingOperations:
        def lineage(self, *_args, **_kwargs):
            raise ValidationFailed("lineage failed")

        def observability_report(self, **_kwargs):
            raise ValidationFailed("observability failed")

        def plan_iceberg_maintenance(self, *_args, **_kwargs):
            raise ValidationFailed("maintenance failed")

    class FailingFoundry:
        datasets = FailingDatasets()
        ontology = FailingOntology()
        insights = FailingInsights()
        operations = FailingOperations()

    monkeypatch.setattr(api_main, "foundry", FailingFoundry())
    client = TestClient(app)
    headers = {"X-Tenant-ID": "tenant-demo", "X-User-ID": "ops-user", "X-Roles": "admin,ops_manager"}
    requests = [
        ("get", "/api/datasets", {}, None),
        ("get", "/api/datasets/clean/orders/inspect", {}, None),
        ("get", "/api/ontology/catalog", {}, None),
        ("get", "/api/insights/reviews", {}, None),
        ("get", "/api/insights/reviews/review-1", {}, None),
        ("post", "/api/insights/reviews/review-1/assign", {}, {"assigneeUserId": "ops-reviewer"}),
        ("get", "/api/operations/lineage", {"resourceId": "version-1"}, None),
        ("post", "/api/operations/observability/detect", {}, {"configs": []}),
        ("get", "/api/operations/maintenance/iceberg", {"datasetRef": "clean.orders"}, None),
    ]

    for method, path, params, payload in requests:
        response = client.request(
            method.upper(),
            path,
            headers={**headers, "Idempotency-Key": path},
            params=params,
            json=payload,
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "VALIDATION_FAILED"
        assert response.json()["detail"]["request_id"] == response.headers["X-Request-ID"]


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


def test_api_object_set_create_and_query(foundry, monkeypatch) -> None:
    ctx = prepare_indexed_demo(foundry)
    monkeypatch.setattr(api_main, "foundry", foundry)

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


def test_api_ontology_catalog_returns_active_object_action_and_link_metadata(foundry, monkeypatch) -> None:
    ctx = prepare_indexed_demo(foundry)
    monkeypatch.setattr(api_main, "foundry", foundry)

    response = TestClient(app).get(
        "/api/ontology/catalog",
        headers={"X-Tenant-ID": ctx.tenant_id, "X-User-ID": "viewer-user", "X-Roles": "viewer"},
    )

    assert response.status_code == 200
    catalog = response.json()
    assert catalog["status"] == "active"
    assert catalog["ontologyVersionId"].startswith("ont_")

    objects = {item["apiName"]: item for item in catalog["objectTypes"]}
    assert set(objects) == {"Customer", "Order"}
    assert objects["Order"]["primaryKeyProperty"] == "orderId"

    order_properties = {item["apiName"]: item for item in objects["Order"]["properties"]}
    assert order_properties["orderId"]["columnName"] == "order_id"
    assert order_properties["margin"]["classification"] == "finance"
    assert order_properties["operatorNote"]["source"] == "edit_layer"

    assert objects["Order"]["actions"][0]["apiName"] == "ApproveOrder"
    assert objects["Order"]["actions"][0]["parameterSchema"]["required"] == ["reason"]
    assert catalog["linkTypes"][0]["apiName"] == "OrderCustomer"
    assert catalog["linkTypes"][0]["fromObjectType"] == "Order"
    assert catalog["linkTypes"][0]["toObjectType"] == "Customer"


def test_api_insight_reviews_create_assign_and_decide_with_audit_evidence(foundry, monkeypatch) -> None:
    ctx = demo_admin_context()
    monkeypatch.setattr(api_main, "foundry", foundry)
    client = TestClient(app)
    headers = {
        "X-Tenant-ID": ctx.tenant_id,
        "X-User-ID": ctx.actor_user_id,
        "X-Roles": "admin,data_engineer,ops_manager",
    }
    payload = {
        "claimId": "claim-1",
        "claimText": "Supplier risk increased",
        "evidenceObjectIds": ["evidence-1"],
        "evidenceRefs": [{"evidenceId": "evidence-1", "sourceObjectId": "O-1001"}],
        "priority": "high",
        "actionProposal": {"actionApiName": "ApproveOrder", "targetObjectId": "O-1001"},
    }

    created = client.post(
        "/api/insights/reviews",
        headers={**headers, "Idempotency-Key": "insight-create-key"},
        json=payload,
    )
    replayed = client.post(
        "/api/insights/reviews",
        headers={**headers, "Idempotency-Key": "insight-create-key"},
        json=payload,
    )
    mismatched_replay = client.post(
        "/api/insights/reviews",
        headers={**headers, "Idempotency-Key": "insight-create-key"},
        json={**payload, "claimText": "Different supplier risk claim"},
    )
    review_id = created.json()["id"]
    listing = client.get("/api/insights/reviews?status=pending", headers=headers)
    assigned = client.post(
        f"/api/insights/reviews/{review_id}/assign",
        headers={**headers, "Idempotency-Key": "insight-assign-key"},
        json={"assigneeUserId": "ops-reviewer"},
    )
    decided = client.post(
        f"/api/insights/reviews/{review_id}/decision",
        headers={**headers, "Idempotency-Key": "insight-decision-key"},
        json={"decision": "approved", "comment": "Evidence checked"},
    )
    conflict = client.post(
        f"/api/insights/reviews/{review_id}/decision",
        headers={**headers, "Idempotency-Key": "insight-reject-key"},
        json={"decision": "rejected"},
    )

    assert created.status_code == 200
    assert replayed.json()["id"] == review_id
    assert mismatched_replay.status_code == 409
    assert listing.json()["items"][0]["id"] == review_id
    assert assigned.json()["assigneeUserId"] == "ops-reviewer"
    assert decided.json()["status"] == "approved"
    assert conflict.status_code == 409
    assert _audit_event_types(foundry.engine, "insight_review") == [
        "insight_review.created",
        "insight_review.assigned",
        "insight_review.approved",
    ]


def test_api_observability_detect_returns_active_incident_report(monkeypatch) -> None:
    class FakeOperations:
        def observability_report(self, *, ctx, configs, previous_incidents, observed_at):
            assert ctx.actor_user_id == "ops-user"
            assert configs[0]["detectorId"] == "raw-orders-flow"
            assert previous_incidents == []
            assert observed_at == "2026-06-19T00:15:00Z"
            return {
                "generatedAt": observed_at,
                "configurationVersion": "s56-v1",
                "activeIncidents": [
                    {
                        "id": "incident:raw-orders-flow",
                        "detectorId": "raw-orders-flow",
                        "detectorType": "flow_interruption",
                        "configVersion": "s56-v1",
                        "status": "active",
                        "severity": "warning",
                        "owner": "data-platform",
                        "message": "missing data beyond expected cadence",
                        "dedupeKey": "raw-orders-flow:flow_interruption:dataset:raw.orders",
                        "firstObservedAt": observed_at,
                        "lastObservedAt": observed_at,
                        "evidence": {"failedRunCount": 0},
                        "evidenceLinks": [],
                        "threshold": {"expectedCadenceSeconds": 300},
                    }
                ],
                "suppressedIncidents": [],
                "summary": {"active": 1, "suppressed": 0, "evaluatedDetectors": 1},
            }

    class FakeFoundry:
        operations = FakeOperations()

    monkeypatch.setattr(api_main, "foundry", FakeFoundry())
    response = TestClient(app).post(
        "/api/operations/observability/detect",
        headers={"X-User-ID": "ops-user", "X-Roles": "ops_manager"},
        json={
            "observedAt": "2026-06-19T00:15:00Z",
            "configs": [
                {
                    "detectorId": "raw-orders-flow",
                    "detectorType": "flow_interruption",
                    "configVersion": "s56-v1",
                    "owner": "data-platform",
                    "severity": "warning",
                    "runType": "sync",
                    "resourceType": "dataset",
                    "resourceId": "raw.orders",
                    "expectedCadenceSeconds": 300,
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["activeIncidents"][0]["detectorId"] == "raw-orders-flow"


def test_api_backup_restore_preflight_returns_commit_point_report(monkeypatch) -> None:
    class FakeOperations:
        def restore_preflight_report(self, *, ctx, backup_id):
            assert ctx.actor_user_id == "ops-user"
            assert backup_id == "backup-api"
            return {
                "generatedAt": "2026-06-19T01:00:00Z",
                "tenantId": ctx.tenant_id,
                "backupId": backup_id,
                "status": "blocked",
                "datasetVersions": [],
                "issues": [
                    {
                        "code": "committed_manifest_missing",
                        "datasetRef": "raw.orders",
                        "datasetId": "ds_orders",
                        "versionId": "dsv_orders",
                        "manifestUri": "/missing/manifest.json",
                        "message": "manifest is missing",
                        "details": {"servingTruth": "metadata_db_committed_dataset_version"},
                    }
                ],
                "activeIndexPointers": [],
                "highWatermarks": {},
                "temporalStrategy": {"workflowProfile": "local-workflow"},
                "searchRebuild": {"rebuildRequiredAfterRestore": True},
                "restoreTrafficGate": {"writeTrafficMustBePausedBeforeRestore": True},
                "summary": {"issueCount": 1},
            }

    class FakeFoundry:
        operations = FakeOperations()

    monkeypatch.setattr(api_main, "foundry", FakeFoundry())
    response = TestClient(app).post(
        "/api/operations/backup-restore/preflight",
        headers={"X-User-ID": "ops-user", "X-Roles": "ops_manager"},
        json={"backupId": "backup-api"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "blocked"
    assert response.json()["issues"][0]["code"] == "committed_manifest_missing"


def test_api_backup_restore_mode_start_and_status_return_pause_gate(monkeypatch) -> None:
    class FakeOperations:
        def start_restore_mode(self, *, ctx, backup_id, restore_id):
            assert ctx.actor_user_id == "ops-user"
            assert backup_id == "backup-api"
            assert restore_id == "restore-api"
            return _restore_mode_payload(ctx.tenant_id)

        def restore_mode_status(self, restore_id, *, ctx):
            assert restore_id == "restore-api"
            return _restore_mode_payload(ctx.tenant_id)

        def approve_restore_resume(self, restore_id, *, ctx, validation_id):
            assert restore_id == "restore-api"
            assert validation_id == "closed-loop-api"
            return _restore_mode_payload(ctx.tenant_id, status="resume_approved")

    class FakeFoundry:
        operations = FakeOperations()

    monkeypatch.setattr(api_main, "foundry", FakeFoundry())
    client = TestClient(app)
    headers = {"X-User-ID": "ops-user", "X-Roles": "ops_manager"}
    started = client.post(
        "/api/operations/backup-restore/restore-mode/start",
        headers=headers,
        json={"backupId": "backup-api", "restoreId": "restore-api"},
    )
    status = client.get("/api/operations/backup-restore/restore-mode/restore-api", headers=headers)
    approved = client.post(
        "/api/operations/backup-restore/restore-mode/restore-api/approve-resume",
        headers=headers,
        json={"validationId": "closed-loop-api"},
    )

    assert started.status_code == 200
    assert status.status_code == 200
    assert approved.status_code == 200
    assert started.json()["is_outbox_publisher_paused"] is True
    assert status.json()["is_serving_traffic_open"] is False
    assert approved.json()["status"] == "resume_approved"
    assert approved.json()["is_outbox_publisher_paused"] is False


def test_api_webhook_ingest_verifies_signature_and_appends_dataset(foundry, monkeypatch) -> None:
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
    foundry.datasets.ensure("raw.webhook_orders", ctx=ctx, primary_key=["event_id"])
    monkeypatch.setattr(api_main, "foundry", foundry)
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

    preview = foundry.datasets.preview("raw.webhook_orders", ctx=ctx)
    transactions = _dataset_transactions(foundry.engine)
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
    deny_events = foundry.operations.query_runs(ctx=ctx, run_type="audit", status="deny")["auditEvents"]
    assert deny_events[0]["action"] == "webhook:ingest"


def test_api_webhook_rejects_oversized_body_before_commit(foundry, monkeypatch) -> None:
    ctx = demo_admin_context()
    body = b'{"order_id":"O-oversized","status":"PENDING","padding":"too-large"}'
    timestamp = _webhook_timestamp()
    headers = {
        "Content-Type": "application/json",
        "X-Foundry-Lite-Signature": "sha256=not-used-before-size-check",
        "X-Foundry-Lite-Timestamp": timestamp,
        "X-Foundry-Lite-Event-ID": "evt-order-oversized",
        "X-User-ID": ctx.actor_user_id,
        "X-Roles": ",".join(ctx.roles),
    }
    foundry.datasets.ensure("raw.webhook_oversized_orders", ctx=ctx, primary_key=["event_id"])
    monkeypatch.setattr(api_main, "foundry", foundry)
    monkeypatch.setenv(WEBHOOK_BODY_LIMIT_ENV, "16")

    response = TestClient(app).post(
        "/api/connectors/webhooks/mock_saas/orders",
        params={"datasetRef": "raw.webhook_oversized_orders"},
        headers=headers,
        content=body,
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_FAILED"
    assert response.json()["detail"]["details"]["max_bytes"] == 16
    assert foundry.datasets.list_versions("raw.webhook_oversized_orders", ctx=ctx) == []


def test_api_webhook_missing_secret_leaves_operator_evidence(foundry, monkeypatch) -> None:
    ctx = demo_admin_context()
    body = b'{"order_id":"O-9001","status":"PENDING"}'
    timestamp = _webhook_timestamp()
    headers = {
        "Content-Type": "application/json",
        "X-Foundry-Lite-Signature": _webhook_signature(body, "missing-secret", timestamp),
        "X-Foundry-Lite-Timestamp": timestamp,
        "X-Foundry-Lite-Event-ID": "evt-order-missing-secret",
        "X-Request-ID": ctx.request_id,
        "X-User-ID": ctx.actor_user_id,
        "X-Roles": ",".join(ctx.roles),
    }
    foundry.datasets.ensure("raw.webhook_missing_secret_orders", ctx=ctx, primary_key=["event_id"])
    monkeypatch.setattr(api_main, "foundry", foundry)
    monkeypatch.delenv(api_main.WEBHOOK_SIGNING_KEY_ENV, raising=False)
    client = TestClient(app)

    response = client.post(
        "/api/connectors/webhooks/mock_saas/orders",
        params={"datasetRef": "raw.webhook_missing_secret_orders"},
        headers=headers,
        content=body,
    )

    audit_events = foundry.operations.query_runs(ctx=ctx, run_type="audit", status="deny")["auditEvents"]
    failure_events = [event for event in audit_events if event["event_type"] == "webhook.secret_resolution_failed"]
    error = failure_events[0]["after_ref"]["error"]
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_FAILED"
    assert foundry.datasets.list_versions("raw.webhook_missing_secret_orders", ctx=ctx) == []
    assert failure_events[0]["request_id"] == ctx.request_id
    assert error["type"] == "VALIDATION_FAILED"
    assert error["trace"]["adapter"] == "secret_provider.get_secret"
    assert error["details"]["secret_value"] == "***REDACTED***"


def test_webhook_same_event_id_different_payload_is_deduped(foundry, monkeypatch) -> None:
    ctx = demo_admin_context()
    secret = "local-webhook-secret"
    event_id = "evt-order-volatile"
    first_body = b'{"order_id":"O-9002","status":"PENDING","timestamp":"2026-06-15T01:00:00Z"}'
    duplicate_body = b'{"order_id":"O-9002","status":"PENDING","timestamp":"2026-06-15T01:00:05Z"}'
    changed_body = b'{"order_id":"O-9002","status":"SHIPPED","timestamp":"2026-06-15T01:00:06Z"}'
    foundry.datasets.ensure("raw.webhook_dedupe_orders", ctx=ctx, primary_key=["event_id"])
    monkeypatch.setattr(api_main, "foundry", foundry)
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

    transactions = _dataset_transactions(foundry.engine)
    append_transactions = [tx for tx in transactions if tx["tx_type"] == "APPEND"]
    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json()["version_id"] == first.json()["version_id"]
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "CONFLICT"
    assert len(append_transactions) == 1


def test_webhook_signature_replay_and_clock_skew_policy(foundry, monkeypatch) -> None:
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
    foundry.datasets.ensure("raw.webhook_replay_orders", ctx=ctx, primary_key=["event_id"])
    monkeypatch.setattr(api_main, "foundry", foundry)
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
        for event in foundry.operations.list_runs(ctx=ctx)["auditEvents"]
        if event["event_type"] == "permission.denied" and event["resource_id"] == "mock_saas:orders"
    ]
    assert replay.status_code == 403
    assert replay.json()["detail"]["code"] == "PERMISSION_DENIED"
    assert foundry.datasets.list_versions("raw.webhook_replay_orders", ctx=ctx) == []
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
    failing_foundry = FoundryLite(
        dependencies=replace(dependencies, compute_adapter=_RowsToParquetFailingComputeAdapter())
    )
    failing_foundry.datasets.ensure("raw.webhook_commit_fail_orders", ctx=ctx, primary_key=["event_id"])
    monkeypatch.setattr(api_main, "foundry", failing_foundry)
    monkeypatch.setenv(api_main.WEBHOOK_SIGNING_KEY_ENV, secret)

    response = TestClient(app).post(
        "/api/connectors/webhooks/mock_saas/orders",
        params={"datasetRef": "raw.webhook_commit_fail_orders"},
        headers=headers,
        content=body,
    )

    transactions = _dataset_transactions(failing_foundry.engine)
    append_transactions = [tx for tx in transactions if tx["tx_type"] == "APPEND"]
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_FAILED"
    assert not [tx for tx in append_transactions if tx["status"] == "COMMITTED"]
    assert [tx for tx in append_transactions if tx["status"] == "ABORTED"]


def test_api_operations_runs_cursor_pages_action_runs(foundry, monkeypatch) -> None:
    ctx = prepare_indexed_demo(foundry)
    monkeypatch.setattr(api_main, "foundry", foundry)
    client = TestClient(app)
    headers = {"X-User-ID": ctx.actor_user_id, "X-Roles": ",".join(ctx.roles)}

    first_order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    second_order = foundry.objects.get("Order", "O-1002", ctx=ctx)
    foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=first_order["objectVersion"],
        params={"reason": "Ops page one"},
        idempotency_key="api-operations-page-one",
        ctx=ctx,
    )
    foundry.actions.apply(
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


def test_api_ai_operations_run_detail_returns_safe_ledger_payload(monkeypatch) -> None:
    class AiOperations:
        def run_detail(self, run_type: str, run_id: str, **kwargs: object) -> dict[str, object]:
            ctx = kwargs["ctx"]
            assert run_type == "ai"
            assert run_id == "ai-run-ops"
            assert "ops_manager" in ctx.roles
            return {
                "runType": "ai",
                "runId": run_id,
                "row": {"id": run_id, "status": "succeeded"},
                "status": "succeeded",
                "errorMessage": None,
                "error": None,
                "correlationId": "trace-ops",
                "references": {"compiled_prompt_hash": "sha256:compiled-prompt"},
                "investigation": {
                    "summary": "ai run ai-run-ops is succeeded",
                    "status": "succeeded",
                    "errorMessage": None,
                    "correlationId": "trace-ops",
                    "references": {"compiled_prompt_hash": "sha256:compiled-prompt"},
                    "relatedCounts": {
                        "outboxEvents": 0,
                        "auditEvents": 0,
                        "objectEdits": 0,
                        "actionWritebacks": 0,
                        "lineageEdges": 0,
                    },
                    "suggestedActions": [],
                },
                "relatedOutboxEvents": [],
                "relatedAuditEvents": [],
                "relatedObjectEdits": [],
                "relatedActionWritebacks": [],
                "runRelations": [],
                "lineageEdges": [],
                "ai": {
                    "summary": {"eventCount": 2, "inputTokens": 128, "estimatedCost": 0.0123},
                    "trace": {"traceId": "trace-ops", "timeline": [{"kind": "event"}]},
                },
            }

    class AiFoundry:
        operations = AiOperations()

    monkeypatch.setattr(api_main, "foundry", AiFoundry())
    response = TestClient(app).get(
        "/api/operations/runs/ai/ai-run-ops",
        headers={"X-User-ID": "ops-user", "X-Roles": "ops_manager"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["runType"] == "ai"
    assert body["ai"]["summary"]["eventCount"] == 2
    assert body["ai"]["trace"]["traceId"] == "trace-ops"


def test_api_aip_builder_validate_returns_read_only_release_preflight(foundry, monkeypatch) -> None:
    monkeypatch.setattr(api_main, "foundry", foundry)
    response = TestClient(app).post(
        "/api/aip/builder/validate",
        headers={"X-Tenant-ID": "tenant-demo", "X-User-ID": "ops-user", "X-Roles": "admin,ops_manager"},
        json={
            "agentVersionId": "agent.order-ops.v1",
            "releaseChannel": "dev",
            "modelAliasVersion": "gpt-governed@v1",
            "promptVersionId": "prompt-order-copilot@v1",
            "contextSources": [
                {
                    "sourceId": "ctx-order-ops",
                    "kind": "ontology.object_set",
                    "securityPartition": "tenant-demo:internal",
                    "selectedProperties": ["orderId", "status"],
                }
            ],
            "toolManifest": [
                {
                    "toolId": "ontology.get_object",
                    "version": "2026-06-25",
                    "effect": "READ",
                    "confirmationPolicy": "NONE",
                    "inputSchema": {"type": "object", "required": ["object_type", "object_id"]},
                    "outputSchema": {"type": "object"},
                }
            ],
            "logicBlocks": [
                {"blockId": "input", "kind": "Input", "inputs": {"objectId": "O-1001"}},
                {
                    "blockId": "retrieve",
                    "kind": "CallFunction",
                    "inputs": {"toolId": "ontology.get_object", "version": "2026-06-25"},
                    "dependsOn": ["input"],
                },
                {
                    "blockId": "proposal",
                    "kind": "CreateActionProposal",
                    "inputs": {"actionType": "ApproveOrder"},
                    "dependsOn": ["retrieve"],
                },
                {"blockId": "output", "kind": "Output", "inputs": {"fromBlock": "proposal"}},
            ],
            "evalAxes": ["retrieval", "answer", "tool", "action", "security"],
            "agentAllowedActions": ["ApproveOrder"],
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert body["validationStatus"] == "ready"
    assert body["draftHash"].startswith("sha256:")
    assert body["releaseReady"] is True


def test_api_aip_builder_run_executes_logic_and_links_operations_detail(foundry, monkeypatch) -> None:
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    monkeypatch.setattr(api_main, "foundry", foundry)
    headers = {
        "X-Tenant-ID": "tenant-demo",
        "X-User-ID": "ops-user",
        "X-Roles": "admin,data_engineer,ops_manager",
    }

    response = TestClient(app).post(
        "/api/aip/builder/run",
        headers=headers,
        json={
            "logicRunId": "logic-api-builder-run-1",
            "agentVersionId": "agent.order-ops.v1",
            "releaseChannel": "dev",
            "modelAliasVersion": "gpt-governed@v1",
            "promptVersionId": "prompt-order-copilot@v1",
            "contextSources": [
                {
                    "sourceId": "ctx-order-ops",
                    "kind": "ontology.object_set",
                    "securityPartition": "tenant-demo:internal",
                    "selectedProperties": ["orderId", "status", "priority", "customerId"],
                }
            ],
            "toolManifest": [
                {
                    "toolId": "ontology.get_object",
                    "version": "2026-06-25",
                    "effect": "READ",
                    "requiredPermission": "object:read",
                    "confirmationPolicy": "NONE",
                    "objectTypeAllowlist": ["Order"],
                    "propertyAllowlist": ["orderId", "status", "priority", "customerId"],
                    "inputSchema": {
                        "type": "object",
                        "required": ["object_type", "object_id"],
                        "properties": {
                            "object_type": {"type": "string"},
                            "object_id": {"type": "string"},
                            "property_names": {"type": "array"},
                        },
                    },
                    "outputSchema": {"type": "object"},
                }
            ],
            "logicBlocks": [
                {"blockId": "input", "kind": "Input", "inputs": {"objectId": "O-1001"}},
                {
                    "blockId": "retrieve",
                    "kind": "CallFunction",
                    "inputs": {
                        "toolId": "ontology.get_object",
                        "version": "2026-06-25",
                        "arguments": {
                            "object_type": "Order",
                            "object_id": "O-1001",
                            "property_names": ["orderId", "status", "priority", "customerId"],
                        },
                    },
                    "dependsOn": ["input"],
                },
                {
                    "blockId": "proposal",
                    "kind": "CreateActionProposal",
                    "inputs": {
                        "actionType": "ApproveOrder",
                        "targetObjectType": "Order",
                        "targetObjectId": "O-1001",
                        "expectedObjectVersion": order["objectVersion"],
                        "parameters": {"reason": "Inventory confirmed"},
                        "evidenceContextIds": ["ctx-order-ops"],
                        "expiresAt": "2026-06-26T00:10:00Z",
                        "claimText": "Approve O-1001 based on selected AIP evidence.",
                        "originatingToolCallId": "logic-api-builder-run-1-retrieve",
                    },
                    "dependsOn": ["retrieve"],
                },
                {"blockId": "output", "kind": "Output", "inputs": {"fromBlock": "proposal"}, "dependsOn": ["proposal"]},
            ],
            "evalAxes": ["retrieval", "answer", "citation", "tool", "action", "security"],
            "agentAllowedTools": ["ontology.get_object"],
            "agentAllowedActions": ["ApproveOrder"],
            "modelAllowedClassifications": ["public", "internal"],
            "inputJson": {"objectType": "Order", "objectId": "O-1001"},
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["validation"]["validationStatus"] == "ready"
    assert body["runStatus"] == "succeeded"
    assert body["logic"]["output"]["status"] == "pending"
    assert body["operations"]["runType"] == "ai"

    detail = TestClient(app).get(f"/api/operations/runs/ai/{body['aiRunId']}", headers=headers)
    detail_body = detail.json()
    assert detail.status_code == 200
    assert detail_body["ai"]["summary"]["contextItemCount"] == 1
    assert detail_body["ai"]["summary"]["toolCallCount"] == 1
    assert detail_body["ai"]["summary"]["status"] == "succeeded"
    assert detail_body["ai"]["toolCalls"][0]["tool_id"] == "ontology.get_object"


def test_api_aip_agent_run_calls_model_and_links_operations_detail(foundry, monkeypatch) -> None:
    prepare_indexed_demo(foundry)
    monkeypatch.setattr(api_main, "foundry", foundry)
    headers = {
        "X-Tenant-ID": "tenant-demo",
        "X-User-ID": "ops-user",
        "X-Roles": "admin,data_engineer,ops_manager",
    }
    client = TestClient(app)

    response = client.post(
        "/api/aip/agent/run",
        headers=headers,
        json={
            "agentRunId": "agent-api-runtime-1",
            "agentVersionId": "agent.order-ops.v1",
            "modelAlias": "default-completion",
            "promptVersionId": "prompt-order-copilot@v1",
            "userMessage": "Explain Order O-1001 for the operator.",
            "agentInstruction": "Answer as the Order Operations Copilot. Do not execute tools.",
            "securityPartition": "tenant-demo:internal",
            "allowedSecurityPartitions": ["tenant-demo:internal"],
            "stateJson": {"objectType": "Order", "objectId": "O-1001"},
            "outputSchema": {"type": "object"},
            "dataClassification": "internal",
            "maxContextItems": 4,
            "maxContextTokens": 1200,
            "maxModelCalls": 1,
            "maxLoopIterations": 1,
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["runStatus"] == "succeeded"
    assert body["answer"] == "echo: Explain Order O-1001 for the operator."
    assert body["operations"]["runType"] == "ai"
    assert len(body["contextIds"]) == 1

    detail = client.get(f"/api/operations/runs/ai/{body['aiRunId']}", headers=headers)
    detail_body = detail.json()
    assert detail.status_code == 200
    assert detail_body["ai"]["summary"]["modelCallCount"] == 1
    assert detail_body["ai"]["summary"]["contextItemCount"] == 1
    assert detail_body["ai"]["summary"]["toolCallCount"] == 0
    assert detail_body["ai"]["summary"]["promptArtifactCount"] == 1
    artifact = detail_body["ai"]["promptArtifacts"][0]
    assert artifact["artifact_ref"].startswith("local-prompt-artifact://")
    assert artifact["content_hash"] == detail_body["row"]["compiled_prompt_hash"]
    assert artifact["encryption_algorithm"] == "fernet-v1"
    assert artifact["export_marking"] == "aip-trace-restricted"
    serialized_detail = json.dumps(detail_body, sort_keys=True)
    assert "Explain Order O-1001 for the operator." not in serialized_detail
    assert "Answer as the Order Operations Copilot" not in serialized_detail
    assert "pytest-prompt-artifact-key" not in serialized_detail
    denied = client.get(
        f"/api/operations/runs/ai/{body['aiRunId']}/prompt-artifacts/{artifact['id']}",
        headers=headers,
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "PERMISSION_DENIED"

    reader = client.get(
        f"/api/operations/runs/ai/{body['aiRunId']}/prompt-artifacts/{artifact['id']}",
        headers={**headers, "X-Roles": headers["X-Roles"] + ",aip_prompt_artifact_reader"},
    )
    reader_body = reader.json()
    prompt_payload = json.loads(reader_body["plaintext"])
    assert reader.status_code == 200
    assert reader_body["artifactId"] == artifact["id"]
    assert reader_body["aiRunId"] == body["aiRunId"]
    assert reader_body["contentHash"] == artifact["content_hash"]
    assert reader_body["exportMarking"] == "aip-trace-restricted"
    assert reader_body["plaintext"] not in serialized_detail
    assert "Explain Order O-1001 for the operator." in {message["content"] for message in prompt_payload["messages"]}


def test_api_security_roles_mask_and_audit_denials(foundry, monkeypatch) -> None:
    ctx = prepare_indexed_demo(foundry)
    monkeypatch.setattr(api_main, "foundry", foundry)
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
    finance_preview = client.get("/api/datasets/clean/orders/preview", headers=finance_headers)
    assert preview.status_code == 200
    assert preview.json()[0]["order_id"] == "O-1001"
    # the backing dataset must not leak the masked value via preview
    assert preview.json()[0]["margin"] == "***MASKED***"
    assert finance_preview.json()[0]["margin"] != "***MASKED***"

    viewer_order = client.get("/api/objects/Order/O-1001", headers=viewer_headers)
    finance_order = client.get("/api/objects/Order/O-1001", headers=finance_headers)
    assert viewer_order.status_code == 200
    assert finance_order.status_code == 200
    assert viewer_order.json()["properties"]["margin"] == "***MASKED***"
    assert finance_order.json()["properties"]["margin"] != "***MASKED***"

    # ?explain=true must not bypass masking: a viewer (no object:explain) gets no
    # explain block at all; an operator gets it but with base/edit still masked;
    # finance/admin see the real value, consistent with the masked-properties policy.
    admin_headers = {"X-Tenant-ID": ctx.tenant_id, "X-User-ID": "admin-api", "X-Roles": "admin"}
    viewer_explain = client.get("/api/objects/Order/O-1001", headers=viewer_headers, params={"explain": "true"})
    ops_explain = client.get("/api/objects/Order/O-1001", headers=ops_headers, params={"explain": "true"})
    finance_explain = client.get("/api/objects/Order/O-1001", headers=finance_headers, params={"explain": "true"})
    admin_explain = client.get("/api/objects/Order/O-1001", headers=admin_headers, params={"explain": "true"})
    assert viewer_explain.status_code == 200
    assert "explain" not in viewer_explain.json()
    assert ops_explain.json()["explain"]["baseProperties"]["margin"] == "***MASKED***"
    assert finance_explain.json()["explain"]["baseProperties"]["margin"] != "***MASKED***"
    assert admin_explain.json()["explain"]["baseProperties"]["margin"] != "***MASKED***"

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


def test_api_dataset_object_action_and_metrics_smoke(foundry, monkeypatch) -> None:
    ctx = prepare_indexed_demo(foundry)
    monkeypatch.setattr(api_main, "foundry", foundry)
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
    metrics_after_preview = client.get("/metrics")
    assert 'path="/api/datasets/{namespace}/{name}/preview"' in metrics_after_preview.text
    assert 'path="/api/datasets/clean/orders/preview"' not in metrics_after_preview.text

    datasets = client.get("/api/datasets", headers=headers)
    dataset_rows = {(row["namespace"], row["name"]): row for row in datasets.json()}
    assert datasets.status_code == 200
    assert ("clean", "orders") in dataset_rows
    assert dataset_rows[("clean", "orders")]["primary_key"] == ["order_id"]

    versions = client.get("/api/datasets/clean/orders/versions", headers=headers)
    assert versions.status_code == 200
    assert versions.json()[0]["version_number"] == 1
    assert versions.json()[0]["row_count"] == 3

    inspected = client.get("/api/datasets/clean/orders/inspect", headers=headers)
    assert inspected.status_code == 200
    assert inspected.json()["dataset"] == "clean.orders"
    assert inspected.json()["version"]["version_number"] == 1
    assert inspected.json()["manifest"]["files"][0]["row_count"] == 3

    valid_ontology = Path("examples/supply-chain-demo/ontology/order-customer.yaml").read_text(encoding="utf-8")
    ontology_validation = client.post("/api/ontology/validate", headers=headers, json={"yaml": valid_ontology})
    assert ontology_validation.status_code == 200
    assert ontology_validation.json() == {
        "status": "valid",
        "object_type_count": 2,
        "link_type_count": 1,
        "action_type_count": 1,
    }
    with foundry.engine.begin() as conn:
        ontology_rows = conn.execute(select(db.ontology_versions.c.status)).scalars().all()
    assert ontology_rows == ["active"]

    invalid_ontology = valid_ontology.replace("column: order_id", "column: missing_order_id", 1)
    ontology_error = client.post("/api/ontology/validate", headers=headers, json={"yaml": invalid_ontology})
    assert ontology_error.status_code == 400
    assert ontology_error.json()["detail"]["code"] == "VALIDATION_FAILED"
    assert ontology_error.json()["detail"]["message"] == "primary key column missing"
    assert ontology_error.json()["detail"]["details"] == {"column": "missing_order_id"}

    order = client.get("/api/objects/Order/O-1001", headers=headers, params={"explain": "true"})
    assert order.status_code == 200
    order_payload = order.json()
    assert order_payload["objectId"] == "O-1001"
    source_run = _source_run_link(order_payload["explain"]["sourceRunChain"], run_type="index")
    assert source_run["relation"] == "indexed_from"
    assert source_run["operationPath"] == f"/api/operations/runs/index/{source_run['runId']}"

    links = client.get("/api/objects/Order/O-1001/links/OrderCustomer", headers=headers)
    assert links.status_code == 200
    assert links.json()[0]["to"]["objectType"] == "Customer"
    assert links.json()[0]["to"]["objectId"] == "C-100"

    source_run_detail = client.get(source_run["operationPath"], headers=headers)
    assert source_run_detail.status_code == 200
    assert source_run_detail.json()["runType"] == "index"
    assert source_run_detail.json()["runId"] == source_run["runId"]

    lineage = client.get(
        "/api/operations/lineage",
        headers=headers,
        params={"resourceId": order_payload["sourceDatasetVersionId"]},
    )
    assert lineage.status_code == 200
    assert any(row["to_resource_id"] == order_payload["sourceDatasetVersionId"] for row in lineage.json())
    viewer_lineage = client.get(
        "/api/operations/lineage",
        headers={**headers, "X-User-ID": "viewer-user", "X-Roles": "viewer"},
        params={"resourceId": order_payload["sourceDatasetVersionId"]},
    )
    assert viewer_lineage.status_code == 403
    assert viewer_lineage.json()["detail"]["code"] == "PERMISSION_DENIED"

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
    action_payload = action.json()
    assert action_payload["status"] == "succeeded"

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
    assert action_run["actor_user_id"] == ctx.actor_user_id
    assert action_run["action_type_api_name"] == "ApproveOrder"
    assert action_run["target_object_type_api_name"] == "Order"
    assert action_run["status"] == "succeeded"
    assert action_run["parameters"] == {"reason": "Inventory confirmed"}
    assert action_run["error"] is None
    assert isinstance(action_run["created_at"], str)
    assert isinstance(action_run["completed_at"], str)

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
    relation_targets = {
        (row["target_run_type"], row["relation"], row["metadata"].get("eventType")) for row in detail["runRelations"]
    }
    assert ("action_writeback", "writeback_attempt", None) in relation_targets
    assert ("outbox", "emitted", "action.run.committed") in relation_targets

    action_log = client.post("/api/materializations/action_log/run", headers=headers)
    assert action_log.status_code == 200
    action_log_payload = action_log.json()
    assert action_log_payload["dataset_ref"] == "ops.action_log"
    assert action_log_payload["row_count"] == 1
    materialization_runs = client.get(
        "/api/operations/runs",
        headers=headers,
        params={"runType": "materialization", "status": "succeeded"},
    )
    assert materialization_runs.status_code == 200
    materialization_run = next(
        row
        for row in materialization_runs.json()["materializationRuns"]
        if row["target_dataset_version_id"] == action_log_payload["version_id"]
    )
    materialization_detail = client.get(
        f"/api/operations/runs/materialization/{materialization_run['id']}",
        headers=headers,
    )
    assert materialization_detail.status_code == 200
    assert any(
        row["target_run_type"] == "outbox"
        and row["relation"] == "emitted"
        and row["resource_id"] == action_log_payload["version_id"]
        for row in materialization_detail.json()["runRelations"]
    )
    action_log_preview = client.get("/api/datasets/ops/action_log/preview", headers=headers)
    assert action_log_preview.status_code == 200
    assert action_log_preview.json()[0]["action_run_id"] == action_payload["actionRunId"]
    assert action_log_preview.json()[0]["actor_user_id"] == ctx.actor_user_id
    assert action_log_preview.json()[0]["target_object_id"] == "O-1001"
    assert action_log_preview.json()[0]["parameters_json"] == '{"reason": "Inventory confirmed"}'

    missing_materialization = client.post("/api/materializations/missing/run", headers=headers)
    assert missing_materialization.status_code == 404
    assert missing_materialization.json()["detail"]["code"] == "NOT_FOUND"
    assert missing_materialization.json()["detail"]["details"] == {"api_name": "missing"}
    assert "request_id" in missing_materialization.json()["detail"]

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

    source_ref = _seed_failed_index_run(foundry.engine, tenant_id=ctx.tenant_id, run_id="index_api_failed")
    failed_run_replay = client.post("/api/operations/runs/index/index_api_failed/replay", headers=headers)
    assert failed_run_replay.status_code == 200
    failed_replay = failed_run_replay.json()
    assert failed_replay["object_type"] == "Order"
    replay_runs = client.get("/api/operations/runs?runType=index", headers=headers).json()
    replay_row = _runtime_row(replay_runs["indexRuns"], failed_replay["index_run_id"])
    assert replay_row["trigger_type"] == "failed_run_replay"
    assert replay_row["source_ref"] == {**source_ref, "replay_of_run_id": "index_api_failed"}

    input_versions = _seed_failed_transform_run(foundry.engine, tenant_id=ctx.tenant_id, run_id="transform_api_failed")
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
    transform_detail_payload = transform_detail.json()
    assert any(
        row["event_type"] == "transform.run.retry_requested" for row in transform_detail_payload["relatedAuditEvents"]
    )
    assert any(
        row["source_run_id"] == "transform_api_failed"
        and row["target_run_id"] == transform_retry_payload["transform_run_id"]
        and row["relation"] == "retry_of"
        for row in transform_detail_payload["runRelations"]
    )

    missing = client.get("/api/objects/Order/NOPE", headers=headers)
    assert missing.status_code == 404
    assert "request_id" in missing.json()["detail"]

    missing_preview = client.get("/api/datasets/clean/missing/preview", headers=headers)
    assert missing_preview.status_code == 404

    missing_set = client.get("/api/object-sets/missing", headers=headers)
    assert missing_set.status_code == 404


def test_api_operations_retry_dead_letter_event(foundry, monkeypatch) -> None:
    ctx = prepare_indexed_demo(foundry)
    _approve_order_for_materialization(foundry, ctx, idempotency_key="api-dlq-materialization")
    _seed_dead_letter_event(foundry.engine, tenant_id=ctx.tenant_id, outbox_id="outbox_api_retry", dlq_id="dlq_api")
    monkeypatch.setattr(api_main, "foundry", foundry)
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


def test_api_operations_retry_dead_letter_event_keeps_dlq_when_reprocess_fails(foundry, monkeypatch) -> None:
    ctx = prepare_indexed_demo(foundry)
    # The dead-letter event names a materialization that does not exist, so the
    # reprocess step fails before the DLQ row is consumed. A failed reprocess
    # must keep the dead-letter event and leave the source outbox event failed
    # rather than silently reporting success (failure must not look like success).
    _seed_dead_letter_event(
        foundry.engine,
        tenant_id=ctx.tenant_id,
        outbox_id="outbox_api_failed",
        dlq_id="dlq_api_failed",
        materialization="ops_missing_materialization",
    )
    monkeypatch.setattr(api_main, "foundry", foundry)
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


def test_api_operations_record_dead_letter_records_retry_bulk_and_discard(foundry, monkeypatch) -> None:
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.shipment_cdc", ctx=ctx, primary_key=["event_id"])
    _seed_dead_letter_record(foundry.engine, tenant_id=ctx.tenant_id, record_id="dlqr_api_retry")
    _seed_dead_letter_record(
        foundry.engine,
        tenant_id=ctx.tenant_id,
        record_id="dlqr_api_bulk",
        source_event_id="shipment_cdc_events:0:2",
        payload_hash="payload-hash-bulk",
    )
    _seed_dead_letter_record(
        foundry.engine,
        tenant_id=ctx.tenant_id,
        record_id="dlqr_api_discard",
        source_event_id="shipment_cdc_events:0:3",
        payload_hash="payload-hash-discard",
    )
    _seed_dead_letter_record(
        foundry.engine,
        tenant_id=ctx.tenant_id,
        record_id="dlqr_api_contract",
        source_event_id="shipment_cdc_events:0:4",
        payload_hash="payload-hash-contract",
        metadata_overrides={"recordDlqKind": "data_quality_contract"},
    )
    monkeypatch.setattr(api_main, "foundry", foundry)
    client = TestClient(app)
    headers = {"X-Tenant-ID": ctx.tenant_id, "X-User-ID": ctx.actor_user_id, "X-Roles": "ops_manager"}

    listing = client.get("/api/operations/dead-letter-records", headers=headers)
    detail = client.get("/api/operations/dead-letter-records/dlqr_api_retry", headers=headers)
    retry = client.post(
        "/api/operations/dead-letter-records/dlqr_api_retry/retry",
        headers={**headers, "Idempotency-Key": "record-replay-key"},
    )
    duplicate = client.post(
        "/api/operations/dead-letter-records/dlqr_api_retry/retry",
        headers={**headers, "Idempotency-Key": "record-replay-key"},
    )
    first_preview = client.get("/api/datasets/raw/shipment_cdc/preview", headers=headers).json()
    bulk = client.post(
        "/api/operations/dead-letter-records/bulk-retry",
        headers={**headers, "Idempotency-Key": "bulk-record-replay-key"},
        json={"ids": ["dlqr_api_bulk", "dlqr_api_contract", "dlqr_api_missing"]},
    )
    discard = client.post("/api/operations/dead-letter-records/dlqr_api_discard/discard", headers=headers)
    audits = client.get("/api/operations/runs?runType=audit", headers=headers).json()["auditEvents"]
    preview = client.get("/api/datasets/raw/shipment_cdc/preview", headers=headers).json()

    assert listing.status_code == 200
    assert {row["id"] for row in listing.json()} == {
        "dlqr_api_retry",
        "dlqr_api_bulk",
        "dlqr_api_discard",
        "dlqr_api_contract",
    }
    assert detail.json()["source_event_id"] == "shipment_cdc_events:0:1"
    assert retry.status_code == 200
    assert retry.json()["replayStatus"] == "SUCCEEDED"
    assert retry.json()["replayDatasetVersionId"] is not None
    assert duplicate.json()["is_idempotent_replay"] is True
    assert duplicate.json()["replayRunId"] == retry.json()["replayRunId"]
    assert duplicate.json()["replayDatasetVersionId"] == retry.json()["replayDatasetVersionId"]
    assert bulk.json()["status"] == "PARTIAL_SUCCESS"
    assert bulk.json()["succeededCount"] == 1
    assert bulk.json()["failedCount"] == 2
    assert bulk.json()["items"][0]["deadLetterRecordId"] == "dlqr_api_bulk"
    assert bulk.json()["items"][0]["replayStatus"] == "SUCCEEDED"
    assert {item["deadLetterRecordId"] for item in bulk.json()["failedItems"]} == {
        "dlqr_api_contract",
        "dlqr_api_missing",
    }
    assert discard.json()["status"] == "DISCARDED"
    assert first_preview[0]["event_id"] == "shipment_cdc_events:0:1"
    assert preview[0]["event_id"] == "shipment_cdc_events:0:2"
    assert any(row["event_type"] == "dead_letter_record.replay_requested" for row in audits)
    assert any(row["event_type"] == "dead_letter_record.replayed" for row in audits)
    assert any(row["event_type"] == "dead_letter_record.bulk_replay_item_failed" for row in audits)
    assert any(row["event_type"] == "dead_letter_record.discarded" for row in audits)


def test_api_operations_workflow_start_status_and_audit(foundry, monkeypatch) -> None:
    ctx = demo_admin_context()
    headers = {"X-User-ID": ctx.actor_user_id, "X-Roles": ",".join(ctx.roles)}
    foundry.datasets.ensure("raw.workflow_orders", ctx=ctx, primary_key=["order_id"])
    monkeypatch.setattr(api_main, "foundry", foundry)
    client = TestClient(app)

    started = client.post(
        "/api/operations/workflows/connector-sync/start",
        headers={**headers, "Idempotency-Key": "api-workflow-start"},
        json={
            "datasetRef": "raw.workflow_orders",
            "connectorName": "rest",
            "resourceName": "orders",
        },
    )
    fetched = client.get(f"/api/operations/workflows/{started.json()['workflowRunId']}", headers=headers)
    detail = client.get(f"/api/operations/runs/audit/{started.json()['foundryRunId']}", headers=headers)

    assert started.status_code == 200
    assert started.json()["workflowRunId"].startswith("flite:")
    assert started.json()["workflowRunId"] != "api-workflow-start"
    assert started.json()["idempotencyKey"] == "api-workflow-start"
    assert started.json()["workflowName"] == "ConnectorSyncWorkflow"
    assert started.json()["workflowProfile"] == "local-workflow"
    assert started.json()["output"]["datasetRef"] == "raw.workflow_orders"
    assert fetched.status_code == 200
    assert fetched.json()["workflowRunId"] == started.json()["workflowRunId"]
    assert fetched.json()["idempotencyKey"] == "api-workflow-start"
    assert detail.status_code == 200
    assert detail.json()["row"]["after_ref"]["workflowRunId"] == started.json()["workflowRunId"]


def test_api_operations_reconciliation_resolves_action_writeback(foundry, monkeypatch) -> None:
    ctx = prepare_indexed_demo(foundry)
    headers = {"X-User-ID": ctx.actor_user_id, "X-Roles": ",".join(ctx.roles)}
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    idempotency_key = "api-reconcile-writeback"
    with pytest.raises(ExternalOutcomeUnknown):
        foundry.actions.apply(
            "ApproveOrder",
            object_type="Order",
            object_id="O-1001",
            expected_object_version=order["objectVersion"],
            params={"reason": "API remote success"},
            idempotency_key=idempotency_key,
            simulate_writeback_outcome_unknown=True,
            ctx=ctx,
        )
    writeback = [
        row
        for row in foundry.operations.list_runs(ctx=ctx)["actionWritebacks"]
        if row["idempotency_key"] == idempotency_key
    ][0]
    monkeypatch.setattr(api_main, "foundry", foundry)

    response = TestClient(app).post(
        f"/api/operations/reconciliation/{writeback['id']}/resolve",
        headers=headers,
        json={
            "remoteStatus": "succeeded",
            "remoteResourceId": f"mock-resource-{idempotency_key}",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "reconciled"
    assert response.json()["writebackId"] == writeback["id"]
    assert foundry.objects.get("Order", "O-1001", ctx=ctx)["properties"]["status"] == "APPROVED"


def test_api_operation_errors_preserve_request_id(monkeypatch) -> None:
    class _Datasets:
        def list_versions(self, *_args, **_kwargs):
            raise NotFound("dataset not found")

    class _Operations:
        def query_runs(self, **_kwargs):
            raise ValidationFailed("invalid run filter")

        def run_detail(self, *_args, **_kwargs):
            raise NotFound("operations run not found")

        def list_dead_letter_records(self, **_kwargs):
            raise ValidationFailed("invalid record DLQ filter")

        def dead_letter_record(self, *_args, **_kwargs):
            raise NotFound("dead-letter record not found")

        def retry_dead_letter_record(self, *_args, **_kwargs):
            raise ValidationFailed("dead-letter record is not retryable")

        def bulk_retry_dead_letter_records(self, *_args, **_kwargs):
            raise ValidationFailed("record ids are required for bulk replay")

        def discard_dead_letter_record(self, *_args, **_kwargs):
            raise ValidationFailed("dead-letter record replay is already requested")

        def retry_dead_letter_event(self, *_args, **_kwargs):
            raise ValidationFailed("dead-letter event is not retryable")

        def plan_iceberg_maintenance(self, *_args, **_kwargs):
            raise ValidationFailed("Iceberg maintenance is unavailable")

        def restore_preflight_report(self, *_args, **_kwargs):
            raise ValidationFailed("restore preflight is unavailable")

        def start_restore_mode(self, *_args, **_kwargs):
            raise ValidationFailed("restore mode is unavailable")

        def restore_mode_status(self, *_args, **_kwargs):
            raise ValidationFailed("restore mode status is unavailable")

        def approve_restore_resume(self, *_args, **_kwargs):
            raise ValidationFailed("restore resume approval is unavailable")

        def start_connector_sync_workflow(self, *_args, **_kwargs):
            raise ValidationFailed("workflow start is unavailable")

        def product_workflow_run(self, *_args, **_kwargs):
            raise NotFound("workflow run not found")

    class _Objects:
        def reindex(self, *_args, **_kwargs):
            raise NotFound("object type not found")

        def replay_index_run(self, *_args, **_kwargs):
            raise ValidationFailed("index run is not failed")

        def links(self, *_args, **_kwargs):
            raise NotFound("object link type not found")

        def query(self, *_args, **_kwargs):
            raise ValidationFailed("invalid object query")

    class _Transforms:
        def retry_run(self, *_args, **_kwargs):
            raise ValidationFailed("transform run is not failed")

    class FailingCore:
        datasets = _Datasets()
        operations = _Operations()
        objects = _Objects()
        transforms = _Transforms()

    monkeypatch.setattr(api_main, "foundry", FailingCore())
    client = TestClient(app)

    listing = client.get("/api/operations/runs", params={"runType": "missing"})
    assert listing.status_code == 400
    assert "request_id" in listing.json()["detail"]

    detail = client.get("/api/operations/runs/action/missing")
    assert detail.status_code == 404
    assert "request_id" in detail.json()["detail"]

    dataset_versions = client.get("/api/datasets/missing/orders/versions")
    assert dataset_versions.status_code == 404
    assert "request_id" in dataset_versions.json()["detail"]

    links = client.get("/api/objects/Order/O-missing/links/MissingLink")
    assert links.status_code == 404
    assert "request_id" in links.json()["detail"]

    object_query = client.post("/api/objects/Order/query", json={"filter": {"bad": "shape"}})
    assert object_query.status_code == 400
    assert "request_id" in object_query.json()["detail"]

    record_list = client.get("/api/operations/dead-letter-records")
    assert record_list.status_code == 400
    assert "request_id" in record_list.json()["detail"]

    record_detail = client.get("/api/operations/dead-letter-records/missing")
    assert record_detail.status_code == 404
    assert "request_id" in record_detail.json()["detail"]

    record_retry = client.post(
        "/api/operations/dead-letter-records/missing/retry",
        headers={"Idempotency-Key": "record-error-key"},
    )
    assert record_retry.status_code == 400
    assert "request_id" in record_retry.json()["detail"]

    record_bulk_retry = client.post(
        "/api/operations/dead-letter-records/bulk-retry",
        headers={"Idempotency-Key": "record-error-bulk-key"},
        json={"ids": ["missing"]},
    )
    assert record_bulk_retry.status_code == 400
    assert "request_id" in record_bulk_retry.json()["detail"]

    record_discard = client.post("/api/operations/dead-letter-records/missing/discard")
    assert record_discard.status_code == 400
    assert "request_id" in record_discard.json()["detail"]

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

    maintenance = client.post("/api/operations/maintenance/iceberg/raw.orders/plan")
    assert maintenance.status_code == 400
    assert "request_id" in maintenance.json()["detail"]

    restore_preflight = client.post("/api/operations/backup-restore/preflight", json={"backupId": "backup-error"})
    assert restore_preflight.status_code == 400
    assert "request_id" in restore_preflight.json()["detail"]

    restore_start = client.post(
        "/api/operations/backup-restore/restore-mode/start",
        json={"backupId": "backup-error", "restoreId": "restore-error"},
    )
    assert restore_start.status_code == 400
    assert "request_id" in restore_start.json()["detail"]

    restore_status = client.get("/api/operations/backup-restore/restore-mode/restore-error")
    assert restore_status.status_code == 400
    assert "request_id" in restore_status.json()["detail"]

    restore_approval = client.post(
        "/api/operations/backup-restore/restore-mode/restore-error/approve-resume",
        json={"validationId": "restore-error-validation"},
    )
    assert restore_approval.status_code == 400
    assert "request_id" in restore_approval.json()["detail"]

    workflow_start = client.post(
        "/api/operations/workflows/connector-sync/start",
        headers={"Idempotency-Key": "workflow-error-key"},
        json={"datasetRef": "raw.orders", "connectorName": "rest", "resourceName": "orders"},
    )
    assert workflow_start.status_code == 400
    assert "request_id" in workflow_start.json()["detail"]

    workflow_detail = client.get("/api/operations/workflows/missing")
    assert workflow_detail.status_code == 404
    assert "request_id" in workflow_detail.json()["detail"]


def test_api_object_set_errors_preserve_request_id(monkeypatch) -> None:
    class _Objects:
        def query_sets(self, **_kwargs):
            raise NotFound("object type not found")

        def create_set(self, *_args, **_kwargs):
            raise ValidationFailed("invalid object set")

    class FailingCore:
        objects = _Objects()

    monkeypatch.setattr(api_main, "foundry", FailingCore())
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

    monkeypatch.setenv("FOUNDRY_LITE_DB_URL", "postgresql://prod.example/foundry")
    with pytest.raises(SystemExit, match="Refusing fresh supply-chain demo reset"):
        _fresh_supply_chain_demo(args)

    args = type("Args", (), {"group": "demo", "command": "run-supply-chain", "fresh": False, "reuse_state": True})()
    assert _fresh_supply_chain_demo(args) is False

    monkeypatch.setenv("FOUNDRY_LITE_DB_URL", "sqlite:///tmp/foundry-lite-demo.db")
    args = type("Args", (), {"group": "demo", "command": "run-supply-chain", "fresh": True, "reuse_state": False})()
    assert _fresh_supply_chain_demo(args) is True

    monkeypatch.delenv("FOUNDRY_LITE_DB_URL", raising=False)
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


def _approve_order_for_materialization(foundry, ctx, *, idempotency_key: str) -> None:
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    foundry.actions.apply(
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


def test_cli_dispatch_rejects_unsupported_command(foundry) -> None:
    args = type("Args", (), {"group": "unknown", "command": "nope"})()
    with pytest.raises(SystemExit):
        _dispatch(foundry, demo_admin_context(), args)


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


def _restore_mode_payload(tenant_id: str, *, status: str = "paused") -> dict[str, object]:
    is_resumed = status == "resume_approved"
    return {
        "generatedAt": "2026-06-19T01:00:00Z",
        "tenantId": tenant_id,
        "restoreId": "restore-api",
        "backupId": "backup-api",
        "status": status,
        "preflightStatus": "ready",
        "is_write_traffic_paused": not is_resumed,
        "is_outbox_publisher_paused": not is_resumed,
        "is_serving_traffic_open": is_resumed,
        "is_post_restore_validation_required": not is_resumed,
        "is_operator_approval_required": not is_resumed,
        "blockingIssueCount": 0,
        "reason": "restore mode started with write traffic and outbox publisher paused",
        "highWatermarks": {},
        "summary": {"activeRestoreMode": not is_resumed},
    }


def _seed_dead_letter_record(
    engine,
    *,
    tenant_id: str,
    record_id: str,
    source_event_id: str = "shipment_cdc_events:0:1",
    payload_hash: str = "payload-hash-api",
    metadata_overrides: Mapping[str, object] | None = None,
) -> None:
    offset = int(source_event_id.rsplit(":", maxsplit=1)[-1])
    metadata = {
        "dataset_id": "ds_shipment_cdc",
        "dataset_ref": "raw.shipment_cdc",
        "schema_strategy": "cdc_envelope_json",
        "stream": "shipment_cdc",
        "topic": "shipment_cdc_events",
        "consumer_group": "foundry-lite-archive",
        "partition": 0,
        "offset": offset,
        "event_type": "shipment.changed",
        "event_key": record_id,
        "request_id": "api-seed",
    }
    metadata.update(dict(metadata_overrides or {}))
    with engine.begin() as conn:
        conn.execute(
            insert(db.dead_letter_records).values(
                id=record_id,
                tenant_id=tenant_id,
                source_event_id=source_event_id,
                source_dataset_version_id=None,
                source_run_id="sync_stream_api",
                payload={
                    "op": "u",
                    "pk": {"shipment_id": record_id},
                    "before": None,
                    "after": {"shipment_id": record_id, "status": "IN_TRANSIT"},
                    "ordering": {"lsn": record_id},
                },
                payload_hash=payload_hash,
                schema_version=1,
                transform_version=None,
                error_kind="VALIDATION_FAILED",
                error_message="cdc envelope field must be an object",
                event_time=None,
                ingested_at="2026-06-10T00:03:00Z",
                first_failed_at="2026-06-10T00:03:00Z",
                attempts=1,
                status="QUARANTINED",
                replay_status="NOT_REQUESTED",
                replay_run_id=None,
                replay_idempotency_key=None,
                replay_requested_at=None,
                discarded_at=None,
                backfill_plan=None,
                is_closed_partition_affected=False,
                metadata=metadata,
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
        definition_snapshot = dict(source["definition_snapshot"])
        conn.execute(
            insert(db.transform_runs).values(
                id=run_id,
                tenant_id=tenant_id,
                transform_id=source["transform_id"],
                status="FAILED",
                input_versions=input_versions,
                definition_snapshot=definition_snapshot,
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


def _audit_event_types(engine, resource_type: str) -> list[str]:
    with engine.begin() as conn:
        rows = (
            conn.execute(
                select(db.audit_events.c.event_type)
                .where(db.audit_events.c.resource_type == resource_type)
                .order_by(db.audit_events.c.created_at, db.audit_events.c.id)
            )
            .scalars()
            .all()
        )
    return [str(row) for row in rows]
