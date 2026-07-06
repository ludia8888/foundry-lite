from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite_api import main as api_main
from foundry_lite_api import runtime as api_runtime


def test_resource_catalog_api_routes_cover_compass_browser_lifecycle(monkeypatch, tmp_path: Path) -> None:
    foundry = FoundryLite(
        dependencies=create_local_core_dependencies(
            db_url=f"sqlite:///{tmp_path / 'resources-api.db'}",
            storage_root=tmp_path / "flite",
        )
    )
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)
    headers = _admin_headers()

    project = _ok(
        client.post(
            "/api/projects",
            json={"displayName": "Compass Ops", "metadata": {"team": "operations"}},
            headers={**headers, "Idempotency-Key": "api-resources-project"},
        )
    )["project"]
    project_id = project["id"]
    assert _ok(client.get("/api/projects", headers=headers))["projects"][0]["id"] == project_id
    assert _ok(client.get(f"/api/projects/{project_id}", headers=headers))["project"]["rid"].startswith(
        "ri.foundry-lite.project."
    )

    grant = _ok(
        client.put(
            f"/api/projects/{project_id}/grants/role/analyst",
            json={"role": "viewer"},
            headers={**headers, "Idempotency-Key": "api-resources-grant"},
        )
    )["grant"]
    assert grant["role"] == "viewer"
    assert _ok(client.get(f"/api/projects/{project_id}/grants", headers=headers))["grants"][0]["principalId"]

    raw_folder = _ok(
        client.post(
            f"/api/projects/{project_id}/folders",
            json={"displayName": "Raw"},
            headers={**headers, "Idempotency-Key": "api-resources-folder-raw"},
        )
    )["folder"]
    landing_folder = _ok(
        client.post(
            f"/api/projects/{project_id}/folders",
            json={"displayName": "Landing", "parentFolderId": raw_folder["id"]},
            headers={**headers, "Idempotency-Key": "api-resources-folder-landing"},
        )
    )["folder"]
    assert len(_ok(client.get(f"/api/projects/{project_id}/folders", headers=headers))["folders"]) == 2
    assert (
        _ok(
            client.post(
                f"/api/projects/{project_id}/folders/{landing_folder['id']}/move",
                json={"parentFolderId": None},
                headers={**headers, "Idempotency-Key": "api-resources-folder-move"},
            )
        )["folder"]["parentFolderId"]
        is None
    )
    assert (
        _ok(
            client.post(
                f"/api/projects/{project_id}/folders/{landing_folder['id']}/trash",
                headers={**headers, "Idempotency-Key": "api-resources-folder-trash"},
            )
        )["folder"]["status"]
        == "trashed"
    )
    assert (
        _ok(
            client.post(
                f"/api/projects/{project_id}/folders/{landing_folder['id']}/restore",
                headers={**headers, "Idempotency-Key": "api-resources-folder-restore"},
            )
        )["folder"]["status"]
        == "active"
    )
    assert (
        len(
            _ok(
                client.get(
                    f"/api/projects/{project_id}/folders",
                    params={"includeTrashed": True},
                    headers=headers,
                )
            )["folders"]
        )
        == 2
    )

    resource = _ok(
        client.post(
            "/api/resources/register",
            json={
                "resourceType": "dataset",
                "displayName": "raw.orders",
                "projectId": project_id,
                "folderId": raw_folder["id"],
                "sourceSurface": "dataset",
                "sourceRef": "raw.orders",
                "operationsPath": "/datasets/raw/orders",
                "metadata": {"classification": "public"},
            },
            headers={**headers, "Idempotency-Key": "api-resources-register"},
        )
    )["resource"]
    rid = resource["rid"]
    assert (
        _ok(client.get("/api/resources", params={"projectId": project_id}, headers=headers))["items"][0]["rid"] == rid
    )
    assert _ok(client.get(f"/api/resources/{rid}", headers=headers))["resource"]["sourceRef"] == "raw.orders"

    assert (
        _ok(
            client.put(
                f"/api/resources/{rid}/favorite",
                headers={**headers, "Idempotency-Key": "api-resources-favorite"},
            )
        )["resource"]["isFavorite"]
        is True
    )
    assert (
        _ok(
            client.put(
                f"/api/resources/{rid}/favorite",
                headers={**headers, "Idempotency-Key": "api-resources-favorite-again"},
            )
        )["resource"]["isFavorite"]
        is True
    )
    assert (
        _ok(
            client.delete(
                f"/api/resources/{rid}/favorite",
                headers={**headers, "Idempotency-Key": "api-resources-unfavorite"},
            )
        )["resource"]["isFavorite"]
        is False
    )
    assert (
        _ok(
            client.post(
                f"/api/resources/{rid}/move",
                json={"projectId": project_id, "folderId": landing_folder["id"]},
                headers={**headers, "Idempotency-Key": "api-resources-move"},
            )
        )["resource"]["folderId"]
        == landing_folder["id"]
    )
    assert (
        _ok(
            client.get(
                "/api/resources",
                params={"projectId": project_id, "folderId": landing_folder["id"]},
                headers=headers,
            )
        )["items"][0]["rid"]
        == rid
    )
    assert (
        _ok(client.get("/api/resources", headers=_headers(actor_user_id="outsider", roles=("external",))))["items"]
        == []
    )
    assert (
        _ok(
            client.post(
                f"/api/resources/{rid}/trash",
                headers={**headers, "Idempotency-Key": "api-resources-trash"},
            )
        )["resource"]["status"]
        == "trashed"
    )
    assert (
        _ok(
            client.get(
                "/api/resources",
                params={"projectId": project_id, "includeTrashed": True},
                headers=headers,
            )
        )["items"][0]["status"]
        == "trashed"
    )
    assert (
        _ok(
            client.post(
                f"/api/resources/{rid}/restore",
                headers={**headers, "Idempotency-Key": "api-resources-restore"},
            )
        )["resource"]["status"]
        == "active"
    )

    foundry.datasets.ensure("raw.reconcile_orders", ctx=demo_admin_context(), primary_key=["order_id"])
    reconcile = _ok(
        client.post(
            "/api/resources/reconcile",
            json={"projectId": project_id},
            headers={**headers, "Idempotency-Key": "api-resources-reconcile"},
        )
    )
    assert reconcile["createdOrUpdated"] >= 1


def test_resource_catalog_api_routes_return_request_id_errors(monkeypatch, tmp_path: Path) -> None:
    foundry = FoundryLite(
        dependencies=create_local_core_dependencies(
            db_url=f"sqlite:///{tmp_path / 'resources-api-errors.db'}",
            storage_root=tmp_path / "flite",
        )
    )
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)
    headers = _admin_headers()
    missing_project = "project_missing"
    missing_folder = "folder_missing"
    missing_rid = "ri.foundry-lite.dataset.missing"

    _assert_error(
        client.post(
            "/api/projects",
            json={"displayName": " "},
            headers={**headers, "Idempotency-Key": "api-error-project-create"},
        )
    )
    _assert_error(client.get(f"/api/projects/{missing_project}", headers=headers))
    _assert_error(client.get(f"/api/projects/{missing_project}/grants", headers=headers))
    _assert_error(
        client.put(
            f"/api/projects/{missing_project}/grants/role/analyst",
            json={"role": "viewer"},
            headers={**headers, "Idempotency-Key": "api-error-grant"},
        )
    )
    _assert_error(client.get(f"/api/projects/{missing_project}/folders", headers=headers))
    _assert_error(
        client.post(
            f"/api/projects/{missing_project}/folders",
            json={"displayName": "Raw"},
            headers={**headers, "Idempotency-Key": "api-error-folder-create"},
        )
    )
    _assert_error(
        client.post(
            f"/api/projects/{missing_project}/folders/{missing_folder}/move",
            json={"parentFolderId": None},
            headers={**headers, "Idempotency-Key": "api-error-folder-move"},
        )
    )
    _assert_error(
        client.post(
            f"/api/projects/{missing_project}/folders/{missing_folder}/trash",
            headers={**headers, "Idempotency-Key": "api-error-folder-trash"},
        )
    )
    _assert_error(
        client.post(
            f"/api/projects/{missing_project}/folders/{missing_folder}/restore",
            headers={**headers, "Idempotency-Key": "api-error-folder-restore"},
        )
    )
    _assert_error(client.get("/api/resources", params={"projectId": missing_project}, headers=headers))
    _assert_error(
        client.post(
            "/api/resources/register",
            json={
                "resourceType": "dataset",
                "displayName": "missing.orders",
                "projectId": missing_project,
                "sourceSurface": "dataset",
                "sourceRef": "missing.orders",
            },
            headers={**headers, "Idempotency-Key": "api-error-resource-register"},
        )
    )
    _assert_error(
        client.post(
            "/api/resources/reconcile",
            json={"projectId": None},
            headers={
                **_headers(actor_user_id="analyst", roles=("viewer",)),
                "Idempotency-Key": "api-error-reconcile",
            },
        )
    )
    _assert_error(client.get(f"/api/resources/{missing_rid}", headers=headers))
    _assert_error(
        client.post(
            f"/api/resources/{missing_rid}/move",
            json={"projectId": missing_project},
            headers={**headers, "Idempotency-Key": "api-error-resource-move"},
        )
    )
    _assert_error(
        client.post(
            f"/api/resources/{missing_rid}/trash",
            headers={**headers, "Idempotency-Key": "api-error-resource-trash"},
        )
    )
    _assert_error(
        client.post(
            f"/api/resources/{missing_rid}/restore",
            headers={**headers, "Idempotency-Key": "api-error-resource-restore"},
        )
    )
    _assert_error(
        client.put(
            f"/api/resources/{missing_rid}/favorite",
            headers={**headers, "Idempotency-Key": "api-error-resource-favorite"},
        )
    )
    _assert_error(
        client.delete(
            f"/api/resources/{missing_rid}/favorite",
            headers={**headers, "Idempotency-Key": "api-error-resource-unfavorite"},
        )
    )


def _admin_headers() -> dict[str, str]:
    ctx = demo_admin_context()
    return _headers(actor_user_id=ctx.actor_user_id, roles=ctx.roles)


def _headers(*, actor_user_id: str, roles: tuple[str, ...]) -> dict[str, str]:
    ctx = demo_admin_context()
    return {
        "X-Tenant-ID": ctx.tenant_id,
        "X-User-ID": actor_user_id,
        "X-Roles": ",".join(roles),
    }


def _ok(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    return response.json()


def _assert_error(response: Any) -> None:
    assert response.status_code >= 400, response.text
    detail = response.json()["detail"]
    assert detail["request_id"]
    assert detail["code"]
