"""expand durable idempotency for Ontology branch Action Type mutations

Revision ID: f0a2c4e6b8d1
Revises: e8a0c2d4f6b9
Create Date: 2026-08-04 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "f0a2c4e6b8d1"
down_revision: str | Sequence[str] | None = "e8a0c2d4f6b9"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ontology_branch_action_idempotency_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=False),
        sa.Column("branch_id", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("request_fingerprint", sa.String(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "actor_user_id",
            "branch_id",
            "operation",
            "idempotency_key",
            name="uq_ontology_branch_action_idempotency",
        ),
    )
    if context.get_context().dialect.name == "postgresql":
        op.execute("ALTER TABLE ontology_branch_action_idempotency_records ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE ontology_branch_action_idempotency_records FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY ontology_branch_action_idempotency_records_tenant_isolation
            ON ontology_branch_action_idempotency_records
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )


def downgrade() -> None:
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
