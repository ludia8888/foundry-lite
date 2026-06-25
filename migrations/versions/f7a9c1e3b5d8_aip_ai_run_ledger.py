"""add AIP AI run/event ledger (P0c canonical runtime plane)

Revision ID: f7a9c1e3b5d8
Revises: e5f7b9c1d3a5
Create Date: 2026-06-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a9c1e3b5d8"
down_revision: str | Sequence[str] | None = "e5f7b9c1d3a5"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "ai_sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("agent_version_id", sa.String(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("last_activity_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "ai_session_state_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("state_hash", sa.String(), nullable=False),
        sa.Column("created_by_run_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "ai_messages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("client_message_id", sa.String(), nullable=False),
        sa.Column("content_ref", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "session_id", "client_message_id", name="uq_ai_message_client_id"),
    )
    op.create_table(
        "ai_execution_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("agent_version_id", sa.String(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=False),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("trace_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("ontology_version_id", sa.String(), nullable=False),
        sa.Column("model_alias_version", sa.String(), nullable=False),
        sa.Column("resolved_model_id", sa.String(), nullable=False),
        sa.Column("resolved_model_revision", sa.String(), nullable=False),
        sa.Column("prompt_version_id", sa.String(), nullable=False),
        sa.Column("compiled_prompt_hash", sa.String(), nullable=False),
        sa.Column("tool_manifest_hash", sa.String(), nullable=False),
        sa.Column("context_manifest_hash", sa.String(), nullable=False),
        sa.Column("state_snapshot_hash", sa.String(), nullable=False),
        sa.Column("policy_snapshot_hash", sa.String(), nullable=False),
        sa.Column("budget_json", sa.JSON(), nullable=False),
        sa.Column("usage_json", sa.JSON(), nullable=True),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "ai_execution_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("ai_run_id", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload_ref", sa.String(), nullable=False),
        sa.Column("payload_hash", sa.String(), nullable=False),
        sa.Column("redacted_preview", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ai_run_id", "sequence", name="uq_ai_execution_event_sequence"),
    )
    op.create_table(
        "ai_model_calls",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("ai_run_id", sa.String(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model_revision", sa.String(), nullable=False),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("response_hash", sa.String(), nullable=False),
        sa.Column("provider_request_id", sa.String(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "ai_context_items",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("ai_run_id", sa.String(), nullable=False),
        sa.Column("context_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("source_resource_type", sa.String(), nullable=False),
        sa.Column("source_resource_id", sa.String(), nullable=False),
        sa.Column("source_version", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("retrieval_method", sa.String(), nullable=False),
        sa.Column("relevance_score", sa.Float(), nullable=False),
        sa.Column("security_partition", sa.String(), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("omission_reason", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "ai_tool_calls",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("ai_run_id", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("tool_id", sa.String(), nullable=False),
        sa.Column("tool_version", sa.String(), nullable=False),
        sa.Column("arguments_hash", sa.String(), nullable=False),
        sa.Column("effect", sa.String(), nullable=False),
        sa.Column("authorization_decision", sa.String(), nullable=False),
        sa.Column("confirmation_policy", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("result_hash", sa.String(), nullable=False),
        sa.Column("linked_action_run_id", sa.String(), nullable=True),
        sa.Column("started_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "ai_citations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("ai_run_id", sa.String(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("context_item_id", sa.String(), nullable=False),
        sa.Column("claim_span", sa.JSON(), nullable=False),
        sa.Column("citation_order", sa.Integer(), nullable=False),
        sa.Column("rendered_ref", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "ai_usage_ledger",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("ai_run_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model_revision", sa.String(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("recorded_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
