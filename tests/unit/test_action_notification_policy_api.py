"""HTTP contract for no-code Action notification policy management."""

from __future__ import annotations

from fastapi.testclient import TestClient
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app


def test_notification_policy_routes_preserve_cas_and_idempotency(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class FakeActions:
        def list_notification_policies(self, **kwargs):
            calls.append(("list", (kwargs["cursor"], kwargs["limit"])))
            return {"items": [_policy()], "nextCursor": "next-policy"}

        def get_notification_policy(self, policy_name, **_kwargs):
            calls.append(("get", policy_name))
            return _policy()

        def create_notification_policy(self, policy_name, **kwargs):
            calls.append(("create", (policy_name, kwargs["idempotency_key"], kwargs["recipients"])))
            return _policy()

        def update_notification_policy(self, policy_name, **kwargs):
            calls.append(("update", (policy_name, kwargs["expected_fingerprint"], kwargs["idempotency_key"])))
            return {**_policy(), "version": 2, "configFingerprint": "sha256:v2"}

        def disable_notification_policy(self, policy_name, **kwargs):
            calls.append(("disable", (policy_name, kwargs["expected_fingerprint"], kwargs["idempotency_key"])))
            return {**_policy(), "status": "disabled", "version": 2}

    class FakeFoundry:
        actions = FakeActions()

    monkeypatch.setattr(api_runtime, "foundry", FakeFoundry())
    client = TestClient(app)
    created = client.post(
        "/api/actions/notification-policies",
        json={
            "policyName": "operations",
            "displayName": "Operations",
            "deliveryMode": "strict",
            "recipients": [{"userId": "operator-1", "roles": ["ops_manager"]}],
        },
        headers={"Idempotency-Key": "create-1"},
    )
    listed = client.get("/api/actions/notification-policies?cursor=prior&limit=25")
    fetched = client.get("/api/actions/notification-policies/operations")
    updated = client.put(
        "/api/actions/notification-policies/operations",
        json={
            "displayName": "Operations",
            "deliveryMode": "best_effort",
            "recipients": [{"userId": "operator-1", "roles": ["ops_manager"]}],
            "status": "active",
            "expectedFingerprint": "sha256:v1",
        },
        headers={"Idempotency-Key": "update-1"},
    )
    disabled = client.request(
        "DELETE",
        "/api/actions/notification-policies/operations",
        json={"expectedFingerprint": "sha256:v2"},
        headers={"Idempotency-Key": "disable-1"},
    )
    assert created.status_code == 201
    assert listed.json()["nextCursor"] == "next-policy"
    assert fetched.json()["targetRef"] == "notification-policy:operations"
    assert updated.json()["version"] == 2
    assert disabled.json()["status"] == "disabled"
    assert calls == [
        ("create", ("operations", "create-1", [{"userId": "operator-1", "roles": ["ops_manager"]}])),
        ("list", ("prior", 25)),
        ("get", "operations"),
        ("update", ("operations", "sha256:v1", "update-1")),
        ("disable", ("operations", "sha256:v2", "disable-1")),
    ]


def _policy() -> dict[str, object]:
    return {
        "id": "policy-1",
        "policyName": "operations",
        "targetRef": "notification-policy:operations",
        "displayName": "Operations",
        "deliveryMode": "strict",
        "recipients": [{"userId": "operator-1", "roles": ["ops_manager"]}],
        "status": "active",
        "version": 1,
        "configFingerprint": "sha256:v1",
        "createdByUserId": "admin-1",
        "updatedByUserId": "admin-1",
        "createdAt": "2026-08-05T00:00:00Z",
        "updatedAt": "2026-08-05T00:00:00Z",
    }
