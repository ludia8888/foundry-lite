"""expand governed release live attestation trust boundary

Revision ID: f2d4b6e8a0c3
Revises: e0b2d4f6a8c1
Create Date: 2026-08-09 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "f2d4b6e8a0c3"
down_revision: str | Sequence[str] | None = "e0b2d4f6a8c1"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "governed_release_live_attestations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("application_id", sa.String(), nullable=False),
        sa.Column("collector_run_id", sa.String(), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attestation_fingerprint", sa.String(), nullable=False),
        sa.Column("manifest_digest", sa.String(), nullable=False),
        sa.Column("evidence_digest", sa.String(), nullable=False),
        sa.Column("configuration_fingerprint", sa.String(), nullable=False),
        sa.Column("collector_version", sa.String(), nullable=False),
        sa.Column("source_revision", sa.String(), nullable=False),
        sa.Column("runtime_profile", sa.String(), nullable=False),
        sa.Column("database_backend", sa.String(), nullable=False),
        sa.Column("source_provider_profile", sa.String(), nullable=False),
        sa.Column("deployment_provider_profile", sa.String(), nullable=False),
        sa.Column("ontology_workflow_run_id", sa.String(), nullable=False),
        sa.Column("pipeline_workflow_run_id", sa.String(), nullable=False),
        sa.Column("ontology_proposal_id", sa.String(), nullable=False),
        sa.Column("pipeline_proposal_id", sa.String(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("checks_json", sa.JSON(), nullable=False),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("collected_at", sa.String(), nullable=False),
        sa.Column("valid_until", sa.String(), nullable=False),
        sa.CheckConstraint(
            "status = 'live_verified'",
            name="ck_governed_release_live_attestation_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "application_id",
            "collector_run_id",
            name="uq_governed_release_live_attestation_run",
        ),
    )
    op.create_index(
        "ix_governed_release_live_attestation_latest",
        "governed_release_live_attestations",
        ["tenant_id", "application_id", "collected_at"],
        unique=False,
    )
    _enable_postgres_tenant_rls()


def _enable_postgres_tenant_rls() -> None:
    if context.get_context().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE governed_release_live_attestations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE governed_release_live_attestations FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY governed_release_live_attestations_tenant_isolation
        ON governed_release_live_attestations
        USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
        WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
        """
    )


def downgrade() -> None:
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
