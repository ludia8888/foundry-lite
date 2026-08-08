"""expand OSDK MCP sessions with a durable single-stream lease

Revision ID: c8e0f2a4b6e1
Revises: b7c9d1e3f5a2
Create Date: 2026-08-09 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8e0f2a4b6e1"
down_revision: str | Sequence[str] | None = "b7c9d1e3f5a2"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("osdk_mcp_sessions", sa.Column("stream_lease_id", sa.String(), nullable=True))
    op.add_column("osdk_mcp_sessions", sa.Column("stream_lease_expires_at", sa.String(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
