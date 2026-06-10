from __future__ import annotations

import json
import os

from fastapi.testclient import TestClient
from foundry_lite_api import main as api_main
from foundry_lite_api.main import app, healthz
from foundry_lite_cli.main import main

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


def test_cli_demo_seed_smoke(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_HOME", str(tmp_path / "cli"))
    main(["demo", "seed"])
    assert os.path.exists("examples/supply-chain-demo/README.md")


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
