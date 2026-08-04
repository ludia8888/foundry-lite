"""expand AI FDE durable structured tool results

Revision ID: e8a0c2d4f6b9
Revises: d7f9a1c3e5b8
Create Date: 2026-08-04 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8a0c2d4f6b9"
down_revision: str | Sequence[str] | None = "d7f9a1c3e5b8"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ai_tool_calls", sa.Column("result_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
