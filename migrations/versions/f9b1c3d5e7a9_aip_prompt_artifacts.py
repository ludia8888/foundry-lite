"""add AIP encrypted prompt artifact receipts (P0v)

Revision ID: f9b1c3d5e7a9
Revises: b9d1f3a5c7e9
Create Date: 2026-06-26 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "f9b1c3d5e7a9"
down_revision: str | Sequence[str] | None = "b9d1f3a5c7e9"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "ai_prompt_artifacts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("ai_run_id", sa.String(), nullable=False),
        sa.Column("artifact_kind", sa.String(), nullable=False),
        sa.Column("artifact_ref", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("artifact_hash", sa.String(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("encryption_key_ref", sa.String(), nullable=False),
        sa.Column("encryption_algorithm", sa.String(), nullable=False),
        sa.Column("retention_until", sa.String(), nullable=False),
        sa.Column("legal_hold", sa.Boolean(), nullable=False),
        sa.Column("erasure_request_id", sa.String(), nullable=True),
        sa.Column("export_marking", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    if context.get_context().dialect.name == "postgresql":
        op.execute("ALTER TABLE ai_prompt_artifacts ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE ai_prompt_artifacts FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY ai_prompt_artifacts_tenant_isolation ON ai_prompt_artifacts
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
