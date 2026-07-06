"""Application service for Compass-style projects, folders, and resources."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from foundry_lite.application.ports import RuntimeRepository, TransactionContext
from foundry_lite.application.ports.resource_catalog_repository import (
    ProjectFolderRow,
    ProjectRole,
    ProjectRow,
    ResourceCatalogRepository,
    ResourceRow,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services import resource_catalog_access as catalog_access
from foundry_lite.application.services import resource_catalog_mutations as catalog_mutations
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.resource_catalog_payloads import (
    folder_payload,
    grant_payload,
    parse_principal_type,
    parse_project_role,
    project_payload,
    replay_or_conflict,
    require_text,
    resource_payload,
    stable_request_hash,
)
from foundry_lite.application.services.resource_catalog_policy import (
    can_discover,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied

MutationWriter = Callable[[TransactionContext, RequestContext, str], dict[str, object]]


class ResourceCatalogService(CoreService):
    """Project-grant-backed resource catalog service."""

    required_dependencies = ("engine", "resource_catalog_repository", "runtime_repository")
    required_collaborators = ()

    resource_catalog_repository: ResourceCatalogRepository
    runtime_repository: RuntimeRepository

    def create_project(
        self,
        *,
        display_name: str,
        description: str | None,
        metadata: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        name = require_text(display_name, "display_name")
        request = {"displayName": name, "description": description, "metadata": dict(metadata)}
        return self._mutation(
            ctx,
            "projects.create",
            idempotency_key,
            request,
            lambda conn, actor, now: catalog_mutations.create_project_mutation(
                self,
                conn,
                actor,
                display_name=name,
                description=description,
                metadata=metadata,
                now=now,
                key=idempotency_key,
            ),
        )

    def list_projects(self, *, ctx: RequestContext | None = None) -> dict[str, object]:
        actor = self._ctx(ctx)
        with self.engine.begin() as conn:
            projects = self.resource_catalog_repository.list_projects(transaction=conn, tenant_id=actor.tenant_id)
            payloads = [self._project_if_discoverable(conn, actor, row) for row in projects]
        return {"projects": [item for item in payloads if item is not None]}

    def get_project(self, project_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        actor = self._ctx(ctx)
        with self.engine.begin() as conn:
            project, role = self._project_with_role(conn, actor, project_id)
            if not can_discover(role):
                raise PermissionDenied("project permission denied", details={"project_id": project_id})
            return {"project": project_payload(project, role)}

    def list_project_grants(self, project_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        actor = self._ctx(ctx)
        with self.engine.begin() as conn:
            self._require_project(conn, actor, project_id, "owner")
            grants = self.resource_catalog_repository.list_project_grants(
                transaction=conn, tenant_id=actor.tenant_id, project_id=project_id
            )
            return {"grants": [grant_payload(row) for row in grants]}

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
        parsed_type = parse_principal_type(principal_type)
        parsed_role = parse_project_role(role)
        request = {"projectId": project_id, "principalType": parsed_type, "principalId": principal_id, "role": role}
        return self._mutation(
            ctx,
            "projects.grants.upsert",
            idempotency_key,
            request,
            lambda conn, actor, now: catalog_mutations.upsert_grant_mutation(
                self,
                conn,
                actor,
                project_id,
                parsed_type,
                require_text(principal_id, "principal_id"),
                parsed_role,
                now,
                idempotency_key,
            ),
        )

    def create_folder(
        self,
        project_id: str,
        *,
        display_name: str,
        parent_folder_id: str | None,
        metadata: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        name = require_text(display_name, "display_name")
        request = {
            "projectId": project_id,
            "displayName": name,
            "parentFolderId": parent_folder_id,
            "metadata": dict(metadata),
        }
        return self._mutation(
            ctx,
            "projects.folders.create",
            idempotency_key,
            request,
            lambda conn, actor, now: catalog_mutations.create_folder_mutation(
                self, conn, actor, project_id, name, parent_folder_id, metadata, now, idempotency_key
            ),
        )

    def list_folders(
        self, project_id: str, *, include_trashed: bool = False, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        actor = self._ctx(ctx)
        with self.engine.begin() as conn:
            self._require_project(conn, actor, project_id, "viewer")
            folders = self.resource_catalog_repository.list_folders(
                transaction=conn, tenant_id=actor.tenant_id, project_id=project_id, include_trashed=include_trashed
            )
            return {"folders": [folder_payload(row) for row in folders]}

    def move_folder(
        self,
        project_id: str,
        folder_id: str,
        *,
        parent_folder_id: str | None,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        request = {"projectId": project_id, "folderId": folder_id, "parentFolderId": parent_folder_id}
        return self._mutation(
            ctx,
            "projects.folders.move",
            idempotency_key,
            request,
            lambda conn, actor, now: catalog_mutations.move_folder_mutation(
                self, conn, actor, project_id, folder_id, parent_folder_id, now, idempotency_key
            ),
        )

    def trash_folder(
        self, project_id: str, folder_id: str, *, idempotency_key: str, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self._folder_status(project_id, folder_id, "trashed", "projects.folders.trash", idempotency_key, ctx)

    def restore_folder(
        self, project_id: str, folder_id: str, *, idempotency_key: str, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self._folder_status(project_id, folder_id, "active", "projects.folders.restore", idempotency_key, ctx)

    def register_resource(
        self,
        *,
        resource_type: str,
        display_name: str,
        project_id: str | None,
        folder_id: str | None,
        source_surface: str,
        source_ref: str,
        operations_path: str | None,
        metadata: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        request = _resource_request(
            resource_type, display_name, project_id, folder_id, source_surface, source_ref, operations_path, metadata
        )
        return self._mutation(
            ctx,
            "resources.register",
            idempotency_key,
            request,
            lambda conn, actor, now: catalog_mutations.register_resource_mutation(
                self, conn, actor, request, now, idempotency_key
            ),
        )

    def list_resources(
        self,
        *,
        project_id: str | None,
        folder_id: str | None,
        include_trashed: bool,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        actor = self._ctx(ctx)
        with self.engine.begin() as conn:
            project_ids = self._readable_project_ids(conn, actor, project_id)
            rows = self.resource_catalog_repository.list_resources(
                transaction=conn,
                tenant_id=actor.tenant_id,
                project_ids=project_ids,
                folder_id=folder_id,
                include_trashed=include_trashed,
            )
            favorites = self.resource_catalog_repository.favorite_resource_ids(
                transaction=conn, tenant_id=actor.tenant_id, actor_user_id=actor.actor_user_id
            )
        return {
            "items": [resource_payload(row, is_favorite=row["id"] in favorites) for row in rows],
            "nextCursor": None,
        }

    def get_resource(self, rid: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        actor = self._ctx(ctx)
        with self.engine.begin() as conn:
            row = self._resource_by_rid(conn, actor, rid)
            self._require_project(conn, actor, row["project_id"], "viewer")
            favorites = self.resource_catalog_repository.favorite_resource_ids(
                transaction=conn, tenant_id=actor.tenant_id, actor_user_id=actor.actor_user_id
            )
            return {"resource": resource_payload(row, is_favorite=row["id"] in favorites)}

    def move_resource(
        self,
        rid: str,
        *,
        project_id: str,
        folder_id: str | None,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        request = {"rid": rid, "projectId": project_id, "folderId": folder_id}
        return self._mutation(
            ctx,
            "resources.move",
            idempotency_key,
            request,
            lambda conn, actor, now: catalog_mutations.move_resource_mutation(
                self, conn, actor, rid, project_id, folder_id, now, idempotency_key
            ),
        )

    def trash_resource(self, rid: str, *, idempotency_key: str, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._resource_status(rid, "trashed", "resources.trash", idempotency_key, ctx)

    def restore_resource(
        self, rid: str, *, idempotency_key: str, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self._resource_status(rid, "active", "resources.restore", idempotency_key, ctx)

    def favorite_resource(
        self, rid: str, *, idempotency_key: str, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        request = {"rid": rid}
        return self._mutation(
            ctx,
            "resources.favorite",
            idempotency_key,
            request,
            lambda conn, actor, now: catalog_mutations.favorite_resource_mutation(
                self, conn, actor, rid, now, idempotency_key, should_add=True
            ),
        )

    def unfavorite_resource(
        self, rid: str, *, idempotency_key: str, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        request = {"rid": rid}
        return self._mutation(
            ctx,
            "resources.unfavorite",
            idempotency_key,
            request,
            lambda conn, actor, now: catalog_mutations.favorite_resource_mutation(
                self, conn, actor, rid, now, idempotency_key, should_add=False
            ),
        )

    def reconcile(
        self, *, project_id: str | None, idempotency_key: str, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        request = {"projectId": project_id}
        return self._mutation(
            ctx,
            "resources.reconcile",
            idempotency_key,
            request,
            lambda conn, actor, now: catalog_mutations.reconcile_mutation(
                self, conn, actor, project_id, now, idempotency_key
            ),
        )

    def _folder_status(
        self,
        project_id: str,
        folder_id: str,
        status: str,
        scope: str,
        idempotency_key: str,
        ctx: RequestContext | None,
    ) -> dict[str, object]:
        request = {"projectId": project_id, "folderId": folder_id, "status": status}
        return self._mutation(
            ctx,
            scope,
            idempotency_key,
            request,
            lambda conn, actor, now: catalog_mutations.set_folder_status_mutation(
                self, conn, actor, project_id, folder_id, status, now, idempotency_key
            ),
        )

    def _resource_status(
        self, rid: str, status: str, scope: str, idempotency_key: str, ctx: RequestContext | None
    ) -> dict[str, object]:
        request = {"rid": rid, "status": status}
        return self._mutation(
            ctx,
            scope,
            idempotency_key,
            request,
            lambda conn, actor, now: catalog_mutations.set_resource_status_mutation(
                self, conn, actor, rid, status, now, idempotency_key
            ),
        )

    def _mutation(
        self,
        ctx: RequestContext | None,
        scope: str,
        idempotency_key: str,
        request: Mapping[str, object],
        writer: MutationWriter,
    ) -> dict[str, object]:
        actor = self._ctx(ctx)
        request_hash = stable_request_hash(request)
        with self.engine.begin() as conn:
            existing = self.resource_catalog_repository.idempotency_record(
                transaction=conn, tenant_id=actor.tenant_id, scope=scope, idempotency_key=idempotency_key
            )
            if existing is not None:
                return replay_or_conflict(existing, request_hash)
            now = _now()
            response = writer(conn, actor, now)
            self.resource_catalog_repository.store_idempotency_record(
                transaction=conn,
                tenant_id=actor.tenant_id,
                scope=scope,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response_payload=response,
                now=now,
            )
            return response

    def _resource_project_id(self, conn: TransactionContext, ctx: RequestContext, project_id: object, now: str) -> str:
        return catalog_access.resource_project_id(self, conn, ctx, project_id, now)

    def _personal_project(self, conn: TransactionContext, ctx: RequestContext) -> ProjectRow | None:
        return catalog_access.personal_project(self, conn, ctx)

    def _readable_project_ids(self, conn: TransactionContext, ctx: RequestContext, project_id: str | None) -> list[str]:
        return catalog_access.readable_project_ids(self, conn, ctx, project_id)

    def _project_if_discoverable(
        self, conn: TransactionContext, ctx: RequestContext, project: ProjectRow
    ) -> dict[str, object] | None:
        result = catalog_access.project_if_discoverable(self, conn, ctx, project)
        return project_payload(*result) if result is not None else None

    def _project_with_role(
        self, conn: TransactionContext, ctx: RequestContext, project_id: str
    ) -> tuple[ProjectRow, ProjectRole | None]:
        return catalog_access.project_with_role(self, conn, ctx, project_id)

    def _require_project(
        self, conn: TransactionContext, ctx: RequestContext, project_id: str, role: ProjectRole
    ) -> ProjectRow:
        return catalog_access.require_project(self, conn, ctx, project_id, role)

    def _require_parent_folder(
        self, conn: TransactionContext, ctx: RequestContext, project_id: str, folder_id: str | None
    ) -> None:
        catalog_access.require_parent_folder(self, conn, ctx, project_id, folder_id)

    def _folder_by_id(self, conn: TransactionContext, ctx: RequestContext, folder_id: str) -> ProjectFolderRow:
        return catalog_access.folder_by_id(self, conn, ctx, folder_id)

    def _resource_by_rid(self, conn: TransactionContext, ctx: RequestContext, rid: str) -> ResourceRow:
        return catalog_access.resource_by_rid(self, conn, ctx, rid)

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
    ) -> None:
        catalog_access.audit(
            self.runtime_repository, conn, ctx, event_type, resource_type, resource_id, action, payload, now
        )

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
    ) -> None:
        catalog_access.outbox(
            self.runtime_repository, conn, ctx, event_type, aggregate_type, aggregate_id, payload, key, now
        )

    def _required_folder(self, row: ProjectFolderRow | None, folder_id: str) -> ProjectFolderRow:
        return catalog_access.required_folder(row, folder_id)

    def _required_resource(self, row: ResourceRow | None, rid: str) -> ResourceRow:
        return catalog_access.required_resource(row, rid)

    def _ctx(self, ctx: RequestContext | None) -> RequestContext:
        return ctx or RequestContext()


def _resource_request(
    resource_type: str,
    display_name: str,
    project_id: str | None,
    folder_id: str | None,
    source_surface: str,
    source_ref: str,
    operations_path: str | None,
    metadata: Mapping[str, object],
) -> dict[str, object]:
    return {
        "resourceType": require_text(resource_type, "resource_type"),
        "displayName": require_text(display_name, "display_name"),
        "projectId": project_id,
        "folderId": folder_id,
        "sourceSurface": require_text(source_surface, "source_surface"),
        "sourceRef": require_text(source_ref, "source_ref"),
        "operationsPath": operations_path,
        "metadata": dict(metadata),
    }
