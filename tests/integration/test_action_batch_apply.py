"""Batch action apply: atomic multi-target mutation via the public facades."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ConflictDetected, PermissionDenied, ValidationFailed
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app

from tests.conftest import DEMO_ROOT, prepare_indexed_demo

WRITEBACK_BLOCK = (
    "    writebacks:\n"
    "      - apiName: erpApproveOrder\n"
    "        mode: beforeCommit\n"
    "        connector: mock_erp_simulator\n"
)


def _batch_ontology(tmp_path: Path) -> Path:
    """Demo ontology with the ApproveOrder ERP writeback removed.

    Batch apply intentionally rejects writeback-declaring actions, so the
    batch-eligible variant of the demo drops that block while keeping the
    permissions, preconditions, and mutations identical.
    """
    ontology = (DEMO_ROOT / "ontology" / "order-customer.yaml").read_text(encoding="utf-8")
    assert WRITEBACK_BLOCK in ontology
    path = tmp_path / "order-customer-batch.yaml"
    path.write_text(ontology.replace(WRITEBACK_BLOCK, "", 1), encoding="utf-8")
    return path


def _ingest_batch_ontology(tmp_path: Path) -> Path:
    """Batch ontology plus a YAML-only action granted to connector_ingest.

    connector_ingest has execute through allowedRoles but no object:read, so
    batch apply must fail closed on the target read check.
    """
    ontology = _batch_ontology(tmp_path).read_text(encoding="utf-8")
    ontology = f"""{ontology}
  - apiName: BatchExpediteOrder
    displayName: Batch expedite order
    target: Order
    parameters:
      - apiName: reason
        type: string
        required: true
    permissions:
      allowedRoles: [connector_ingest]
    mutations:
      - type: setProperty
        property: operatorNote
        valueFrom: params.reason
"""
    path = tmp_path / "order-customer-batch-ingest.yaml"
    path.write_text(ontology, encoding="utf-8")
    return path


def _prepare_demo_with_ontology(foundry: FoundryLite, ontology_path: Path) -> RequestContext:
    ctx = demo_admin_context()
    foundry.demo.seed_files()
    foundry.datasets.ensure("raw.erp_orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("raw.crm_customers", ctx=ctx, primary_key=["customer_id"])
    foundry.datasets.ensure("clean.orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.ensure("clean.customers", ctx=ctx, primary_key=["customer_id"])
    foundry.demo.register_transforms(ctx)
    foundry.datasets.upload_csv("raw.erp_orders", str(DEMO_ROOT / "data" / "orders.csv"), ctx=ctx)
    foundry.datasets.upload_csv("raw.crm_customers", str(DEMO_ROOT / "data" / "customers.csv"), ctx=ctx)
    foundry.transforms.run("clean_orders", ctx=ctx)
    foundry.transforms.run("clean_customers", ctx=ctx)
    foundry.ontology.apply(str(ontology_path), ctx=ctx)
    foundry.objects.reindex("Order", ctx=ctx)
    return ctx


def _rows_for_key(runs: Mapping[str, object], collection: str, idempotency_key: str) -> list[Mapping[str, object]]:
    rows = cast(list[Mapping[str, object]], runs[collection])
    return [row for row in rows if row.get("idempotency_key") == idempotency_key]


def _batch_edit_rows(runs: Mapping[str, object], idempotency_key: str) -> list[Mapping[str, object]]:
    rows = cast(list[Mapping[str, object]], runs["objectEdits"])
    return [row for row in rows if str(row.get("idempotency_key", "")).startswith(f"{idempotency_key}:")]


def test_batch_apply_edits_multiple_orders_atomically_with_audit_evidence(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = _prepare_demo_with_ontology(foundry, _batch_ontology(tmp_path))
    first = foundry.objects.get("Order", "O-1001", ctx=ctx)
    second = foundry.objects.get("Order", "O-1002", ctx=ctx)

    response = foundry.actions.apply_batch(
        "ApproveOrder",
        object_type="Order",
        targets=[
            {"object_id": "O-1001", "expected_object_version": first["objectVersion"]},
            {"object_id": "O-1002", "expected_object_version": second["objectVersion"]},
        ],
        params={"reason": "Batch inventory confirmed"},
        idempotency_key="batch-approve-happy",
        ctx=ctx,
    )

    results = {item["objectId"]: item for item in response["results"]}
    approved_first = foundry.objects.get("Order", "O-1001", ctx=ctx)
    approved_second = foundry.objects.get("Order", "O-1002", ctx=ctx)
    runs = foundry.operations.list_runs(ctx=ctx)
    run_rows = _rows_for_key(runs, "actionRuns", "batch-approve-happy")
    edit_rows = _batch_edit_rows(runs, "batch-approve-happy")

    assert response["status"] == "succeeded"
    assert response["objectType"] == "Order"
    assert set(results) == {"O-1001", "O-1002"}
    assert results["O-1001"]["newObjectVersion"] == first["objectVersion"] + 1
    assert results["O-1002"]["newObjectVersion"] == second["objectVersion"] + 1
    assert results["O-1001"]["patch"]["status"] == "APPROVED"
    cache_refresh = response["cacheRefresh"]
    assert set(cache_refresh["objectKeys"]) == {"objects:Order:O-1001", "objects:Order:O-1002"}
    assert cache_refresh["actionRunKeys"] == [f"actions:runs:{response['actionRunId']}"]

    # Read-your-writes: both targets are visible with the new state and version.
    for approved, before in ((approved_first, first), (approved_second, second)):
        assert approved["properties"]["status"] == "APPROVED"
        assert approved["properties"]["operatorNote"] == "Batch inventory confirmed"
        assert approved["objectVersion"] == before["objectVersion"] + 1

    # One batch run row, terminal succeeded, with the response snapshot stored.
    assert len(run_rows) == 1
    assert run_rows[0]["status"] == "succeeded"
    stored_result = cast(Mapping[str, object], run_rows[0]["result"])
    assert len(cast(list[object], stored_result["results"])) == 2

    # Per-target object edits carry idempotency keys derived from the batch key.
    assert {str(row["idempotency_key"]) for row in edit_rows} == {
        "batch-approve-happy:O-1001",
        "batch-approve-happy:O-1002",
    }
    assert {str(row["object_id"]) for row in edit_rows} == {"O-1001", "O-1002"}

    audit_events = cast(list[Mapping[str, object]], runs["auditEvents"])
    outbox_events = cast(list[Mapping[str, object]], runs["outboxEvents"])
    assert any(
        event["event_type"] == "action.run.committed" and event["resource_id"] == response["actionRunId"]
        for event in audit_events
    )
    assert {
        str(event["aggregate_id"])
        for event in outbox_events
        if event["event_type"] == "object.edit.committed" and event["correlation_id"] == response["actionRunId"]
    } == {"O-1001", "O-1002"}
    assert any(
        event["event_type"] == "action.run.committed" and event["aggregate_id"] == response["actionRunId"]
        for event in outbox_events
    )


def test_batch_apply_rejects_stale_target_and_leaves_every_object_unchanged(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = _prepare_demo_with_ontology(foundry, _batch_ontology(tmp_path))
    first = foundry.objects.get("Order", "O-1001", ctx=ctx)
    second = foundry.objects.get("Order", "O-1002", ctx=ctx)

    with pytest.raises(ConflictDetected) as excinfo:
        foundry.actions.apply_batch(
            "ApproveOrder",
            object_type="Order",
            targets=[
                {"object_id": "O-1001", "expected_object_version": first["objectVersion"]},
                {"object_id": "O-1002", "expected_object_version": second["objectVersion"] + 1},
            ],
            params={"reason": "Half stale batch"},
            idempotency_key="batch-approve-stale",
            ctx=ctx,
        )

    failures = cast(list[Mapping[str, object]], excinfo.value.details["failures"])
    after_first = foundry.objects.get("Order", "O-1001", ctx=ctx)
    after_second = foundry.objects.get("Order", "O-1002", ctx=ctx)
    runs = foundry.operations.list_runs(ctx=ctx)
    run_rows = _rows_for_key(runs, "actionRuns", "batch-approve-stale")

    assert [failure["objectId"] for failure in failures] == ["O-1002"]
    assert failures[0]["code"] == "CONFLICT"
    # All-or-nothing: the valid target must not have been edited either.
    assert after_first["objectVersion"] == first["objectVersion"]
    assert after_first["properties"]["status"] == "PENDING"
    assert after_second["objectVersion"] == second["objectVersion"]
    assert len(run_rows) == 1
    assert run_rows[0]["status"] == "conflict"
    assert run_rows[0]["error"] is not None
    assert _batch_edit_rows(runs, "batch-approve-stale") == []
    audit_events = cast(list[Mapping[str, object]], runs["auditEvents"])
    assert any(
        event["event_type"] == "action.run.failed" and event["resource_id"] == run_rows[0]["id"]
        for event in audit_events
    )


def test_batch_apply_replays_idempotently_and_conflicts_on_body_drift(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    ctx = _prepare_demo_with_ontology(foundry, _batch_ontology(tmp_path))
    first = foundry.objects.get("Order", "O-1001", ctx=ctx)
    second = foundry.objects.get("Order", "O-1002", ctx=ctx)
    targets = [
        {"object_id": "O-1001", "expected_object_version": first["objectVersion"]},
        {"object_id": "O-1002", "expected_object_version": second["objectVersion"]},
    ]

    committed = foundry.actions.apply_batch(
        "ApproveOrder",
        object_type="Order",
        targets=targets,
        params={"reason": "Batch replay proof"},
        idempotency_key="batch-approve-replay",
        ctx=ctx,
    )
    replay = foundry.actions.apply_batch(
        "ApproveOrder",
        object_type="Order",
        targets=targets,
        params={"reason": "Batch replay proof"},
        idempotency_key="batch-approve-replay",
        ctx=ctx,
    )

    assert replay["idempotentReplay"] is True
    assert replay["actionRunId"] == committed["actionRunId"]
    assert replay["results"] == committed["results"]
    # Replay must not have edited the objects again.
    assert foundry.objects.get("Order", "O-1001", ctx=ctx)["objectVersion"] == first["objectVersion"] + 1

    with pytest.raises(ConflictDetected) as excinfo:
        foundry.actions.apply_batch(
            "ApproveOrder",
            object_type="Order",
            targets=targets,
            params={"reason": "Different body with reused key"},
            idempotency_key="batch-approve-replay",
            ctx=ctx,
        )

    runs = foundry.operations.list_runs(ctx=ctx)
    audit_events = cast(list[Mapping[str, object]], runs["auditEvents"])
    assert excinfo.value.details["action_run_id"] == committed["actionRunId"]
    assert any(
        event["event_type"] == "action.run.idempotency_conflict" and event["resource_id"] == committed["actionRunId"]
        for event in audit_events
    )


def test_batch_apply_rejects_empty_duplicate_and_oversized_target_lists(foundry: FoundryLite) -> None:
    ctx = demo_admin_context()

    with pytest.raises(ValidationFailed, match="at least one target"):
        foundry.actions.apply_batch(
            "ApproveOrder",
            object_type="Order",
            targets=[],
            params={"reason": "empty"},
            idempotency_key="batch-empty",
            ctx=ctx,
        )
    with pytest.raises(ValidationFailed, match="must be unique") as duplicate_error:
        foundry.actions.apply_batch(
            "ApproveOrder",
            object_type="Order",
            targets=[
                {"object_id": "O-1001", "expected_object_version": 1},
                {"object_id": "O-1001", "expected_object_version": 1},
            ],
            params={"reason": "duplicate"},
            idempotency_key="batch-duplicate",
            ctx=ctx,
        )
    with pytest.raises(ValidationFailed, match="target limit exceeded") as oversized_error:
        foundry.actions.apply_batch(
            "ApproveOrder",
            object_type="Order",
            targets=[{"object_id": f"O-{index}", "expected_object_version": 1} for index in range(1_001)],
            params={"reason": "oversized"},
            idempotency_key="batch-oversized",
            ctx=ctx,
        )

    assert duplicate_error.value.details["duplicateObjectIds"] == ["O-1001"]
    assert oversized_error.value.details == {"targetCount": 1_001, "targetLimit": 1_000}
    # Malformed batches are rejected before any run row exists.
    assert all(
        not _rows_for_key(foundry.operations.list_runs(ctx=ctx), "actionRuns", key)
        for key in ("batch-empty", "batch-duplicate", "batch-oversized")
    )


def test_batch_apply_rejects_writeback_declaring_action(foundry: FoundryLite) -> None:
    # The stock demo ApproveOrder declares an ERP before-commit writeback, so
    # the batch path must refuse it and leave the single-target saga in charge.
    ctx = prepare_indexed_demo(foundry)
    order = foundry.objects.get("Order", "O-1001", ctx=ctx)

    with pytest.raises(ValidationFailed, match="declared writebacks"):
        foundry.actions.apply_batch(
            "ApproveOrder",
            object_type="Order",
            targets=[{"object_id": "O-1001", "expected_object_version": order["objectVersion"]}],
            params={"reason": "Batch with writeback"},
            idempotency_key="batch-writeback-rejected",
            ctx=ctx,
        )

    runs = foundry.operations.list_runs(ctx=ctx)
    unchanged = foundry.objects.get("Order", "O-1001", ctx=ctx)
    assert _rows_for_key(runs, "actionRuns", "batch-writeback-rejected") == []
    assert _batch_edit_rows(runs, "batch-writeback-rejected") == []
    assert unchanged["objectVersion"] == order["objectVersion"]
    assert unchanged["properties"]["status"] == "PENDING"


def test_batch_apply_execute_permission_does_not_bypass_object_read(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    admin_ctx = _prepare_demo_with_ontology(foundry, _ingest_batch_ontology(tmp_path))
    ingest_only = RequestContext(actor_user_id="ingest-1", roles=("connector_ingest",))
    first = foundry.objects.get("Order", "O-1001", ctx=admin_ctx)
    second = foundry.objects.get("Order", "O-1002", ctx=admin_ctx)
    targets = [
        {"object_id": "O-1001", "expected_object_version": first["objectVersion"]},
        {"object_id": "O-1002", "expected_object_version": second["objectVersion"]},
    ]

    with pytest.raises(PermissionDenied) as denied:
        foundry.actions.apply_batch(
            "BatchExpediteOrder",
            object_type="Order",
            targets=targets,
            params={"reason": "cannot read targets"},
            idempotency_key="batch-expedite-no-read",
            ctx=ingest_only,
        )

    # The same execute grant plus a role with object:read may run the batch.
    ingest_reader = RequestContext(actor_user_id="ingest-2", roles=("connector_ingest", "viewer"))
    applied = foundry.actions.apply_batch(
        "BatchExpediteOrder",
        object_type="Order",
        targets=targets,
        params={"reason": "reader may act"},
        idempotency_key="batch-expedite-with-read",
        ctx=ingest_reader,
    )

    runs = foundry.operations.list_runs(ctx=admin_ctx)
    audit_events = cast(list[Mapping[str, object]], runs["auditEvents"])
    assert denied.value.details["permission"] == "object:read"
    assert _rows_for_key(runs, "actionRuns", "batch-expedite-no-read") == []
    assert any(
        event["event_type"] == "permission.denied" and event["resource_id"] == "Order:O-1001" for event in audit_events
    )
    assert applied["status"] == "succeeded"
    assert len(applied["results"]) == 2


def test_api_apply_batch_route_maps_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeActions:
        def apply_batch(self, action_type: str, **kwargs: object) -> dict[str, object]:
            assert action_type == "ApproveOrder"
            assert kwargs["object_type"] == "Order"
            assert kwargs["targets"] == [
                {"object_id": "O-1001", "expected_object_version": 3},
                {"object_id": "O-1002", "expected_object_version": 4},
            ]
            assert kwargs["params"] == {"reason": "Batch over HTTP"}
            assert kwargs["idempotency_key"] == "http-batch-1"
            return {
                "actionRunId": "action_run_batch",
                "status": "succeeded",
                "objectType": "Order",
                "results": [
                    {"objectId": "O-1001", "objectEditId": "edit_1", "newObjectVersion": 4, "patch": {}},
                    {"objectId": "O-1002", "objectEditId": "edit_2", "newObjectVersion": 5, "patch": {}},
                ],
            }

    class FakeFoundry:
        actions = FakeActions()

    monkeypatch.setattr(api_runtime, "foundry", FakeFoundry())
    response = TestClient(app).post(
        "/api/actions/ApproveOrder/apply-batch",
        headers={"Idempotency-Key": "http-batch-1"},
        json={
            "objectType": "Order",
            "targets": [
                {"objectId": "O-1001", "expectedObjectVersion": 3},
                {"objectId": "O-1002", "expectedObjectVersion": 4},
            ],
            "params": {"reason": "Batch over HTTP"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["actionRunId"] == "action_run_batch"
    assert [item["objectId"] for item in body["results"]] == ["O-1001", "O-1002"]
    assert response.headers["X-Request-ID"]


def test_api_apply_batch_route_maps_batch_conflict_to_409(monkeypatch: pytest.MonkeyPatch) -> None:
    class ConflictActions:
        def apply_batch(self, _action_type: str, **_kwargs: object) -> dict[str, object]:
            raise ConflictDetected(
                "batch action apply rejected: target conflicts",
                details={"targetCount": 2, "failures": [{"objectId": "O-1002", "code": "CONFLICT"}]},
            )

    class FakeFoundry:
        actions = ConflictActions()

    monkeypatch.setattr(api_runtime, "foundry", FakeFoundry())
    response = TestClient(app).post(
        "/api/actions/ApproveOrder/apply-batch",
        headers={"Idempotency-Key": "http-batch-conflict"},
        json={
            "objectType": "Order",
            "targets": [{"objectId": "O-1002", "expectedObjectVersion": 1}],
            "params": {"reason": "conflict"},
        },
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "CONFLICT"
    assert detail["details"]["failures"] == [{"objectId": "O-1002", "code": "CONFLICT"}]


def test_api_apply_batch_route_requires_idempotency_key_header(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingActions:
        def apply_batch(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("request without Idempotency-Key must not reach foundry")

    class FakeFoundry:
        actions = FailingActions()

    monkeypatch.setattr(api_runtime, "foundry", FakeFoundry())
    response = TestClient(app).post(
        "/api/actions/ApproveOrder/apply-batch",
        json={
            "objectType": "Order",
            "targets": [{"objectId": "O-1001", "expectedObjectVersion": 1}],
            "params": {"reason": "no header"},
        },
    )

    assert response.status_code == 422
    assert response.headers["X-Request-ID"]
