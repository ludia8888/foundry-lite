"""add insight review execution idempotency key column

Revision ID: f4b6a8c0d2e1
Revises: e1a3c5d7f9b2
Create Date: 2026-07-30 00:00:00.000000

Promote the approval-execution idempotency key out of the ``review_metadata``
JSON blob into a dedicated, indexed column so the idempotency hot path can look
it up with a ``WHERE`` predicate instead of scanning every tenant review. The
JSON claim is still written on execution (it also carries the request and
proposal fingerprints); this column mirrors only the key for fast lookup and
matches the create/assignment/decision idempotency-key columns already on the
table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "f4b6a8c0d2e1"
down_revision: str | Sequence[str] | None = "e1a3c5d7f9b2"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("insight_reviews", sa.Column("execution_idempotency_key", sa.String(), nullable=True))
    if context.get_context().dialect.name == "postgresql":
        op.execute(
            "UPDATE insight_reviews "
            "SET execution_idempotency_key = review_metadata -> 'approvalExecution' ->> 'idempotencyKey' "
            "WHERE review_metadata -> 'approvalExecution' ->> 'idempotencyKey' IS NOT NULL"
        )
    else:
        op.execute(
            "UPDATE insight_reviews "
            "SET execution_idempotency_key = json_extract(review_metadata, '$.approvalExecution.idempotencyKey') "
            "WHERE json_extract(review_metadata, '$.approvalExecution.idempotencyKey') IS NOT NULL"
        )
    op.create_index(
        "uq_insight_review_execution_idempotency",
        "insight_reviews",
        ["tenant_id", "execution_idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
