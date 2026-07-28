"""expand durable successful-row cache for governed Pipeline semantics

Revision ID: c7e9a1b3d5f0
Revises: b3d5f7a9c1e4
Create Date: 2026-07-17 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "c7e9a1b3d5f0"
down_revision: str | Sequence[str] | None = "b3d5f7a9c1e4"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pipeline_semantic_row_cache",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("cache_key", sa.String(), nullable=False),
        sa.Column("request_fingerprint", sa.String(), nullable=False),
        sa.Column("pipeline_id", sa.String(), nullable=False),
        sa.Column("scope_kind", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(), nullable=False),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("descriptor_id", sa.String(), nullable=False),
        sa.Column("spec_version", sa.String(), nullable=False),
        sa.Column("cache_generation", sa.Integer(), nullable=False),
        sa.Column("resource_security_policy_fingerprint", sa.String(), nullable=False),
        sa.Column("model_alias", sa.String(), nullable=False),
        sa.Column("environment", sa.String(), nullable=False),
        sa.Column("resolved_model_id", sa.String(), nullable=False),
        sa.Column("resolved_model_revision", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("prompt_version_id", sa.String(), nullable=False),
        sa.Column("prompt_fingerprint", sa.String(), nullable=False),
        sa.Column("input_fingerprint", sa.String(), nullable=False),
        sa.Column("media_fingerprint", sa.String(), nullable=False),
        sa.Column("output_schema_fingerprint", sa.String(), nullable=False),
        sa.Column("config_fingerprint", sa.String(), nullable=False),
        sa.Column("output_value", sa.JSON(), nullable=False),
        sa.Column("model_evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "cache_key",
            name="uq_pipeline_semantic_row_cache_key",
        ),
    )
    if context.get_context().dialect.name == "postgresql":
        _enable_tenant_rls()


def downgrade() -> None:
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")


def _enable_tenant_rls() -> None:
    table = "pipeline_semantic_row_cache"
    condition = "tenant_id = current_setting('foundry_lite.tenant_id', true)"
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.execute(f"CREATE POLICY {table}_tenant_isolation ON {table} USING ({condition}) WITH CHECK ({condition})")
