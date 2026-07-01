"""add durable observability incidents

Revision ID: b8e0f2a4c6d8
Revises: a7d9e1f3b5c7
Create Date: 2026-07-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "b8e0f2a4c6d8"
down_revision: str | Sequence[str] | None = "a7d9e1f3b5c7"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "observability_incidents",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("detector_id", sa.String(), nullable=False),
        sa.Column("detector_type", sa.String(), nullable=False),
        sa.Column("config_version", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("owner", sa.String(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.String(), nullable=False),
        sa.Column("first_observed_at", sa.String(), nullable=False),
        sa.Column("last_observed_at", sa.String(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("evidence_links", sa.JSON(), nullable=False),
        sa.Column("threshold", sa.JSON(), nullable=False),
        sa.Column("acknowledged_at", sa.String(), nullable=True),
        sa.Column("acknowledged_by", sa.String(), nullable=True),
        sa.Column("resolved_at", sa.String(), nullable=True),
        sa.Column("resolved_by", sa.String(), nullable=True),
        sa.Column("resolution_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "dedupe_key", name="uq_observability_incident_dedupe"),
    )
    if context.get_context().dialect.name == "postgresql":
        op.execute("ALTER TABLE observability_incidents ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE observability_incidents FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY observability_incidents_tenant_isolation ON observability_incidents
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S56 forward-fix policy blocks destructive production downgrades.")
