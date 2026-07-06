from __future__ import annotations

from typing import Any

import pytest
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.domain.errors import ConflictDetected, PermissionDenied, ValidationFailed
from foundry_lite.infrastructure import schema as db


def _catalog_contexts() -> tuple[RequestContext, RequestContext, RequestContext]:
    admin = demo_admin_context()
    analyst = RequestContext(
        tenant_id=admin.tenant_id,
        actor_user_id="analyst-user",
        roles=("analyst",),
    )
    stranger = RequestContext(
        tenant_id=admin.tenant_id,
        actor_user_id="stranger-user",
        roles=("viewer",),
    )
    return admin, analyst, stranger


def _create_project_with_analyst_viewer(foundry: Any, admin: RequestContext) -> dict[str, Any]:
    project = foundry.resources.create_project(
        display_name="Supply Chain Ops",
        idempotency_key="project-supply-chain",
        ctx=admin,
    )["project"]
    foundry.resources.upsert_project_grant(
        project["id"],
        principal_type="role",
        principal_id="analyst",
        role="viewer",
        idempotency_key="grant-analyst-viewer",
        ctx=admin,
    )
    return project


def _create_raw_orders_resource(
    foundry: Any,
    admin: RequestContext,
    project_id: str,
) -> dict[str, Any]:
    folder = foundry.resources.create_folder(
        project_id,
        display_name="Raw",
        idempotency_key="folder-raw",
        ctx=admin,
    )["folder"]
    return foundry.resources.register_resource(
        resource_type="dataset",
        display_name="raw.orders",
        project_id=project_id,
        folder_id=folder["id"],
        source_surface="dataset",
        source_ref="raw.orders",
        idempotency_key="resource-raw-orders",
        ctx=admin,
    )["resource"]


def _assert_analyst_can_read_and_favorite(
    foundry: Any,
    analyst: RequestContext,
    project_id: str,
    resource: dict[str, Any],
) -> None:
    visible = foundry.resources.list_resources(project_id=project_id, ctx=analyst)
    assert [item["rid"] for item in visible["items"]] == [resource["rid"]]
    assert foundry.resources.get_resource(resource["rid"], ctx=analyst)["resource"]["rid"] == resource["rid"]

    favorite = foundry.resources.favorite_resource(
        resource["rid"],
        idempotency_key="favorite-raw-orders",
        ctx=analyst,
    )["resource"]
    assert favorite["isFavorite"] is True
    assert foundry.resources.list_resources(project_id=project_id, ctx=analyst)["items"][0]["isFavorite"] is True


def _assert_project_permission_boundaries(
    foundry: Any,
    analyst: RequestContext,
    stranger: RequestContext,
    project_id: str,
) -> None:
    with pytest.raises(PermissionDenied):
        foundry.resources.create_folder(
            project_id,
            display_name="Curated",
            idempotency_key="folder-curated-denied",
            ctx=analyst,
        )
    with pytest.raises(PermissionDenied):
        foundry.resources.list_resources(project_id=project_id, ctx=stranger)


def test_resource_catalog_project_grants_inherit_to_folders_and_resources(foundry) -> None:
    admin, analyst, stranger = _catalog_contexts()
    project = _create_project_with_analyst_viewer(foundry, admin)
    resource = _create_raw_orders_resource(foundry, admin, project["id"])

    _assert_analyst_can_read_and_favorite(foundry, analyst, project["id"], resource)
    _assert_project_permission_boundaries(foundry, analyst, stranger, project["id"])


def _create_ops_projects_and_folders(
    foundry: Any,
    admin: RequestContext,
) -> dict[str, dict[str, Any]]:
    project = foundry.resources.create_project(
        display_name="Ops",
        idempotency_key="project-ops",
        ctx=admin,
    )["project"]
    replayed_project = foundry.resources.create_project(
        display_name="Ops",
        idempotency_key="project-ops",
        ctx=admin,
    )["project"]
    other_project = foundry.resources.create_project(
        display_name="Other",
        idempotency_key="project-other",
        ctx=admin,
    )["project"]
    folder = foundry.resources.create_folder(
        project["id"],
        display_name="Raw",
        idempotency_key="folder-ops-raw",
        ctx=admin,
    )["folder"]
    other_folder = foundry.resources.create_folder(
        other_project["id"],
        display_name="Other Raw",
        idempotency_key="folder-other-raw",
        ctx=admin,
    )["folder"]
    return {
        "project": project,
        "replayedProject": replayed_project,
        "otherProject": other_project,
        "folder": folder,
        "otherFolder": other_folder,
    }


def _register_orders_resource(
    foundry: Any,
    admin: RequestContext,
    project_id: str,
    folder_id: str,
) -> dict[str, Any]:
    return foundry.resources.register_resource(
        resource_type="dataset",
        display_name="raw.orders",
        project_id=project_id,
        folder_id=folder_id,
        source_surface="dataset",
        source_ref="raw.orders",
        idempotency_key="resource-orders",
        ctx=admin,
    )["resource"]


def _assert_project_idempotency(foundry: Any, admin: RequestContext, projects: dict[str, Any]) -> None:
    assert projects["replayedProject"]["id"] == projects["project"]["id"]
    with pytest.raises(ConflictDetected):
        foundry.resources.create_project(
            display_name="Different",
            idempotency_key="project-ops",
            ctx=admin,
        )


def _assert_folder_project_boundary(foundry: Any, admin: RequestContext, projects: dict[str, Any]) -> None:
    with pytest.raises(ValidationFailed):
        foundry.resources.trash_folder(
            projects["project"]["id"],
            projects["otherFolder"]["id"],
            idempotency_key="folder-cross-project-trash",
            ctx=admin,
        )


def _assert_resource_trash_restore(
    foundry: Any,
    admin: RequestContext,
    project_id: str,
    resource: dict[str, Any],
) -> None:
    trashed = foundry.resources.trash_resource(
        resource["rid"],
        idempotency_key="resource-trash",
        ctx=admin,
    )["resource"]
    assert trashed["status"] == "trashed"
    assert foundry.resources.list_resources(project_id=project_id, ctx=admin)["items"] == []
    assert (
        foundry.resources.list_resources(
            project_id=project_id,
            include_trashed=True,
            ctx=admin,
        )["items"][0]["rid"]
        == resource["rid"]
    )

    restored = foundry.resources.restore_resource(
        resource["rid"],
        idempotency_key="resource-restore",
        ctx=admin,
    )["resource"]
    assert restored["status"] == "active"
    assert foundry.resources.list_resources(project_id=project_id, ctx=admin)["items"][0]["rid"] == resource["rid"]


def test_resource_catalog_idempotency_trash_restore_and_project_boundary(foundry) -> None:
    admin = demo_admin_context()
    projects = _create_ops_projects_and_folders(foundry, admin)
    resource = _register_orders_resource(
        foundry,
        admin,
        projects["project"]["id"],
        projects["folder"]["id"],
    )

    _assert_project_idempotency(foundry, admin, projects)
    _assert_folder_project_boundary(foundry, admin, projects)
    _assert_resource_trash_restore(foundry, admin, projects["project"]["id"], resource)


def test_dataset_creation_automatically_registers_resource_row(foundry) -> None:
    admin = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=admin, primary_key=["order_id"])

    items = foundry.resources.list_resources(ctx=admin)["items"]

    dataset_resources = [
        item for item in items if item["sourceSurface"] == "dataset" and item["sourceRef"] == "raw.orders"
    ]
    assert dataset_resources
    assert dataset_resources[0]["rid"].startswith("ri.foundry-lite.dataset.")


def test_resource_catalog_reconcile_backfills_existing_dataset_rows(foundry) -> None:
    admin = demo_admin_context()
    now = "2026-07-05T00:00:00Z"
    with foundry.engine.begin() as conn:
        conn.execute(
            db.datasets.insert().values(
                id="ds_legacy_orders",
                tenant_id=admin.tenant_id,
                namespace="legacy",
                name="orders",
                description=None,
                storage_kind="parquet_manifest",
                storage_uri="/legacy/orders",
                owner_team=None,
                classification="public",
                status="active",
                primary_key=["order_id"],
                partition_spec=[],
                sort_order=[],
                target_file_size_bytes=None,
                created_at=now,
                updated_at=now,
            )
        )

    result = foundry.resources.reconcile(idempotency_key="resources-reconcile", ctx=admin)

    assert result["createdOrUpdated"] >= 1
    dataset_resources = [
        item
        for item in result["resources"]
        if item["sourceSurface"] == "dataset" and item["sourceRef"] == "legacy.orders"
    ]
    assert dataset_resources
    assert dataset_resources[0]["rid"].startswith("ri.foundry-lite.dataset.")
