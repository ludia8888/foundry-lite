"""add OSDK idempotency and OAuth compromise evidence

Revision ID: e3f5a7c9d1b3
Revises: d2f4a6c8e0b1
Create Date: 2026-07-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "e3f5a7c9d1b3"
down_revision: str | Sequence[str] | None = "d2f4a6c8e0b1"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "osdk_developer_console_idempotency_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "operation",
            "idempotency_key",
            name="uq_osdk_developer_console_idempotency",
        ),
    )
    op.add_column("osdk_oauth_sessions", sa.Column("compromised_at", sa.String(), nullable=True))
    op.add_column("osdk_oauth_sessions", sa.Column("compromise_reason", sa.String(), nullable=True))
    if context.get_context().dialect.name == "postgresql":
        op.execute("ALTER TABLE osdk_developer_console_idempotency_records ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE osdk_developer_console_idempotency_records FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY osdk_developer_console_idempotency_records_tenant_isolation
            ON osdk_developer_console_idempotency_records
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("OSDK security hardening uses forward-fix-only schema changes.")
