"""Every Action route must translate a domain error into its HTTP contract.

Each handler wraps its facade call in `except FoundryLiteError: raise _handle_error(...)`. Those
branches were the single largest uncovered region in the API layer, and they are exactly the
branches a caller hits on a bad day: an unknown action, a lost optimistic-concurrency race, a
denied permission. An untested error path is how a 409 silently becomes a 500 — the client
retries a conflict it should have surfaced, or gives up on a conflict it could have resolved.

These tests drive each route through a facade that raises, and assert the mapped status code
rather than the happy path (covered elsewhere).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from foundry_lite.domain.errors import (
    ConflictDetected,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app

_HEADERS = {
    "X-Tenant-ID": "tenant-demo",
    "X-User-ID": "u-1",
    "X-Roles": "admin",
    "Idempotency-Key": "error-path",
}
_TARGET = {
    "target": {"objectType": "Order", "objectId": "O-1"},
    "expectedObjectVersion": 1,
    "params": {"reason": "why"},
}


class _RaisingActions:
    """Facade whose every method raises the configured domain error."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def __getattr__(self, _name: str) -> Any:
        def call(*_args: object, **_kwargs: object) -> object:
            raise self.error

        return call


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _install(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    monkeypatch.setattr(api_runtime, "foundry", SimpleNamespace(actions=_RaisingActions(error)))


_ROUTES: tuple[tuple[str, str, str, dict[str, object] | None], ...] = (
    ("cancel_run", "POST", "/api/actions/runs/run-1/cancel", {"reason": "stop"}),
    ("logs", "GET", "/api/actions/logs", None),
    ("revert_eligibility", "GET", "/api/actions/runs/run-1/revert-eligibility", None),
    ("revert", "POST", "/api/actions/runs/run-1/revert", {}),
    ("branch_diff", "GET", "/api/actions/branches/br-1/diff", None),
    ("branch_object", "GET", "/api/actions/branches/br-1/objects/Order/O-1", None),
    ("notification_policies", "GET", "/api/actions/notification-policies", None),
    ("schema", "GET", "/api/actions/ApproveOrder/schema", None),
    ("get", "GET", "/api/actions/ApproveOrder", None),
    ("plan", "POST", "/api/actions/ApproveOrder/plan", _TARGET),
    ("dry_run", "POST", "/api/actions/ApproveOrder/dry-run", _TARGET),
    ("apply", "POST", "/api/actions/ApproveOrder/apply", _TARGET),
    ("start_run", "POST", "/api/actions/ApproveOrder/runs", _TARGET),
)

_ERRORS: tuple[tuple[Exception, int], ...] = (
    (NotFound("missing"), 404),
    (PermissionDenied("denied"), 403),
    (ConflictDetected("version moved"), 409),
    (ValidationFailed("bad input"), 400),
)


@pytest.mark.parametrize(("label", "method", "path", "body"), _ROUTES, ids=[route[0] for route in _ROUTES])
@pytest.mark.parametrize(("error", "expected_status"), _ERRORS, ids=[type(err[0]).__name__ for err in _ERRORS])
def test_action_route_maps_domain_error_to_its_status(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    method: str,
    path: str,
    body: dict[str, object] | None,
    error: Exception,
    expected_status: int,
) -> None:
    _install(monkeypatch, error)

    response = client.request(method, path, headers=_HEADERS, json=body)

    assert response.status_code == expected_status, f"{label} mapped {error!r} to {response.status_code}"
    assert "detail" in response.json()


def test_branch_run_takes_the_branch_path_and_answers_200_not_202(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A branch execution commits into an overlay, so it is not an accepted async run."""
    calls: list[str] = []

    class _BranchActions:
        def execute_branch(self, action_type: str, **kwargs: object) -> dict[str, object]:
            calls.append(action_type)
            return {"actionRunId": "run-1", "status": "SUCCEEDED", "branchId": kwargs["branch_id"]}

        def start_run(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("a branch request must not start a main-ontology run")

    monkeypatch.setattr(api_runtime, "foundry", SimpleNamespace(actions=_BranchActions()))

    response = client.post(
        "/api/actions/ApproveOrder/runs",
        headers=_HEADERS,
        json={**_TARGET, "branchId": "br-1"},
    )

    assert response.status_code == 200
    assert response.json()["branchId"] == "br-1"
    assert calls == ["ApproveOrder"]


def test_run_without_a_branch_is_accepted_for_async_execution(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _RunActions:
        def start_run(self, _action_type: str, **_kwargs: object) -> dict[str, object]:
            return {"actionRunId": "run-1", "status": "QUEUED"}

        def execute_branch(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("no branch was requested")

    monkeypatch.setattr(api_runtime, "foundry", SimpleNamespace(actions=_RunActions()))

    response = client.post("/api/actions/ApproveOrder/runs", headers=_HEADERS, json=_TARGET)

    assert response.status_code == 202
    assert response.json()["status"] == "QUEUED"
