"""SQLAlchemy adapter for the Compass-style resource catalog."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from sqlalchemy import and_, delete, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from foundry_lite.application.ports.resource_catalog_repository import (
    ProjectFolderRecord,
    ProjectFolderRow,
    ProjectGrantRecord,
    ProjectGrantRow,
    ProjectRecord,
    ProjectRow,
    ResourceCandidate,
    ResourceIdempotencyRow,
    ResourceRecord,
    ResourceRow,
)
from foundry_lite.application.ports.transaction_context import StatusTransition
from foundry_lite.application.state_transitions import RESOURCE_CATALOG_RESTORE, RESOURCE_CATALOG_TRASH
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update


class SqlAlchemyResourceCatalogRepository:
    """Tenant-scoped Project/Folder/RID catalog persistence."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def insert_project(self, *, transaction: Any, record: ProjectRecord) -> ProjectRow:
        transaction.execute(db.projects.insert().values(_project_values(record)))
        return _required(
            self.project_by_id(transaction=transaction, tenant_id=record.tenant_id, project_id=record.project_id)
        )

    def project_by_id(self, *, transaction: Any, tenant_id: str, project_id: str) -> ProjectRow | None:
        row = (
            transaction.execute(
                select(db.projects).where(and_(db.projects.c.tenant_id == tenant_id, db.projects.c.id == project_id))
            )
            .mappings()
            .first()
        )
        return _row(row, ProjectRow)

    def list_projects(self, *, transaction: Any, tenant_id: str) -> list[ProjectRow]:
        rows = (
            transaction.execute(
                select(db.projects)
                .where(and_(db.projects.c.tenant_id == tenant_id, db.projects.c.status == "active"))
                .order_by(db.projects.c.display_name, db.projects.c.id)
            )
            .mappings()
            .all()
        )
        return [_cast(row, ProjectRow) for row in rows]

    def upsert_project_grant(self, *, transaction: Any, record: ProjectGrantRecord) -> ProjectGrantRow:
        existing = _grant_by_principal(transaction, record)
        if existing is None:
            transaction.execute(db.project_grants.insert().values(_grant_values(record)))
        else:
            transaction.execute(
                update(db.project_grants)
                .where(
                    and_(db.project_grants.c.tenant_id == record.tenant_id, db.project_grants.c.id == existing["id"])
                )
                .values(role=record.role, updated_at=record.updated_at)
            )
        return _required_grant(_grant_by_principal(transaction, record))

    def list_project_grants(self, *, transaction: Any, tenant_id: str, project_id: str) -> list[ProjectGrantRow]:
        rows = (
            transaction.execute(
                select(db.project_grants)
                .where(and_(db.project_grants.c.tenant_id == tenant_id, db.project_grants.c.project_id == project_id))
                .order_by(db.project_grants.c.principal_type, db.project_grants.c.principal_id)
            )
            .mappings()
            .all()
        )
        return [_cast(row, ProjectGrantRow) for row in rows]

    def insert_folder(self, *, transaction: Any, record: ProjectFolderRecord) -> ProjectFolderRow:
        transaction.execute(db.project_folders.insert().values(_folder_values(record)))
        return _required_folder(
            self.folder_by_id(transaction=transaction, tenant_id=record.tenant_id, folder_id=record.folder_id)
        )

    def folder_by_id(self, *, transaction: Any, tenant_id: str, folder_id: str) -> ProjectFolderRow | None:
        row = (
            transaction.execute(
                select(db.project_folders).where(
                    and_(db.project_folders.c.tenant_id == tenant_id, db.project_folders.c.id == folder_id)
                )
            )
            .mappings()
            .first()
        )
        return _row(row, ProjectFolderRow)

    def list_folders(
        self, *, transaction: Any, tenant_id: str, project_id: str, include_trashed: bool
    ) -> list[ProjectFolderRow]:
        conditions: list[Any] = [
            db.project_folders.c.tenant_id == tenant_id,
            db.project_folders.c.project_id == project_id,
        ]
        if not include_trashed:
            conditions.append(db.project_folders.c.status == "active")
        rows = (
            transaction.execute(
                select(db.project_folders)
                .where(and_(*conditions))
                .order_by(db.project_folders.c.display_name, db.project_folders.c.id)
            )
            .mappings()
            .all()
        )
        return [_cast(row, ProjectFolderRow) for row in rows]

    def update_folder(
        self, *, transaction: Any, tenant_id: str, folder_id: str, values: Mapping[str, object]
    ) -> ProjectFolderRow | None:
        _update_values(transaction, db.project_folders, tenant_id, folder_id, values)
        return self.folder_by_id(transaction=transaction, tenant_id=tenant_id, folder_id=folder_id)

    def upsert_resource(self, *, transaction: Any, record: ResourceRecord) -> ResourceRow:
        existing = self.resource_by_source(
            transaction=transaction,
            tenant_id=record.tenant_id,
            source_surface=record.source_surface,
            source_ref=record.source_ref,
        )
        if existing is None:
            transaction.execute(db.resources.insert().values(_resource_values(record)))
            return _required_resource(
                self.resource_by_rid(transaction=transaction, tenant_id=record.tenant_id, rid=record.rid)
            )
        transaction.execute(
            update(db.resources)
            .where(and_(db.resources.c.tenant_id == record.tenant_id, db.resources.c.id == existing["id"]))
            .values(
                resource_type=record.resource_type,
                display_name=record.display_name,
                project_id=record.project_id,
                folder_id=record.folder_id,
                operations_path=record.operations_path,
                metadata=record.metadata,
                updated_at=record.updated_at,
            )
        )
        return _required_resource(
            self.resource_by_rid(transaction=transaction, tenant_id=record.tenant_id, rid=existing["rid"])
        )

    def resource_by_rid(self, *, transaction: Any, tenant_id: str, rid: str) -> ResourceRow | None:
        row = (
            transaction.execute(
                select(db.resources).where(and_(db.resources.c.tenant_id == tenant_id, db.resources.c.rid == rid))
            )
            .mappings()
            .first()
        )
        return _row(row, ResourceRow)

    def resource_by_source(
        self, *, transaction: Any, tenant_id: str, source_surface: str, source_ref: str
    ) -> ResourceRow | None:
        row = (
            transaction.execute(
                select(db.resources).where(
                    and_(
                        db.resources.c.tenant_id == tenant_id,
                        db.resources.c.source_surface == source_surface,
                        db.resources.c.source_ref == source_ref,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _row(row, ResourceRow)

    def list_resources(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        project_ids: Sequence[str],
        folder_id: str | None,
        include_trashed: bool,
    ) -> list[ResourceRow]:
        if not project_ids:
            return []
        conditions: list[Any] = [db.resources.c.tenant_id == tenant_id, db.resources.c.project_id.in_(project_ids)]
        if folder_id is not None:
            conditions.append(db.resources.c.folder_id == folder_id)
        if not include_trashed:
            conditions.append(db.resources.c.status == "active")
        rows = (
            transaction.execute(
                select(db.resources).where(and_(*conditions)).order_by(db.resources.c.display_name, db.resources.c.id)
            )
            .mappings()
            .all()
        )
        return [_cast(row, ResourceRow) for row in rows]

    def update_resource(
        self, *, transaction: Any, tenant_id: str, resource_id: str, values: Mapping[str, object]
    ) -> ResourceRow | None:
        _update_values(transaction, db.resources, tenant_id, resource_id, values)
        row = (
            transaction.execute(
                select(db.resources).where(
                    and_(db.resources.c.tenant_id == tenant_id, db.resources.c.id == resource_id)
                )
            )
            .mappings()
            .first()
        )
        return _row(row, ResourceRow)

    def add_favorite(self, *, transaction: Any, tenant_id: str, resource_id: str, actor_user_id: str, now: str) -> None:
        try:
            transaction.execute(
                db.resource_favorites.insert().values(
                    id=f"favorite_{resource_id}_{actor_user_id}",
                    tenant_id=tenant_id,
                    resource_id=resource_id,
                    actor_user_id=actor_user_id,
                    created_at=now,
                )
            )
        except IntegrityError:
            return

    def remove_favorite(self, *, transaction: Any, tenant_id: str, resource_id: str, actor_user_id: str) -> bool:
        result = transaction.execute(
            delete(db.resource_favorites).where(
                and_(
                    db.resource_favorites.c.tenant_id == tenant_id,
                    db.resource_favorites.c.resource_id == resource_id,
                    db.resource_favorites.c.actor_user_id == actor_user_id,
                )
            )
        )
        return result.rowcount > 0

    def favorite_resource_ids(self, *, transaction: Any, tenant_id: str, actor_user_id: str) -> set[str]:
        rows = transaction.execute(
            select(db.resource_favorites.c.resource_id).where(
                and_(
                    db.resource_favorites.c.tenant_id == tenant_id,
                    db.resource_favorites.c.actor_user_id == actor_user_id,
                )
            )
        ).all()
        return {str(row[0]) for row in rows}

    def idempotency_record(
        self, *, transaction: Any, tenant_id: str, scope: str, idempotency_key: str
    ) -> ResourceIdempotencyRow | None:
        row = (
            transaction.execute(
                select(db.resource_idempotency_records).where(
                    and_(
                        db.resource_idempotency_records.c.tenant_id == tenant_id,
                        db.resource_idempotency_records.c.scope == scope,
                        db.resource_idempotency_records.c.idempotency_key == idempotency_key,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _row(row, ResourceIdempotencyRow)

    def store_idempotency_record(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        scope: str,
        idempotency_key: str,
        request_hash: str,
        response_payload: Mapping[str, object],
        now: str,
    ) -> None:
        transaction.execute(
            db.resource_idempotency_records.insert().values(
                id=f"resource_idem_{scope}_{idempotency_key}",
                tenant_id=tenant_id,
                scope=scope,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response_payload=dict(response_payload),
                created_at=now,
                updated_at=now,
            )
        )

    def existing_resource_candidates(self, *, transaction: Any, tenant_id: str) -> list[ResourceCandidate]:
        candidates = _dataset_candidates(transaction, tenant_id)
        candidates.extend(_source_candidates(transaction, tenant_id))
        candidates.extend(_pipeline_candidates(transaction, tenant_id))
        candidates.extend(_ontology_candidates(transaction, tenant_id))
        return candidates


def _dataset_candidates(transaction: Any, tenant_id: str) -> list[ResourceCandidate]:
    rows = (
        transaction.execute(
            select(db.datasets).where(and_(db.datasets.c.tenant_id == tenant_id, db.datasets.c.status == "active"))
        )
        .mappings()
        .all()
    )
    return [
        ResourceCandidate(
            resource_type="dataset",
            display_name=f"{row['namespace']}.{row['name']}",
            source_surface="dataset",
            source_ref=f"{row['namespace']}.{row['name']}",
            operations_path=f"/datasets/{row['namespace']}/{row['name']}",
            metadata={"datasetId": row["id"], "classification": row["classification"] or "public"},
        )
        for row in rows
    ]


def _source_candidates(transaction: Any, tenant_id: str) -> list[ResourceCandidate]:
    rows = (
        transaction.execute(select(db.source_connections).where(db.source_connections.c.tenant_id == tenant_id))
        .mappings()
        .all()
    )
    return [
        ResourceCandidate(
            resource_type="source",
            display_name=str(row["display_name"]),
            source_surface="source",
            source_ref=str(row["source_name"]),
            operations_path=f"/sources/{row['source_name']}",
            metadata={"sourceId": row["id"], "kind": row["kind"], "status": row["status"]},
        )
        for row in rows
    ]


def _pipeline_candidates(transaction: Any, tenant_id: str) -> list[ResourceCandidate]:
    pipeline_branches = getattr(db, "pipeline_branches", None)
    if pipeline_branches is None:
        return []
    rows = (
        transaction.execute(select(pipeline_branches).where(pipeline_branches.c.tenant_id == tenant_id))
        .mappings()
        .all()
    )
    return [
        ResourceCandidate(
            resource_type="pipeline",
            display_name=f"{row['pipeline_id']} / {row['name']}",
            source_surface="pipeline_branch",
            source_ref=str(row["id"]),
            operations_path=f"/pipelines/{row['pipeline_id']}/branches/{row['id']}",
            metadata={"pipelineId": row["pipeline_id"], "branchId": row["id"], "status": row["status"]},
        )
        for row in rows
    ]


def _ontology_candidates(transaction: Any, tenant_id: str) -> list[ResourceCandidate]:
    rows = (
        transaction.execute(select(db.ontology_versions).where(db.ontology_versions.c.tenant_id == tenant_id))
        .mappings()
        .all()
    )
    return [
        ResourceCandidate(
            resource_type="ontology",
            display_name=f"Ontology v{row['version_number']}",
            source_surface="ontology_version",
            source_ref=str(row["id"]),
            operations_path=f"/ontology/versions/{row['id']}",
            metadata={"versionNumber": row["version_number"], "status": row["status"]},
        )
        for row in rows
    ]


def _project_values(record: ProjectRecord) -> dict[str, object]:
    return {
        "id": record.project_id,
        "tenant_id": record.tenant_id,
        "rid": record.rid,
        "display_name": record.display_name,
        "description": record.description,
        "status": record.status,
        "created_by_user_id": record.created_by_user_id,
        "metadata": dict(record.metadata),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _grant_values(record: ProjectGrantRecord) -> dict[str, object]:
    return {
        "id": record.grant_id,
        "tenant_id": record.tenant_id,
        "project_id": record.project_id,
        "principal_type": record.principal_type,
        "principal_id": record.principal_id,
        "role": record.role,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _folder_values(record: ProjectFolderRecord) -> dict[str, object]:
    return {
        "id": record.folder_id,
        "tenant_id": record.tenant_id,
        "rid": record.rid,
        "project_id": record.project_id,
        "parent_folder_id": record.parent_folder_id,
        "display_name": record.display_name,
        "status": record.status,
        "created_by_user_id": record.created_by_user_id,
        "metadata": dict(record.metadata),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _resource_values(record: ResourceRecord) -> dict[str, object]:
    return {
        "id": record.resource_id,
        "tenant_id": record.tenant_id,
        "rid": record.rid,
        "resource_type": record.resource_type,
        "display_name": record.display_name,
        "project_id": record.project_id,
        "folder_id": record.folder_id,
        "source_surface": record.source_surface,
        "source_ref": record.source_ref,
        "operations_path": record.operations_path,
        "status": record.status,
        "metadata": dict(record.metadata),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _grant_by_principal(transaction: Any, record: ProjectGrantRecord) -> ProjectGrantRow | None:
    row = (
        transaction.execute(
            select(db.project_grants).where(
                and_(
                    db.project_grants.c.tenant_id == record.tenant_id,
                    db.project_grants.c.project_id == record.project_id,
                    db.project_grants.c.principal_type == record.principal_type,
                    db.project_grants.c.principal_id == record.principal_id,
                )
            )
        )
        .mappings()
        .first()
    )
    return _row(row, ProjectGrantRow)


def _update_values(
    transaction: Any,
    table: Any,
    tenant_id: str,
    row_id: str,
    values: Mapping[str, object],
) -> None:
    updates = dict(values)
    status = updates.pop("status", None)
    if status is not None:
        cas_status_update(
            transaction,
            table,
            tenant_id=tenant_id,
            row_id=row_id,
            transition=_catalog_status_transition(status),
            values=updates,
        )
        return
    transaction.execute(
        update(table).where(and_(table.c.tenant_id == tenant_id, table.c.id == row_id)).values(**updates)
    )


def _catalog_status_transition(status: object) -> StatusTransition:
    if status == "trashed":
        return RESOURCE_CATALOG_TRASH
    if status == "active":
        return RESOURCE_CATALOG_RESTORE
    return StatusTransition(("active", "trashed"), str(status))


def _cast[RowT](row: Any, row_type: type[RowT]) -> RowT:
    return cast(RowT, dict(row))


def _row[RowT](row: Any | None, row_type: type[RowT]) -> RowT | None:
    return _cast(row, row_type) if row is not None else None


def _required(row: ProjectRow | None) -> ProjectRow:
    if row is None:
        raise RuntimeError("project write did not return a row")
    return row


def _required_grant(row: ProjectGrantRow | None) -> ProjectGrantRow:
    if row is None:
        raise RuntimeError("project grant write did not return a row")
    return row


def _required_folder(row: ProjectFolderRow | None) -> ProjectFolderRow:
    if row is None:
        raise RuntimeError("project folder write did not return a row")
    return row


def _required_resource(row: ResourceRow | None) -> ResourceRow:
    if row is None:
        raise RuntimeError("resource write did not return a row")
    return row
