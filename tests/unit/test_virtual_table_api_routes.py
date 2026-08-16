from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from foundry_lite.application.ports.virtual_table import (
    ExternalTableRef,
    VirtualTableColumn,
    VirtualTableRecord,
    VirtualTableSchema,
)
from foundry_lite.application.services.virtual_table_service import (
    AutoRegistrationPlan,
    BulkRegistrationFailure,
    BulkRegistrationResult,
)
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app

_HEADERS = {
    "X-Tenant-ID": "tenant-demo",
    "X-User-ID": "u-admin",
    "X-Roles": "admin",
    "X-Request-ID": "req-virtual-table",
}
_CONFIG = {"databaseUrlSecretRef": "vault://database", "schema": "public", "table": "orders"}


def _record(rid: str = "vt-1") -> VirtualTableRecord:
    return VirtualTableRecord(
        rid=rid,
        tenant_id="tenant-demo",
        name="orders",
        parent_rid="folder-1",
        connection_rid="conn-1",
        config=_CONFIG,
        schema=VirtualTableSchema((VirtualTableColumn("order_id", "integer", False),)),
        markings=("internal",),
        created_at="2026-08-13T00:00:00Z",
    )


class _VirtualTables:
    def register(self, **kwargs: object) -> VirtualTableRecord:
        assert kwargs["connection_rid"] == "conn-1"
        return _record()

    def discover(self, **kwargs: object) -> tuple[ExternalTableRef, ...]:
        assert kwargs["schema_names"] == ("public",)
        return (ExternalTableRef("public", "orders"),)

    def register_many(self, **kwargs: object) -> BulkRegistrationResult:
        tables = kwargs["tables"]
        assert isinstance(tables, tuple) and tables[0].qualified_name == "public.orders"
        return BulkRegistrationResult(
            registered=(_record(),),
            failures=(BulkRegistrationFailure("public.bad_table", "ValidationFailed"),),
        )

    def preview_auto_registration(self, **_kwargs: object) -> AutoRegistrationPlan:
        return AutoRegistrationPlan(
            new_tables=(ExternalTableRef("public", "new_orders"),),
            missing_tables=("public.old_orders",),
        )

    def run_auto_registration(self, **_kwargs: object) -> BulkRegistrationResult:
        return BulkRegistrationResult(registered=(_record("vt-new"),), failures=())

    def list(self, **_kwargs: object) -> tuple[VirtualTableRecord, ...]:
        return (_record(),)

    def get(self, rid: str, **_kwargs: object) -> VirtualTableRecord:
        assert rid == "vt-1"
        return _record()

    def schema_drift(self, rid: str, **_kwargs: object) -> dict[str, object]:
        return {"rid": rid, "hasDrift": True, "changes": ["column_added:status"]}

    def delete(self, rid: str, **_kwargs: object) -> None:
        assert rid == "vt-1"


class _RaisingVirtualTables:
    def __getattr__(self, _name: str):
        def call(*_args: object, **_kwargs: object) -> object:
            raise ValidationFailed("invalid virtual-table request")

        return call


def _install(monkeypatch: pytest.MonkeyPatch, virtual_tables: object) -> TestClient:
    monkeypatch.setattr(api_runtime, "foundry", SimpleNamespace(virtual_tables=virtual_tables))
    return TestClient(app)


def test_virtual_table_http_surface_preserves_pointer_and_operation_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _install(monkeypatch, _VirtualTables())
    register = client.post(
        "/api/sources/conn-1/virtual-tables",
        headers=_HEADERS,
        json={"name": "orders", "parentRid": "folder-1", "config": _CONFIG, "markings": ["internal"]},
    )
    discover = client.post(
        "/api/sources/conn-1/virtual-tables/discover",
        headers=_HEADERS,
        json={"config": _CONFIG, "schemaNames": ["public"]},
    )
    bulk = client.post(
        "/api/sources/conn-1/virtual-tables/bulk",
        headers=_HEADERS,
        json={
            "parentRid": "folder-1",
            "config": _CONFIG,
            "tables": [{"schema": "public", "table": "orders"}],
            "markings": ["internal"],
        },
    )
    preview = client.post(
        "/api/sources/conn-1/virtual-tables/auto-registration/preview",
        headers=_HEADERS,
        json={"parentRid": "folder-1", "config": _CONFIG, "schemaNames": ["public"]},
    )
    run = client.post(
        "/api/sources/conn-1/virtual-tables/auto-registration/run",
        headers=_HEADERS,
        json={"parentRid": "folder-1", "config": _CONFIG, "schemaNames": ["public"]},
    )
    listed = client.get("/api/sources/conn-1/virtual-tables", headers=_HEADERS)
    fetched = client.get("/api/virtual-tables/vt-1", headers=_HEADERS)
    drift = client.get("/api/virtual-tables/vt-1/schema-drift", headers=_HEADERS)
    deleted = client.delete("/api/virtual-tables/vt-1", headers=_HEADERS)

    assert register.status_code == 200
    assert register.json()["schema"] == {"columns": [{"name": "order_id", "dataType": "integer", "isNullable": False}]}
    assert register.json()["config"] == _CONFIG
    assert discover.json() == {"tables": [{"schema": "public", "table": "orders"}]}
    assert bulk.json()["failures"] == [{"table": "public.bad_table", "reason": "ValidationFailed"}]
    assert preview.json() == {
        "newTables": [{"schema": "public", "table": "new_orders"}],
        "missingTables": ["public.old_orders"],
    }
    assert run.json()["registered"][0]["rid"] == "vt-new"
    assert listed.json()["virtualTables"][0]["rid"] == "vt-1"
    assert fetched.json()["createdAt"] == "2026-08-13T00:00:00Z"
    assert drift.json()["changes"] == ["column_added:status"]
    assert deleted.json() == {"rid": "vt-1", "status": "DELETED"}


_ERROR_ROUTES: tuple[tuple[str, str, dict[str, object] | None], ...] = (
    (
        "POST",
        "/api/sources/conn-1/virtual-tables",
        {"name": "orders", "parentRid": "folder-1", "config": _CONFIG},
    ),
    (
        "POST",
        "/api/sources/conn-1/virtual-tables/discover",
        {"config": _CONFIG, "schemaNames": ["public"]},
    ),
    (
        "POST",
        "/api/sources/conn-1/virtual-tables/bulk",
        {"parentRid": "folder-1", "config": _CONFIG, "tables": []},
    ),
    (
        "POST",
        "/api/sources/conn-1/virtual-tables/auto-registration/preview",
        {"parentRid": "folder-1", "config": _CONFIG},
    ),
    (
        "POST",
        "/api/sources/conn-1/virtual-tables/auto-registration/run",
        {"parentRid": "folder-1", "config": _CONFIG},
    ),
    ("GET", "/api/sources/conn-1/virtual-tables", None),
    ("GET", "/api/virtual-tables/vt-1", None),
    ("GET", "/api/virtual-tables/vt-1/schema-drift", None),
    ("DELETE", "/api/virtual-tables/vt-1", None),
)


@pytest.mark.parametrize(("method", "path", "body"), _ERROR_ROUTES)
def test_virtual_table_http_surface_maps_domain_failures_without_a_500(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    response = _install(monkeypatch, _RaisingVirtualTables()).request(
        method,
        path,
        headers=_HEADERS,
        json=body,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "VALIDATION_FAILED",
        "message": "invalid virtual-table request",
        "details": {},
        "request_id": "req-virtual-table",
    }
