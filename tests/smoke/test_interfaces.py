from __future__ import annotations

import os

from fastapi.testclient import TestClient
from foundry_lite_api.main import app, healthz
from foundry_lite_cli.main import main


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


def test_cli_demo_seed_smoke(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_HOME", str(tmp_path / "cli"))
    main(["demo", "seed"])
    assert os.path.exists("examples/supply-chain-demo/README.md")
