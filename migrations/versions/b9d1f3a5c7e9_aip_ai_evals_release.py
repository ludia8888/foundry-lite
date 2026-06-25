"""add AIP eval evidence and release promotion guard (P0k)

Revision ID: b9d1f3a5c7e9
Revises: a8c0e2f4b6d9
Create Date: 2026-06-26 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "b9d1f3a5c7e9"
down_revision: str | Sequence[str] | None = "a8c0e2f4b6d9"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "ai_eval_suites",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("suite_api_name", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("axes_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "suite_api_name", "version", name="uq_ai_eval_suite_version"),
    )
    op.create_table(
        "ai_eval_cases",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("suite_id", sa.String(), nullable=False),
        sa.Column("case_api_name", sa.String(), nullable=False),
        sa.Column("axis", sa.String(), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("expected_json", sa.JSON(), nullable=False),
        sa.Column("rubric_json", sa.JSON(), nullable=False),
        sa.Column("tags_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "suite_id", "case_api_name", name="uq_ai_eval_case_name"),
    )
    op.create_table(
        "ai_eval_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("suite_id", sa.String(), nullable=False),
        sa.Column("agent_version_id", sa.String(), nullable=False),
        sa.Column("candidate_release_channel", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("min_score", sa.Float(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "ai_eval_results",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("eval_run_id", sa.String(), nullable=False),
        sa.Column("case_id", sa.String(), nullable=False),
        sa.Column("sample_index", sa.Integer(), nullable=False),
        sa.Column("axis", sa.String(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("evaluator", sa.String(), nullable=False),
        sa.Column("input_hash", sa.String(), nullable=False),
        sa.Column("expected_hash", sa.String(), nullable=False),
        sa.Column("actual_hash", sa.String(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("eval_run_id", "case_id", "sample_index", "axis", name="uq_ai_eval_result_sample"),
    )
    op.create_table(
        "ai_agent_releases",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("agent_version_id", sa.String(), nullable=False),
        sa.Column("release_channel", sa.String(), nullable=False),
        sa.Column("eval_run_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("policy_version", sa.String(), nullable=False),
        sa.Column("promoted_by", sa.String(), nullable=False),
        sa.Column("promoted_at", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "agent_version_id", "release_channel", name="uq_ai_agent_release_channel"),
    )
    if context.get_context().dialect.name == "postgresql":
        op.execute("ALTER TABLE ai_eval_suites ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE ai_eval_suites FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY ai_eval_suites_tenant_isolation ON ai_eval_suites
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )
        op.execute("ALTER TABLE ai_eval_cases ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE ai_eval_cases FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY ai_eval_cases_tenant_isolation ON ai_eval_cases
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )
        op.execute("ALTER TABLE ai_eval_runs ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE ai_eval_runs FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY ai_eval_runs_tenant_isolation ON ai_eval_runs
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )
        op.execute("ALTER TABLE ai_eval_results ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE ai_eval_results FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY ai_eval_results_tenant_isolation ON ai_eval_results
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )
        op.execute("ALTER TABLE ai_agent_releases ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE ai_agent_releases FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY ai_agent_releases_tenant_isolation ON ai_agent_releases
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
