"""expand the OSDK application MCP publication registry

Revision ID: f1b3c5d7e9a2
Revises: f0a2c4e6b8d1
Create Date: 2026-08-04 17:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "f1b3c5d7e9a2"
down_revision: str | Sequence[str] | None = "f0a2c4e6b8d1"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("osdk_application_clients", sa.Column("current_secret_id", sa.String(), nullable=True))
    op.create_table(
        "osdk_client_secret_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("app_id", sa.String(), nullable=False),
        sa.Column("client_row_id", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("vault_secret_name", sa.String(), nullable=False),
        sa.Column("vault_secret_version", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_by_user_id", sa.String(), nullable=False),
        sa.Column("rotation_reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("revoked_at", sa.String(), nullable=True),
        sa.Column("last_used_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "client_row_id",
            "vault_secret_version",
            name="uq_osdk_client_secret_version",
        ),
    )
    op.create_table(
        "osdk_mcp_servers",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("app_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("description_markdown", sa.String(), nullable=False),
        sa.Column("allowed_origins", sa.JSON(), nullable=False),
        sa.Column("last_activity_at", sa.String(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "app_id", name="uq_osdk_mcp_server_application"),
    )
    op.create_table(
        "osdk_mcp_sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("app_id", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("last_seen_at", sa.String(), nullable=False),
        sa.Column("terminated_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "osdk_mcp_session_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "session_id", "sequence", name="uq_osdk_mcp_session_event_sequence"),
    )
    op.create_table(
        "osdk_mcp_tool_activations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("app_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=False),
        sa.Column("tool_id", sa.String(), nullable=False),
        sa.Column("query_hash", sa.String(), nullable=False),
        sa.Column("activated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "app_id",
            "session_id",
            "client_id",
            "actor_user_id",
            "tool_id",
            name="uq_osdk_mcp_tool_activation",
        ),
    )
    if context.get_context().dialect.name == "postgresql":
        op.execute("ALTER TABLE osdk_client_secret_versions ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE osdk_client_secret_versions FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY osdk_client_secret_versions_tenant_isolation
            ON osdk_client_secret_versions
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )
        op.execute("ALTER TABLE osdk_mcp_servers ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE osdk_mcp_servers FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY osdk_mcp_servers_tenant_isolation
            ON osdk_mcp_servers
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )
        op.execute("ALTER TABLE osdk_mcp_tool_activations ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE osdk_mcp_tool_activations FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY osdk_mcp_tool_activations_tenant_isolation
            ON osdk_mcp_tool_activations
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )
        op.execute("ALTER TABLE osdk_mcp_sessions ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE osdk_mcp_sessions FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY osdk_mcp_sessions_tenant_isolation
            ON osdk_mcp_sessions
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )
        op.execute("ALTER TABLE osdk_mcp_session_events ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE osdk_mcp_session_events FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY osdk_mcp_session_events_tenant_isolation
            ON osdk_mcp_session_events
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )


def downgrade() -> None:
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
