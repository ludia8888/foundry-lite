"""HTTP contract for governed Action effect operator controls."""

from __future__ import annotations

from fastapi.testclient import TestClient
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app


def test_action_effect_routes_preserve_filters_idempotency_and_reconciliation_evidence(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class FakeActions:
        def list_effect_receipts(self, **kwargs):
            calls.append(("list", (kwargs["status"], kwargs["cursor"], kwargs["limit"])))
            return {"items": [_effect()], "nextCursor": "next-effect"}

        def get_effect_receipt(self, receipt_id, **_kwargs):
            calls.append(("get", receipt_id))
            return _effect()

        def cancel_effect(self, receipt_id, **kwargs):
            calls.append(("cancel", (receipt_id, kwargs["reason"], kwargs["idempotency_key"])))
            return {**_effect(), "status": "cancelled"}

        def retry_effect(self, receipt_id, **kwargs):
            calls.append(("retry", (receipt_id, kwargs["idempotency_key"])))
            return {**_effect(), "status": "retry_wait"}

        def reconcile_effect(self, receipt_id, **kwargs):
            calls.append(
                (
                    "reconcile",
                    (receipt_id, kwargs["resolution"], kwargs["evidence"], kwargs["idempotency_key"]),
                )
            )
            return {**_effect(), "status": "succeeded"}

    class FakeFoundry:
        actions = FakeActions()

    monkeypatch.setattr(api_runtime, "foundry", FakeFoundry())
    client = TestClient(app)
    listed = client.get("/api/actions/effects?status=dead_letter&cursor=prior&limit=25")
    fetched = client.get("/api/actions/effects/receipt-1")
    cancelled = client.post(
        "/api/actions/effects/receipt-1/cancel",
        json={"reason": "obsolete"},
        headers={"Idempotency-Key": "cancel-1"},
    )
    retried = client.post(
        "/api/actions/effects/receipt-1/retry",
        headers={"Idempotency-Key": "retry-1"},
    )
    reconciled = client.post(
        "/api/actions/effects/receipt-1/reconcile",
        json={
            "resolution": "confirmed_delivered",
            "evidence": {
                "verificationMethod": "provider_query",
                "providerReference": "case-1",
                "verifiedAt": "2026-08-05T12:00:00Z",
                "externalExecutionId": "provider-1",
            },
        },
        headers={"Idempotency-Key": "reconcile-1"},
    )

    assert listed.json()["nextCursor"] == "next-effect"
    assert fetched.json()["receiptId"] == "receipt-1"
    assert cancelled.status_code == 202 and cancelled.json()["status"] == "cancelled"
    assert retried.status_code == 202 and retried.json()["status"] == "retry_wait"
    assert reconciled.json()["status"] == "succeeded"
    assert calls == [
        ("list", ("dead_letter", "prior", 25)),
        ("get", "receipt-1"),
        ("cancel", ("receipt-1", "obsolete", "cancel-1")),
        ("retry", ("receipt-1", "retry-1")),
        (
            "reconcile",
            (
                "receipt-1",
                "confirmed_delivered",
                {
                    "verificationMethod": "provider_query",
                    "providerReference": "case-1",
                    "verifiedAt": "2026-08-05T12:00:00Z",
                    "externalExecutionId": "provider-1",
                },
                "reconcile-1",
            ),
        ),
    ]


def _effect() -> dict[str, object]:
    return {
        "receiptId": "receipt-1",
        "actionRunId": "run-1",
        "effectId": "notify-ops",
        "phase": "after_commit",
        "kind": "notification",
        "targetRef": "notification-policy:operations",
        "status": "dead_letter",
    }
