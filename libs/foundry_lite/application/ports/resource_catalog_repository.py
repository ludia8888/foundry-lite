"""Application port for Project/Folder/RID resource catalog persistence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import TransactionContext

ProjectRole = Literal["owner", "editor", "viewer", "discoverer"]
PrincipalType = Literal["user", "role", "group"]
ResourceStatus = Literal["active", "trashed"]
ResourceJson = Mapping[str, object]


class ProjectRow(TypedDict):
    id: str
    tenant_id: str
    rid: str
    display_name: str
    description: str | None
    status: str
    created_by_user_id: str
    metadata: ResourceJson
    created_at: str
    updated_at: str


class ProjectGrantRow(TypedDict):
    id: str
    tenant_id: str
    project_id: str
    principal_type: str
    principal_id: str
    role: str
    created_at: str
    updated_at: str


class ProjectFolderRow(TypedDict):
    id: str
    tenant_id: str
    rid: str
    project_id: str
    parent_folder_id: str | None
    display_name: str
    status: str
    created_by_user_id: str
    metadata: ResourceJson
    created_at: str
    updated_at: str


class ResourceRow(TypedDict):
    id: str
    tenant_id: str
    rid: str
    resource_type: str
    display_name: str
    project_id: str
    folder_id: str | None
    source_surface: str
    source_ref: str
    operations_path: str | None
    status: str
    metadata: ResourceJson
    created_at: str
    updated_at: str


class ResourceIdempotencyRow(TypedDict):
    id: str
    tenant_id: str
    scope: str
    idempotency_key: str
    request_hash: str
    response_payload: ResourceJson
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ProjectRecord:
    project_id: str
    tenant_id: str
    rid: str
    display_name: str
    description: str | None
    status: ResourceStatus
    created_by_user_id: str
    metadata: ResourceJson
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ProjectGrantRecord:
    grant_id: str
    tenant_id: str
    project_id: str
    principal_type: PrincipalType
    principal_id: str
    role: ProjectRole
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ProjectFolderRecord:
    folder_id: str
    tenant_id: str
    rid: str
    project_id: str
    parent_folder_id: str | None
    display_name: str
    status: ResourceStatus
    created_by_user_id: str
    metadata: ResourceJson
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ResourceRecord:
    resource_id: str
    tenant_id: str
    rid: str
    resource_type: str
    display_name: str
    project_id: str
    folder_id: str | None
    source_surface: str
    source_ref: str
    operations_path: str | None
    status: ResourceStatus
    metadata: ResourceJson
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ResourceCandidate:
    resource_type: str
    display_name: str
    source_surface: str
    source_ref: str
    operations_path: str | None
    metadata: ResourceJson


class ResourceCatalogRepository(Protocol):
    def insert_project(self, *, transaction: TransactionContext, record: ProjectRecord) -> ProjectRow: ...

    def project_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, project_id: str
    ) -> ProjectRow | None: ...

    def list_projects(self, *, transaction: TransactionContext, tenant_id: str) -> list[ProjectRow]: ...

    def upsert_project_grant(
        self, *, transaction: TransactionContext, record: ProjectGrantRecord
    ) -> ProjectGrantRow: ...

    def list_project_grants(
        self, *, transaction: TransactionContext, tenant_id: str, project_id: str
    ) -> list[ProjectGrantRow]: ...

    def insert_folder(self, *, transaction: TransactionContext, record: ProjectFolderRecord) -> ProjectFolderRow: ...

    def folder_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, folder_id: str
    ) -> ProjectFolderRow | None: ...

    def list_folders(
        self, *, transaction: TransactionContext, tenant_id: str, project_id: str, include_trashed: bool
    ) -> list[ProjectFolderRow]: ...

    def update_folder(
        self, *, transaction: TransactionContext, tenant_id: str, folder_id: str, values: Mapping[str, object]
    ) -> ProjectFolderRow | None: ...

    def upsert_resource(self, *, transaction: TransactionContext, record: ResourceRecord) -> ResourceRow: ...

    def resource_by_rid(self, *, transaction: TransactionContext, tenant_id: str, rid: str) -> ResourceRow | None: ...

    def resource_by_source(
        self, *, transaction: TransactionContext, tenant_id: str, source_surface: str, source_ref: str
    ) -> ResourceRow | None: ...

    def list_resources(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        project_ids: Sequence[str],
        folder_id: str | None,
        include_trashed: bool,
    ) -> list[ResourceRow]: ...

    def update_resource(
        self, *, transaction: TransactionContext, tenant_id: str, resource_id: str, values: Mapping[str, object]
    ) -> ResourceRow | None: ...

    def add_favorite(
        self, *, transaction: TransactionContext, tenant_id: str, resource_id: str, actor_user_id: str, now: str
    ) -> None: ...

    def remove_favorite(
        self, *, transaction: TransactionContext, tenant_id: str, resource_id: str, actor_user_id: str
    ) -> bool: ...

    def favorite_resource_ids(
        self, *, transaction: TransactionContext, tenant_id: str, actor_user_id: str
    ) -> set[str]: ...

    def idempotency_record(
        self, *, transaction: TransactionContext, tenant_id: str, scope: str, idempotency_key: str
    ) -> ResourceIdempotencyRow | None: ...

    def store_idempotency_record(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        scope: str,
        idempotency_key: str,
        request_hash: str,
        response_payload: ResourceJson,
        now: str,
    ) -> None: ...

    def existing_resource_candidates(
        self, *, transaction: TransactionContext, tenant_id: str
    ) -> list[ResourceCandidate]: ...
