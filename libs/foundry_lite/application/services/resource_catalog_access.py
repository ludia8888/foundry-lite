"""Access helpers for ResourceCatalogService."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.ports import RuntimeRepository, TransactionContext
from foundry_lite.application.ports.resource_catalog_repository import (
    ProjectFolderRow,
    ProjectRole,
    ProjectRow,
    ResourceCatalogRepository,
    ResourceRow,
)
from foundry_lite.application.services.resource_catalog_policy import (
    actor_project_role,
    can_discover,
    can_read,
    require_project_role,
)
from foundry_lite.application.services.resource_catalog_records import (
    audit_record,
    outbox_record,
    owner_grant_record,
    project_record,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed


class ResourceCatalogAccessHost(Protocol):
    resource_catalog_repository: ResourceCatalogRepository
    runtime_repository: RuntimeRepository


def resource_project_id(
    host: ResourceCatalogAccessHost,
    conn: TransactionContext,
    ctx: RequestContext,
    project_id: object,
    now: str,
) -> str:
    value = _optional_text(project_id)
    if value is not None:
        return value
    personal = personal_project(host, conn, ctx)
    if personal is not None:
        return personal["id"]
    project = host.resource_catalog_repository.insert_project(
        transaction=conn,
        record=project_record(
            ctx,
            display_name=f"{ctx.actor_user_id} Home",
            description=None,
            metadata={"kind": "personal"},
            now=now,
        ),
    )
    host.resource_catalog_repository.upsert_project_grant(
        transaction=conn, record=owner_grant_record(ctx, project["id"], now=now)
    )
    return project["id"]


def personal_project(
    host: ResourceCatalogAccessHost, conn: TransactionContext, ctx: RequestContext
) -> ProjectRow | None:
    for row in host.resource_catalog_repository.list_projects(transaction=conn, tenant_id=ctx.tenant_id):
        if row["created_by_user_id"] == ctx.actor_user_id and dict(row["metadata"]).get("kind") == "personal":
            return row
    return None


def readable_project_ids(
    host: ResourceCatalogAccessHost,
    conn: TransactionContext,
    ctx: RequestContext,
    project_id: str | None,
) -> list[str]:
    if project_id is not None:
        require_project(host, conn, ctx, project_id, "viewer")
        return [project_id]
    return [
        project["id"]
        for project in host.resource_catalog_repository.list_projects(transaction=conn, tenant_id=ctx.tenant_id)
        if can_read(project_role(host, conn, ctx, project["id"]))
    ]


def project_if_discoverable(
    host: ResourceCatalogAccessHost,
    conn: TransactionContext,
    ctx: RequestContext,
    project: ProjectRow,
) -> tuple[ProjectRow, ProjectRole | None] | None:
    role = project_role(host, conn, ctx, project["id"])
    return (project, role) if can_discover(role) else None


def project_with_role(
    host: ResourceCatalogAccessHost,
    conn: TransactionContext,
    ctx: RequestContext,
    project_id: str,
) -> tuple[ProjectRow, ProjectRole | None]:
    project = host.resource_catalog_repository.project_by_id(
        transaction=conn, tenant_id=ctx.tenant_id, project_id=project_id
    )
    if project is None:
        raise NotFound("project not found", details={"project_id": project_id})
    return project, project_role(host, conn, ctx, project_id)


def require_project(
    host: ResourceCatalogAccessHost,
    conn: TransactionContext,
    ctx: RequestContext,
    project_id: str,
    role: ProjectRole,
) -> ProjectRow:
    project, actual = project_with_role(host, conn, ctx, project_id)
    require_project_role(actual, role, project_id=project_id)
    return project


def require_parent_folder(
    host: ResourceCatalogAccessHost,
    conn: TransactionContext,
    ctx: RequestContext,
    project_id: str,
    folder_id: str | None,
) -> None:
    if folder_id is None:
        return
    folder = folder_by_id(host, conn, ctx, folder_id)
    if folder["project_id"] != project_id or folder["status"] != "active":
        raise ValidationFailed("folder does not belong to active project tree", details={"folder_id": folder_id})


def folder_by_id(
    host: ResourceCatalogAccessHost, conn: TransactionContext, ctx: RequestContext, folder_id: str
) -> ProjectFolderRow:
    row = host.resource_catalog_repository.folder_by_id(transaction=conn, tenant_id=ctx.tenant_id, folder_id=folder_id)
    return required_folder(row, folder_id)


def resource_by_rid(
    host: ResourceCatalogAccessHost, conn: TransactionContext, ctx: RequestContext, rid: str
) -> ResourceRow:
    row = host.resource_catalog_repository.resource_by_rid(transaction=conn, tenant_id=ctx.tenant_id, rid=rid)
    if row is None:
        raise NotFound("resource not found", details={"rid": rid})
    return row


def audit(
    runtime_repository: RuntimeRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    event_type: str,
    resource_type: str,
    resource_id: str | None,
    action: str,
    payload: Mapping[str, object],
    now: str,
) -> None:
    runtime_repository.insert_audit_event(
        transaction=conn,
        record=audit_record(
            ctx,
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            after_ref=payload,
            now=now,
        ),
    )


def outbox(
    runtime_repository: RuntimeRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: Mapping[str, object],
    key: str,
    now: str,
) -> None:
    runtime_repository.insert_outbox_event(
        transaction=conn,
        record=outbox_record(
            ctx,
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
            idempotency_key=key,
            now=now,
        ),
    )


def required_folder(row: ProjectFolderRow | None, folder_id: str) -> ProjectFolderRow:
    if row is None:
        raise NotFound("folder not found", details={"folder_id": folder_id})
    return row


def required_resource(row: ResourceRow | None, rid: str) -> ResourceRow:
    if row is None:
        raise NotFound("resource not found", details={"rid": rid})
    return row


def project_role(
    host: ResourceCatalogAccessHost, conn: TransactionContext, ctx: RequestContext, project_id: str
) -> ProjectRole | None:
    grants = host.resource_catalog_repository.list_project_grants(
        transaction=conn, tenant_id=ctx.tenant_id, project_id=project_id
    )
    return actor_project_role(ctx, grants)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationFailed("expected text value", details={"value": value})
    cleaned = value.strip()
    return cleaned or None
