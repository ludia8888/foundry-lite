"""add OSDK app scope and SDK release registry

Revision ID: c1d3e5f7a9b0
Revises: b8e0f2a4c6d8
Create Date: 2026-07-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "c1d3e5f7a9b0"
down_revision: str | Sequence[str] | None = "b8e0f2a4c6d8"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = (
    "osdk_applications",
    "osdk_application_clients",
    "osdk_application_resources",
    "osdk_sdk_versions",
    "osdk_release_artifacts",
)


def upgrade() -> None:
    """Upgrade schema."""
    _create_applications()
    _create_application_clients()
    _create_application_resources()
    _create_sdk_versions()
    _create_release_artifacts()
    if context.get_context().dialect.name == "postgresql":
        _install_postgres_rls()


def _create_applications() -> None:
    op.create_table(
        "osdk_applications",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("app_api_name", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("owner_user_id", sa.String(), nullable=False),
        sa.Column("created_idempotency_key", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "app_api_name", name="uq_osdk_application_api_name"),
        sa.UniqueConstraint("tenant_id", "created_idempotency_key", name="uq_osdk_application_create_key"),
    )


def _create_application_clients() -> None:
    op.create_table(
        "osdk_application_clients",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("app_id", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "client_id", name="uq_osdk_application_client_id"),
    )


def _create_application_resources() -> None:
    op.create_table(
        "osdk_application_resources",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("app_id", sa.String(), nullable=False),
        sa.Column("resource_type", sa.String(), nullable=False),
        sa.Column("resource_api_name", sa.String(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "app_id",
            "resource_type",
            "resource_api_name",
            name="uq_osdk_application_resource",
        ),
    )


def _create_sdk_versions() -> None:
    op.create_table(
        "osdk_sdk_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("app_id", sa.String(), nullable=False),
        sa.Column("language", sa.String(), nullable=False),
        sa.Column("package_name", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("generator_name", sa.String(), nullable=False),
        sa.Column("generator_version", sa.String(), nullable=False),
        sa.Column("ontology_fingerprint", sa.String(), nullable=False),
        sa.Column("resource_scope_snapshot", sa.JSON(), nullable=False),
        sa.Column("compatibility_status", sa.String(), nullable=False),
        sa.Column("release_status", sa.String(), nullable=False),
        sa.Column("artifact_hash", sa.String(), nullable=True),
        sa.Column("artifact_uri", sa.String(), nullable=True),
        sa.Column("created_idempotency_key", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("released_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "app_id", "language", "version", name="uq_osdk_sdk_version"),
        sa.UniqueConstraint("tenant_id", "app_id", "created_idempotency_key", name="uq_osdk_sdk_version_create_key"),
    )


def _create_release_artifacts() -> None:
    op.create_table(
        "osdk_release_artifacts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("app_id", sa.String(), nullable=False),
        sa.Column("sdk_version_id", sa.String(), nullable=False),
        sa.Column("artifact_kind", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("storage_uri", sa.String(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "sdk_version_id", "artifact_kind", name="uq_osdk_release_artifact"),
    )


def _install_postgres_rls() -> None:
    for table_name in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table_name}_tenant_isolation ON {table_name}
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("OSDK registry uses forward-fix-only schema changes.")
