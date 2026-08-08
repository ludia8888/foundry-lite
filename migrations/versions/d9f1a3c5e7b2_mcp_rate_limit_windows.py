"""expand durable shared MCP fixed-window rate limits

Revision ID: d9f1a3c5e7b2
Revises: c8e0f2a4b6e1
Create Date: 2026-08-09 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "d9f1a3c5e7b2"
down_revision: str | Sequence[str] | None = "c8e0f2a4b6e1"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_rate_limit_windows",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("plane", sa.String(), nullable=False),
        sa.Column("application_id", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=False),
        sa.Column("limit_scope", sa.String(), nullable=False),
        sa.Column("window_started_at_epoch", sa.BigInteger(), nullable=False),
        sa.Column("window_expires_at_epoch", sa.BigInteger(), nullable=False),
        sa.Column("limit_value", sa.Integer(), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("denied_count", sa.Integer(), nullable=False),
        sa.Column("last_request_id", sa.String(), nullable=False),
        sa.Column("last_denied_at", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.CheckConstraint("limit_value > 0", name="ck_mcp_rate_limit_positive_limit"),
        sa.CheckConstraint("window_seconds > 0", name="ck_mcp_rate_limit_positive_window"),
        sa.CheckConstraint("request_count >= 1", name="ck_mcp_rate_limit_request_count"),
        sa.CheckConstraint(
            "denied_count >= 0 AND denied_count <= request_count",
            name="ck_mcp_rate_limit_denied_count",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "plane",
            "application_id",
            "client_id",
            "actor_user_id",
            "limit_scope",
            "window_started_at_epoch",
            name="uq_mcp_rate_limit_window_identity",
        ),
    )
    op.create_index(
        "ix_mcp_rate_limit_window_expiry",
        "mcp_rate_limit_windows",
        ["tenant_id", "window_expires_at_epoch"],
        unique=False,
    )
    _enable_postgres_tenant_rls()


def _enable_postgres_tenant_rls() -> None:
    if context.get_context().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE mcp_rate_limit_windows ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE mcp_rate_limit_windows FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY mcp_rate_limit_windows_tenant_isolation
        ON mcp_rate_limit_windows
        USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
        WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
        """
    )


def downgrade() -> None:
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
