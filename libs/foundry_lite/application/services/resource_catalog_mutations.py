"""Mutation handlers for ResourceCatalogService.

The public service owns request/transaction/idempotency orchestration; these
helpers keep the individual write workflows small enough to audit.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.ports import RuntimeRepository, TransactionContext
from foundry_lite.application.ports.resource_catalog_repository import (
    PrincipalType,
    ProjectFolderRow,
    ProjectRole,
    ResourceCandidate,
    ResourceCatalogRepository,
    ResourceRow,
)
from foundry_lite.application.services.resource_catalog_payloads import (
    folder_payload,
    grant_payload,
    project_payload,
    resource_payload,
)
from foundry_lite.application.services.resource_catalog_records import (
    folder_record,
    grant_record,
    owner_grant_record,
    project_record,
    resource_record,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed


class ResourceCatalogMutationHost(Protocol):
    resource_catalog_repository: ResourceCatalogRepository
    runtime_repository: RuntimeRepository

    def _require_project(
        self, conn: TransactionContext, ctx: RequestContext, project_id: str, role: ProjectRole
    ) -> object: ...

    def _require_parent_folder(
        self, conn: TransactionContext, ctx: RequestContext, project_id: str, folder_id: str | None
    ) -> None: ...

    def _folder_by_id(self, conn: TransactionContext, ctx: RequestContext, folder_id: str) -> ProjectFolderRow: ...

    def _resource_by_rid(self, conn: TransactionContext, ctx: RequestContext, rid: str) -> ResourceRow: ...

    def _resource_project_id(
        self, conn: TransactionContext, ctx: RequestContext, project_id: object, now: str
    ) -> str: ...

    def _required_folder(self, row: ProjectFolderRow | None, folder_id: str) -> ProjectFolderRow: ...

    def _required_resource(self, row: ResourceRow | None, rid: str) -> ResourceRow: ...

    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event_type: str,
        resource_type: str,
        resource_id: str | None,
        action: str,
        payload: Mapping[str, object],
        *,
        now: str,
    ) -> None: ...

    def _outbox(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, object],
        *,
        key: str,
        now: str,
    ) -> None: ...


def create_project_mutation(
    svc: ResourceCatalogMutationHost,
    conn: TransactionContext,
    ctx: RequestContext,
    *,
    display_name: str,
    description: str | None,
    metadata: Mapping[str, object],
    now: str,
    key: str,
) -> dict[str, object]:
    project = svc.resource_catalog_repository.insert_project(
        transaction=conn,
        record=project_record(ctx, display_name=display_name, description=description, metadata=metadata, now=now),
    )
    svc.resource_catalog_repository.upsert_project_grant(
        transaction=conn, record=owner_grant_record(ctx, project["id"], now=now)
    )
    payload = _payload("project", project_payload(project, "owner"))
    _record(svc, conn, ctx, "project.created", "project", project["id"], "create", payload, key, now)
    return payload


def upsert_grant_mutation(
    svc: ResourceCatalogMutationHost,
    conn: TransactionContext,
    ctx: RequestContext,
    project_id: str,
    principal_type: PrincipalType,
    principal_id: str,
    role: ProjectRole,
    now: str,
    key: str,
) -> dict[str, object]:
    svc._require_project(conn, ctx, project_id, "owner")
    row = svc.resource_catalog_repository.upsert_project_grant(
        transaction=conn,
        record=grant_record(
            ctx, project_id=project_id, principal_type=principal_type, principal_id=principal_id, role=role, now=now
        ),
    )
    payload = _payload("grant", grant_payload(row))
    _record(svc, conn, ctx, "project.grant.upserted", "project", project_id, "grant.upsert", payload, key, now)
    return payload


def create_folder_mutation(
    svc: ResourceCatalogMutationHost,
    conn: TransactionContext,
    ctx: RequestContext,
    project_id: str,
    display_name: str,
    parent_folder_id: str | None,
    metadata: Mapping[str, object],
    now: str,
    key: str,
) -> dict[str, object]:
    svc._require_project(conn, ctx, project_id, "editor")
    svc._require_parent_folder(conn, ctx, project_id, parent_folder_id)
    row = svc.resource_catalog_repository.insert_folder(
        transaction=conn,
        record=folder_record(
            ctx,
            project_id=project_id,
            parent_folder_id=parent_folder_id,
            display_name=display_name,
            metadata=metadata,
            now=now,
        ),
    )
    payload = _payload("folder", folder_payload(row))
    _record(svc, conn, ctx, "project.folder.created", "folder", row["id"], "create", payload, key, now)
    return payload


def move_folder_mutation(
    svc: ResourceCatalogMutationHost,
    conn: TransactionContext,
    ctx: RequestContext,
    project_id: str,
    folder_id: str,
    parent_folder_id: str | None,
    now: str,
    key: str,
) -> dict[str, object]:
    svc._require_project(conn, ctx, project_id, "editor")
    folder = svc._folder_by_id(conn, ctx, folder_id)
    if folder["project_id"] != project_id:
        raise ValidationFailed("folder does not belong to project", details={"folder_id": folder_id})
    svc._require_parent_folder(conn, ctx, project_id, parent_folder_id)
    row = svc.resource_catalog_repository.update_folder(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        folder_id=folder_id,
        values={"parent_folder_id": parent_folder_id, "updated_at": now},
    )
    payload = _payload("folder", folder_payload(svc._required_folder(row, folder_id)))
    _record(svc, conn, ctx, "project.folder.moved", "folder", folder_id, "move", payload, key, now)
    return payload


def set_folder_status_mutation(
    svc: ResourceCatalogMutationHost,
    conn: TransactionContext,
    ctx: RequestContext,
    project_id: str,
    folder_id: str,
    status: str,
    now: str,
    key: str,
) -> dict[str, object]:
    svc._require_project(conn, ctx, project_id, "editor")
    folder = svc._folder_by_id(conn, ctx, folder_id)
    if folder["project_id"] != project_id:
        raise ValidationFailed("folder does not belong to project", details={"folder_id": folder_id})
    row = svc.resource_catalog_repository.update_folder(
        transaction=conn, tenant_id=ctx.tenant_id, folder_id=folder_id, values={"status": status, "updated_at": now}
    )
    payload = _payload("folder", folder_payload(svc._required_folder(row, folder_id)))
    _record(svc, conn, ctx, f"project.folder.{status}", "folder", folder_id, status, payload, key, now)
    return payload


def register_resource_mutation(
    svc: ResourceCatalogMutationHost,
    conn: TransactionContext,
    ctx: RequestContext,
    request: Mapping[str, object],
    now: str,
    key: str,
) -> dict[str, object]:
    project_id = svc._resource_project_id(conn, ctx, request.get("projectId"), now)
    svc._require_project(conn, ctx, project_id, "editor")
    folder_id = _optional_text(request.get("folderId"))
    svc._require_parent_folder(conn, ctx, project_id, folder_id)
    row = svc.resource_catalog_repository.upsert_resource(
        transaction=conn,
        record=resource_record(
            ctx,
            resource_type=str(request["resourceType"]),
            display_name=str(request["displayName"]),
            project_id=project_id,
            folder_id=folder_id,
            source_surface=str(request["sourceSurface"]),
            source_ref=str(request["sourceRef"]),
            operations_path=_optional_text(request.get("operationsPath")),
            metadata=_metadata(request.get("metadata")),
            now=now,
        ),
    )
    payload = _payload("resource", resource_payload(row))
    _record(svc, conn, ctx, "resource.registered", "resource", row["rid"], "register", payload, key, now)
    return payload


def move_resource_mutation(
    svc: ResourceCatalogMutationHost,
    conn: TransactionContext,
    ctx: RequestContext,
    rid: str,
    project_id: str,
    folder_id: str | None,
    now: str,
    key: str,
) -> dict[str, object]:
    before = svc._resource_by_rid(conn, ctx, rid)
    svc._require_project(conn, ctx, before["project_id"], "editor")
    svc._require_project(conn, ctx, project_id, "editor")
    svc._require_parent_folder(conn, ctx, project_id, folder_id)
    row = svc.resource_catalog_repository.update_resource(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        resource_id=before["id"],
        values={"project_id": project_id, "folder_id": folder_id, "updated_at": now},
    )
    payload = _payload("resource", resource_payload(svc._required_resource(row, rid)))
    _record(svc, conn, ctx, "resource.moved", "resource", rid, "move", payload, key, now)
    return payload


def set_resource_status_mutation(
    svc: ResourceCatalogMutationHost,
    conn: TransactionContext,
    ctx: RequestContext,
    rid: str,
    status: str,
    now: str,
    key: str,
) -> dict[str, object]:
    before = svc._resource_by_rid(conn, ctx, rid)
    svc._require_project(conn, ctx, before["project_id"], "editor")
    row = svc.resource_catalog_repository.update_resource(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        resource_id=before["id"],
        values={"status": status, "updated_at": now},
    )
    payload = _payload("resource", resource_payload(svc._required_resource(row, rid)))
    _record(svc, conn, ctx, f"resource.{status}", "resource", rid, status, payload, key, now)
    return payload


def favorite_resource_mutation(
    svc: ResourceCatalogMutationHost,
    conn: TransactionContext,
    ctx: RequestContext,
    rid: str,
    now: str,
    key: str,
    *,
    should_add: bool,
) -> dict[str, object]:
    row = svc._resource_by_rid(conn, ctx, rid)
    svc._require_project(conn, ctx, row["project_id"], "viewer")
    if should_add:
        svc.resource_catalog_repository.add_favorite(
            transaction=conn, tenant_id=ctx.tenant_id, resource_id=row["id"], actor_user_id=ctx.actor_user_id, now=now
        )
    else:
        svc.resource_catalog_repository.remove_favorite(
            transaction=conn, tenant_id=ctx.tenant_id, resource_id=row["id"], actor_user_id=ctx.actor_user_id
        )
    payload = _payload("resource", resource_payload(row, is_favorite=should_add))
    action = "favorite" if should_add else "unfavorite"
    _record(svc, conn, ctx, f"resource.{action}", "resource", rid, action, payload, key, now)
    return payload


def reconcile_mutation(
    svc: ResourceCatalogMutationHost,
    conn: TransactionContext,
    ctx: RequestContext,
    project_id: str | None,
    now: str,
    key: str,
) -> dict[str, object]:
    if not ctx.has_role("admin"):
        raise PermissionDenied("admin role is required to reconcile resources", details={"operation": "reconcile"})
    target_project_id = svc._resource_project_id(conn, ctx, project_id, now)
    candidates = svc.resource_catalog_repository.existing_resource_candidates(transaction=conn, tenant_id=ctx.tenant_id)
    rows = [_upsert_candidate(svc, conn, ctx, target_project_id, candidate, now) for candidate in candidates]
    payload: dict[str, object] = {
        "createdOrUpdated": len(rows),
        "resources": [resource_payload(row) for row in rows],
    }
    _record(svc, conn, ctx, "resources.reconciled", "resource", target_project_id, "reconcile", payload, key, now)
    return payload


def _upsert_candidate(
    svc: ResourceCatalogMutationHost,
    conn: TransactionContext,
    ctx: RequestContext,
    project_id: str,
    candidate: ResourceCandidate,
    now: str,
) -> ResourceRow:
    return svc.resource_catalog_repository.upsert_resource(
        transaction=conn,
        record=resource_record(
            ctx,
            resource_type=candidate.resource_type,
            display_name=candidate.display_name,
            project_id=project_id,
            folder_id=None,
            source_surface=candidate.source_surface,
            source_ref=candidate.source_ref,
            operations_path=candidate.operations_path,
            metadata=candidate.metadata,
            now=now,
        ),
    )


def _record(
    svc: ResourceCatalogMutationHost,
    conn: TransactionContext,
    ctx: RequestContext,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    action: str,
    payload: Mapping[str, object],
    key: str,
    now: str,
) -> None:
    svc._audit(conn, ctx, event_type, aggregate_type, aggregate_id, action, payload, now=now)
    svc._outbox(conn, ctx, event_type, aggregate_type, aggregate_id, payload, key=key, now=now)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationFailed("expected text value", details={"value": value})
    cleaned = value.strip()
    return cleaned or None


def _payload(key: str, value: Mapping[str, object]) -> dict[str, object]:
    return {key: dict(value)}


def _metadata(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValidationFailed("metadata must be an object", details={"metadata": value})
    return {str(key): item for key, item in value.items()}
