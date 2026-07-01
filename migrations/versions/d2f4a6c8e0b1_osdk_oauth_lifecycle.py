"""add OSDK OAuth session and internal SDK lifecycle tables

Revision ID: d2f4a6c8e0b1
Revises: c1d3e5f7a9b0
Create Date: 2026-07-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "d2f4a6c8e0b1"
down_revision: str | Sequence[str] | None = "c1d3e5f7a9b0"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = (
    "osdk_sdk_release_channels",
    "osdk_sdk_compatibility_windows",
    "osdk_release_artifact_download_tokens",
    "osdk_oauth_authorization_codes",
    "osdk_oauth_sessions",
    "osdk_oauth_refresh_tokens",
)


def upgrade() -> None:
    """Upgrade schema."""
    _extend_application_clients()
    _create_release_channels()
    _create_compatibility_windows()
    _create_download_tokens()
    _create_oauth_authorization_codes()
    _create_oauth_sessions()
    _create_oauth_refresh_tokens()
    if context.get_context().dialect.name == "postgresql":
        _install_postgres_rls()


def _extend_application_clients() -> None:
    op.add_column("osdk_application_clients", sa.Column("redirect_uris", sa.JSON(), nullable=True))
    op.add_column("osdk_application_clients", sa.Column("allowed_scopes", sa.JSON(), nullable=True))
    op.add_column("osdk_application_clients", sa.Column("access_token_ttl_seconds", sa.Integer(), nullable=True))
    op.add_column("osdk_application_clients", sa.Column("refresh_token_ttl_seconds", sa.Integer(), nullable=True))
    op.add_column("osdk_application_clients", sa.Column("updated_at", sa.String(), nullable=True))


def _create_release_channels() -> None:
    op.create_table(
        "osdk_sdk_release_channels",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("app_id", sa.String(), nullable=False),
        sa.Column("language", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("sdk_version_id", sa.String(), nullable=False),
        sa.Column("promoted_at", sa.String(), nullable=False),
        sa.Column("promoted_by_user_id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "app_id",
            "language",
            "channel",
            name="uq_osdk_sdk_release_channel",
        ),
    )


def _create_compatibility_windows() -> None:
    op.create_table(
        "osdk_sdk_compatibility_windows",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("app_id", sa.String(), nullable=False),
        sa.Column("language", sa.String(), nullable=False),
        sa.Column("from_sdk_version_id", sa.String(), nullable=False),
        sa.Column("to_sdk_version_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("supported_until", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "app_id",
            "language",
            "from_sdk_version_id",
            "to_sdk_version_id",
            name="uq_osdk_sdk_compatibility_window",
        ),
    )


def _create_download_tokens() -> None:
    op.create_table(
        "osdk_release_artifact_download_tokens",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("artifact_id", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("used_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "token_hash", name="uq_osdk_release_artifact_download_token_hash"),
    )


def _create_oauth_authorization_codes() -> None:
    op.create_table(
        "osdk_oauth_authorization_codes",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("code_hash", sa.String(), nullable=False),
        sa.Column("app_id", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("redirect_uri", sa.String(), nullable=False),
        sa.Column("code_challenge", sa.String(), nullable=False),
        sa.Column("code_challenge_method", sa.String(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("consumed_at", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "code_hash", name="uq_osdk_oauth_authorization_code_hash"),
    )


def _create_oauth_sessions() -> None:
    op.create_table(
        "osdk_oauth_sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("app_id", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("last_refreshed_at", sa.String(), nullable=True),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("revoked_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def _create_oauth_refresh_tokens() -> None:
    op.create_table(
        "osdk_oauth_refresh_tokens",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("replaced_by_token_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("used_at", sa.String(), nullable=True),
        sa.Column("revoked_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "token_hash", name="uq_osdk_oauth_refresh_token_hash"),
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
    raise NotImplementedError("OSDK OAuth lifecycle uses forward-fix-only schema changes.")
