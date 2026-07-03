"""Ontology lifecycle: rollback, validate migration plans, HTTP apply/rollback, titleProperty."""

from __future__ import annotations

from typing import Any, cast

import yaml
from fastapi.testclient import TestClient
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import NotFound, ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api.main import app
from pytest import MonkeyPatch, raises
from sqlalchemy import select

ORDERS_CSV = "order_id,status,status_alt\nO-1,PENDING,PENDING\n"

ADMIN_HEADERS = {
    "X-Tenant-ID": "tenant-demo",
    "X-User-ID": "user-demo-admin",
    "X-Roles": "admin,data_engineer,ops_manager",
}


def _order_definition(
    *,
    status_column: str = "status",
    title_property: str | None = "orderId",
    extra_properties: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    order: dict[str, object] = {
        "apiName": "Order",
        "primaryKey": "orderId",
        "backing": {"dataset": "clean.orders"},
        "properties": [
            {"apiName": "orderId", "column": "order_id", "type": "string", "nullable": False, "indexed": True},
            {"apiName": "status", "column": status_column, "type": "string"},
            *(extra_properties or []),
        ],
    }
    if title_property is not None:
        order["titleProperty"] = title_property
    return {"objectTypes": [order]}


def _yaml_text(definition: dict[str, object]) -> str:
    return yaml.safe_dump(definition, sort_keys=False)


def _prepare_orders_dataset(foundry: FoundryLite, tmp_path, ctx: RequestContext) -> None:
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text(ORDERS_CSV, encoding="utf-8")
    foundry.datasets.ensure("clean.orders", ctx=ctx, primary_key=["order_id"])
    foundry.datasets.upload_csv("clean.orders", str(csv_path), ctx=ctx)


def _version_statuses(foundry: FoundryLite) -> dict[int, str]:
    with foundry.engine.begin() as conn:
        rows = (
            cast(Any, conn).execute(select(db.ontology_versions.c.version_number, db.ontology_versions.c.status)).all()
        )
    return {row.version_number: row.status for row in rows}


def test_rollback_restores_archived_shape_as_new_active_version(foundry, tmp_path) -> None:
    ctx = demo_admin_context()
    _prepare_orders_dataset(foundry, tmp_path, ctx)
    first = foundry.ontology.apply_text(_yaml_text(_order_definition()), ctx=ctx)
    second = foundry.ontology.apply_text(_yaml_text(_order_definition(status_column="status_alt")), ctx=ctx)
    assert first["version_number"] == 1
    assert second["version_number"] == 2
    assert second["migration_plan"]["status"] == "compatible_with_warning"

    result = foundry.ontology.rollback(1, ctx=ctx)

    assert result["version_number"] == 3
    assert result["rolled_back_from_version_number"] == 2
    assert result["rolled_back_to_version_number"] == 1
    assert result["migration_plan"]["objectReindexPlan"]
    catalog = foundry.ontology.catalog(ctx=ctx)
    assert catalog["versionNumber"] == 3
    assert catalog["status"] == "active"
    order = catalog["objectTypes"][0]
    assert order["titleProperty"] == "orderId"
    properties = {item["apiName"]: item for item in order["properties"]}
    assert properties["status"]["columnName"] == "status"
    # Append-only history: rollback creates a new version, old ones stay archived.
    assert _version_statuses(foundry) == {1: "archived", 2: "archived", 3: "active"}


def test_rollback_records_audit_event_with_source_and_target_versions(foundry, tmp_path) -> None:
    ctx = demo_admin_context()
    _prepare_orders_dataset(foundry, tmp_path, ctx)
    foundry.ontology.apply_text(_yaml_text(_order_definition()), ctx=ctx)
    foundry.ontology.apply_text(_yaml_text(_order_definition(status_column="status_alt")), ctx=ctx)

    result = foundry.ontology.rollback(1, ctx=ctx)

    audit_events = foundry.operations.list_runs(ctx=ctx)["auditEvents"]
    rollback_events = [event for event in audit_events if event["event_type"] == "ontology.rollback"]
    assert len(rollback_events) == 1
    event = rollback_events[0]
    assert event["resource_id"] == result["ontology_version_id"]
    assert event["before_ref"]["sourceVersionNumber"] == 2
    assert event["after_ref"]["targetVersionNumber"] == 1
    assert event["after_ref"]["newVersionNumber"] == 3


def test_rollback_blocked_when_restoration_would_remove_a_property(foundry, tmp_path) -> None:
    ctx = demo_admin_context()
    _prepare_orders_dataset(foundry, tmp_path, ctx)
    foundry.ontology.apply_text(_yaml_text(_order_definition()), ctx=ctx)
    added_property: list[dict[str, object]] = [{"apiName": "note", "type": "string", "source": "edit_layer"}]
    foundry.ontology.apply_text(_yaml_text(_order_definition(extra_properties=added_property)), ctx=ctx)

    with raises(ValidationFailed) as exc_info:
        foundry.ontology.rollback(1, ctx=ctx)

    plan = cast(dict[str, Any], exc_info.value.details["ontologyMigration"])
    assert plan["status"] == "blocked"
    assert any(change["kind"] == "property_removed" for change in plan["blockedChanges"])
    # The blocked rollback must leave history untouched.
    assert _version_statuses(foundry) == {1: "archived", 2: "active"}


def test_rollback_rejects_unknown_and_non_archived_versions(foundry, tmp_path) -> None:
    ctx = demo_admin_context()
    _prepare_orders_dataset(foundry, tmp_path, ctx)
    foundry.ontology.apply_text(_yaml_text(_order_definition()), ctx=ctx)

    with raises(NotFound):
        foundry.ontology.rollback(99, ctx=ctx)
    with raises(ValidationFailed, match="only archived ontology versions"):
        foundry.ontology.rollback(1, ctx=ctx)


def test_validate_reports_migration_plan_with_warning_and_blocked_examples(foundry, tmp_path) -> None:
    ctx = demo_admin_context()
    _prepare_orders_dataset(foundry, tmp_path, ctx)
    foundry.ontology.apply_text(_yaml_text(_order_definition()), ctx=ctx)

    deprecating = _order_definition()
    order_types = deprecating["objectTypes"]
    assert isinstance(order_types, list)
    order_properties = order_types[0]["properties"]
    assert isinstance(order_properties, list)
    order_properties[1]["deprecated"] = True
    warned = foundry.ontology.validate(_yaml_text(deprecating), ctx=ctx)
    assert warned["status"] == "valid"
    assert warned["object_type_count"] == 1
    assert warned["migration_plan"]["status"] == "compatible_with_warning"
    assert any(change["kind"] == "property_deprecated" for change in warned["migration_plan"]["warnings"])

    removing = _order_definition()
    removing_types = removing["objectTypes"]
    assert isinstance(removing_types, list)
    removing_properties = removing_types[0]["properties"]
    assert isinstance(removing_properties, list)
    del removing_properties[1]
    blocked = foundry.ontology.validate(_yaml_text(removing), ctx=ctx)
    assert blocked["migration_plan"]["status"] == "blocked"
    assert any(change["kind"] == "property_removed" for change in blocked["migration_plan"]["blockedChanges"])


def test_title_property_must_reference_a_declared_property(foundry, tmp_path) -> None:
    ctx = demo_admin_context()
    _prepare_orders_dataset(foundry, tmp_path, ctx)

    with raises(ValidationFailed, match="titleProperty must reference a declared property") as exc_info:
        foundry.ontology.validate(_yaml_text(_order_definition(title_property="missing")), ctx=ctx)

    assert exc_info.value.details == {"objectType": "Order", "titleProperty": "missing"}


def test_catalog_title_property_is_optional(foundry, tmp_path) -> None:
    ctx = demo_admin_context()
    _prepare_orders_dataset(foundry, tmp_path, ctx)
    foundry.ontology.apply_text(_yaml_text(_order_definition(title_property=None)), ctx=ctx)

    catalog = foundry.ontology.catalog(ctx=ctx)

    assert catalog["objectTypes"][0]["titleProperty"] is None


def test_api_ontology_apply_endpoint_activates_and_returns_migration_plan(
    foundry, tmp_path, monkeypatch: MonkeyPatch
) -> None:
    ctx = demo_admin_context()
    _prepare_orders_dataset(foundry, tmp_path, ctx)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(app)

    response = client.post(
        "/api/ontology/apply",
        headers=ADMIN_HEADERS,
        json={"yamlText": _yaml_text(_order_definition())},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ontologyVersionId"].startswith("ont_")
    assert payload["versionNumber"] == 1
    assert payload["migrationPlan"]["status"] == "compatible"
    catalog = client.get("/api/ontology/catalog", headers=ADMIN_HEADERS)
    assert catalog.json()["ontologyVersionId"] == payload["ontologyVersionId"]
    assert catalog.json()["objectTypes"][0]["titleProperty"] == "orderId"


def test_api_ontology_apply_rejects_oversized_yaml(foundry, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(api_runtime, "foundry", foundry)

    response = TestClient(app).post(
        "/api/ontology/apply",
        headers=ADMIN_HEADERS,
        json={"yamlText": "a" * (1_048_576 + 1)},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_FAILED"
    assert response.json()["detail"]["message"] == "ontology yaml text exceeds size limit"


def test_api_ontology_apply_requires_activate_permission(foundry, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(api_runtime, "foundry", foundry)

    response = TestClient(app).post(
        "/api/ontology/apply",
        headers={"X-Tenant-ID": "tenant-demo", "X-User-ID": "viewer-user", "X-Roles": "viewer"},
        json={"yamlText": _yaml_text(_order_definition())},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "PERMISSION_DENIED"


def test_api_ontology_rollback_endpoint_restores_archived_version(foundry, tmp_path, monkeypatch: MonkeyPatch) -> None:
    ctx = demo_admin_context()
    _prepare_orders_dataset(foundry, tmp_path, ctx)
    foundry.ontology.apply_text(_yaml_text(_order_definition()), ctx=ctx)
    foundry.ontology.apply_text(_yaml_text(_order_definition(status_column="status_alt")), ctx=ctx)
    monkeypatch.setattr(api_runtime, "foundry", foundry)

    response = TestClient(app).post(
        "/api/ontology/rollback",
        headers=ADMIN_HEADERS,
        json={"versionNumber": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["versionNumber"] == 3
    assert payload["rolledBackFromVersionNumber"] == 2
    assert payload["rolledBackToVersionNumber"] == 1
    assert payload["migrationPlan"]["objectReindexPlan"]


def test_api_ontology_rollback_requires_activate_permission(foundry, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(api_runtime, "foundry", foundry)

    response = TestClient(app).post(
        "/api/ontology/rollback",
        headers={"X-Tenant-ID": "tenant-demo", "X-User-ID": "viewer-user", "X-Roles": "viewer"},
        json={"versionNumber": 1},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "PERMISSION_DENIED"
