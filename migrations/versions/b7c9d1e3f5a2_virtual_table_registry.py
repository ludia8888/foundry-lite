"""register virtual table pointers to external tables

Revision ID: b7c9d1e3f5a2
Revises: a5f7b9c1d3e6
Create Date: 2026-08-05 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "b7c9d1e3f5a2"
down_revision: str | Sequence[str] | None = "a5f7b9c1d3e6"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _create_virtual_table_registry()
    if context.get_context().dialect.name == "postgresql":
        _enable_rls("virtual_tables")


def _create_virtual_table_registry() -> None:
    # Pointer metadata only. External rows stay in the source system — that is the whole
    # point of a virtual table, so there is deliberately no row storage here.
    op.create_table(
        "virtual_tables",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("parent_rid", sa.String(), nullable=False),
        sa.Column("connection_rid", sa.String(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("pinned_schema", sa.JSON(), nullable=False),
        sa.Column("markings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.UniqueConstraint("tenant_id", "parent_rid", "name", name="uq_virtual_table_name"),
    )
    op.create_index(
        "ix_virtual_tables_tenant_connection",
        "virtual_tables",
        ["tenant_id", "connection_rid"],
    )


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
    raise NotImplementedError("production rollback uses forward-fix or restore runbooks, never an Alembic downgrade")
