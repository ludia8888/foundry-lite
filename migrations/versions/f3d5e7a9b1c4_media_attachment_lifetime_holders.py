"""expand immutable attachment lifetime holder ledger

Revision ID: f3d5e7a9b1c4
Revises: f2c4d6e8a0b3
Create Date: 2026-08-04 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "f3d5e7a9b1c4"
down_revision: str | Sequence[str] | None = "f2c4d6e8a0b3"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "media_attachment_associations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("media_item_version_id", sa.String(), nullable=False),
        sa.Column("holder_type", sa.String(), nullable=False),
        sa.Column("holder_id", sa.String(), nullable=False),
        sa.Column("first_action_run_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "media_item_version_id",
            "holder_type",
            "holder_id",
            name="uq_media_attachment_lifetime_holder",
        ),
    )
    op.create_index(
        "ix_media_attachment_associations_version",
        "media_attachment_associations",
        ["tenant_id", "media_item_version_id"],
    )
    if context.get_context().dialect.name == "postgresql":
        _enable_rls("media_attachment_associations")


def _enable_rls(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('foundry_lite.tenant_id', true)) "
            "WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))"
        )
    )


def downgrade() -> None:
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
