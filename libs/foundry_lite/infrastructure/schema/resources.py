"""Project, folder, and RID-backed resource catalog tables."""

from __future__ import annotations

from sqlalchemy import JSON, Column, String, Table, Text, UniqueConstraint

from foundry_lite.infrastructure.schema.base import metadata

projects = Table(
    "projects",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("rid", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("description", Text),
    Column("status", String, nullable=False),
    Column("created_by_user_id", String, nullable=False),
    Column("metadata", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "rid", name="uq_project_rid"),
)

project_grants = Table(
    "project_grants",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("project_id", String, nullable=False),
    Column("principal_type", String, nullable=False),
    Column("principal_id", String, nullable=False),
    Column("role", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint(
        "tenant_id",
        "project_id",
        "principal_type",
        "principal_id",
        name="uq_project_grant_principal",
    ),
)

project_folders = Table(
    "project_folders",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("rid", String, nullable=False),
    Column("project_id", String, nullable=False),
    Column("parent_folder_id", String),
    Column("display_name", String, nullable=False),
    Column("status", String, nullable=False),
    Column("created_by_user_id", String, nullable=False),
    Column("metadata", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "rid", name="uq_project_folder_rid"),
)

resources = Table(
    "resources",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("rid", String, nullable=False),
    Column("resource_type", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("project_id", String, nullable=False),
    Column("folder_id", String),
    Column("source_surface", String, nullable=False),
    Column("source_ref", String, nullable=False),
    Column("operations_path", String),
    Column("status", String, nullable=False),
    Column("metadata", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "rid", name="uq_resource_rid"),
    UniqueConstraint("tenant_id", "source_surface", "source_ref", name="uq_resource_source_ref"),
)

resource_favorites = Table(
    "resource_favorites",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("resource_id", String, nullable=False),
    Column("actor_user_id", String, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint("tenant_id", "resource_id", "actor_user_id", name="uq_resource_favorite_actor"),
)

resource_idempotency_records = Table(
    "resource_idempotency_records",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("scope", String, nullable=False),
    Column("idempotency_key", String, nullable=False),
    Column("request_hash", String, nullable=False),
    Column("response_payload", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "scope", "idempotency_key", name="uq_resource_idempotency_key"),
)
