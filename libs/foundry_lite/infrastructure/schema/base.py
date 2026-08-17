"""Shared SQLAlchemy metadata and PostgreSQL tenant-context setting."""

from __future__ import annotations

from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
POSTGRES_TENANT_SETTING = "foundry_lite.tenant_id"


def postgres_json_document_type() -> JSON:
    """Store documents as JSONB on PostgreSQL while retaining SQLite compatibility."""
    return JSON().with_variant(JSONB(), "postgresql")
