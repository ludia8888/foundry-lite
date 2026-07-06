"""Thin facade for projects, folders, and RID resources."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.services.resource_catalog_service import ResourceCatalogService
from foundry_lite.domain.context import RequestContext
from foundry_lite.observability.tracing import trace_public_methods


@trace_public_methods
class ResourceWorkspace:
    """Compass-style project and resource browser entrypoint."""

    def __init__(self, resources: ResourceCatalogService) -> None:
        self._resources = resources

    def create_project(
        self,
        *,
        display_name: str,
        description: str | None = None,
        metadata: Mapping[str, object] | None = None,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._resources.create_project(
            display_name=display_name,
            description=description,
            metadata=metadata or {},
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def list_projects(self, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._resources.list_projects(ctx=ctx)

    def get_project(self, project_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._resources.get_project(project_id, ctx=ctx)

    def list_project_grants(self, project_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._resources.list_project_grants(project_id, ctx=ctx)

    def upsert_project_grant(
        self,
        project_id: str,
        *,
        principal_type: str,
        principal_id: str,
        role: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._resources.upsert_project_grant(
            project_id,
            principal_type=principal_type,
            principal_id=principal_id,
            role=role,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def create_folder(
        self,
        project_id: str,
        *,
        display_name: str,
        parent_folder_id: str | None = None,
        metadata: Mapping[str, object] | None = None,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._resources.create_folder(
            project_id,
            display_name=display_name,
            parent_folder_id=parent_folder_id,
            metadata=metadata or {},
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def list_folders(
        self, project_id: str, *, include_trashed: bool = False, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self._resources.list_folders(project_id, include_trashed=include_trashed, ctx=ctx)

    def move_folder(
        self,
        project_id: str,
        folder_id: str,
        *,
        parent_folder_id: str | None,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._resources.move_folder(
            project_id, folder_id, parent_folder_id=parent_folder_id, idempotency_key=idempotency_key, ctx=ctx
        )

    def trash_folder(
        self, project_id: str, folder_id: str, *, idempotency_key: str, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self._resources.trash_folder(project_id, folder_id, idempotency_key=idempotency_key, ctx=ctx)

    def restore_folder(
        self, project_id: str, folder_id: str, *, idempotency_key: str, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self._resources.restore_folder(project_id, folder_id, idempotency_key=idempotency_key, ctx=ctx)

    def register_resource(
        self,
        *,
        resource_type: str,
        display_name: str,
        source_surface: str,
        source_ref: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        project_id: str | None = None,
        folder_id: str | None = None,
        operations_path: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        return self._resources.register_resource(
            resource_type=resource_type,
            display_name=display_name,
            project_id=project_id,
            folder_id=folder_id,
            source_surface=source_surface,
            source_ref=source_ref,
            operations_path=operations_path,
            metadata=metadata or {},
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def list_resources(
        self,
        *,
        project_id: str | None = None,
        folder_id: str | None = None,
        include_trashed: bool = False,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._resources.list_resources(
            project_id=project_id, folder_id=folder_id, include_trashed=include_trashed, ctx=ctx
        )

    def get_resource(self, rid: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._resources.get_resource(rid, ctx=ctx)

    def move_resource(
        self,
        rid: str,
        *,
        project_id: str,
        folder_id: str | None = None,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._resources.move_resource(
            rid, project_id=project_id, folder_id=folder_id, idempotency_key=idempotency_key, ctx=ctx
        )

    def trash_resource(self, rid: str, *, idempotency_key: str, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._resources.trash_resource(rid, idempotency_key=idempotency_key, ctx=ctx)

    def restore_resource(
        self, rid: str, *, idempotency_key: str, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self._resources.restore_resource(rid, idempotency_key=idempotency_key, ctx=ctx)

    def favorite_resource(
        self, rid: str, *, idempotency_key: str, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self._resources.favorite_resource(rid, idempotency_key=idempotency_key, ctx=ctx)

    def unfavorite_resource(
        self, rid: str, *, idempotency_key: str, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self._resources.unfavorite_resource(rid, idempotency_key=idempotency_key, ctx=ctx)

    def reconcile(
        self, *, project_id: str | None = None, idempotency_key: str, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self._resources.reconcile(project_id=project_id, idempotency_key=idempotency_key, ctx=ctx)
