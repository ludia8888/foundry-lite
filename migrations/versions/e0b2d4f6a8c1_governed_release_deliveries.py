"""expand durable governed release external delivery ledger

Revision ID: e0b2d4f6a8c1
Revises: d9f1a3c5e7b2
Create Date: 2026-08-09 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "e0b2d4f6a8c1"
down_revision: str | Sequence[str] | None = "d9f1a3c5e7b2"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "governed_release_deliveries",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("application_id", sa.String(), nullable=False),
        sa.Column("proposal_id", sa.String(), nullable=False),
        sa.Column("release_kind", sa.String(), nullable=False),
        sa.Column("workflow_run_id", sa.String(), nullable=False),
        sa.Column("parent_delivery_id", sa.String(), nullable=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("target_ref", sa.JSON(), nullable=False),
        sa.Column("candidate_ref", sa.JSON(), nullable=True),
        sa.Column("environment", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("request_fingerprint", sa.String(), nullable=False),
        sa.Column("provider_operation_id", sa.String(), nullable=True),
        sa.Column("provider_resource_id", sa.String(), nullable=True),
        sa.Column("prior_resource_id", sa.String(), nullable=True),
        sa.Column("result_ref", sa.JSON(), nullable=True),
        sa.Column("error_ref", sa.JSON(), nullable=True),
        sa.Column("ai_run_id", sa.String(), nullable=False),
        sa.Column("binding_hash", sa.String(), nullable=False),
        sa.Column("execution_attempt", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("dispatch_started_at", sa.String(), nullable=True),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.CheckConstraint(
            "operation IN ('source_publish', 'source_merge', 'application_deploy', 'application_rollback')",
            name="ck_governed_release_delivery_operation",
        ),
        sa.CheckConstraint(
            "status IN ('prepared', 'dispatching', 'landed', 'absent', 'ambiguous', 'failed')",
            name="ck_governed_release_delivery_status",
        ),
        sa.CheckConstraint(
            "release_kind IN ('ontology', 'pipeline')",
            name="ck_governed_release_delivery_release_kind",
        ),
        sa.CheckConstraint(
            "(operation = 'source_publish' AND parent_delivery_id IS NULL AND workflow_run_id = ai_run_id) "
            "OR (operation <> 'source_publish' AND parent_delivery_id IS NOT NULL)",
            name="ck_governed_release_delivery_lineage_shape",
        ),
        sa.CheckConstraint("execution_attempt >= 0", name="ck_governed_release_delivery_attempt"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "operation",
            "idempotency_key",
            name="uq_governed_release_delivery_idempotency",
        ),
    )
    op.create_index(
        "ix_governed_release_delivery_proposal",
        "governed_release_deliveries",
        ["tenant_id", "proposal_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_governed_release_delivery_reconcile",
        "governed_release_deliveries",
        ["tenant_id", "status", "updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_governed_release_delivery_workflow",
        "governed_release_deliveries",
        ["tenant_id", "application_id", "workflow_run_id", "created_at"],
        unique=False,
    )
    _enable_postgres_tenant_rls()


def _enable_postgres_tenant_rls() -> None:
    if context.get_context().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE governed_release_deliveries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE governed_release_deliveries FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY governed_release_deliveries_tenant_isolation
        ON governed_release_deliveries
        USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
        WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
        """
    )


def downgrade() -> None:
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
