"""source connection tests

Revision ID: f0a2c4e6b8d0
Revises: d8e0f2a4c6b9
Create Date: 2026-07-13 23:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "f0a2c4e6b8d0"
down_revision: str | Sequence[str] | None = "d8e0f2a4c6b9"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_connection_tests",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("source_name", sa.String(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("config_fingerprint", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("operations_path", sa.String(), nullable=False),
        sa.Column("started_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_name",
            "idempotency_key",
            name="uq_source_connection_test_key",
        ),
    )
    if context.get_context().dialect.name == "postgresql":
        _apply_rls()


def _apply_rls() -> None:
    condition = "tenant_id = current_setting('foundry_lite.tenant_id', true)"
    op.execute("ALTER TABLE source_connection_tests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE source_connection_tests FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS source_connection_tests_tenant_isolation ON source_connection_tests")
    op.execute(
        "CREATE POLICY source_connection_tests_tenant_isolation ON source_connection_tests "
        f"USING ({condition}) WITH CHECK ({condition})"
    )


def downgrade() -> None:
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
