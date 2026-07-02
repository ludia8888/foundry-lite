"""Shared SQLAlchemy metadata and PostgreSQL tenant-context setting."""

from __future__ import annotations

from sqlalchemy import MetaData

metadata = MetaData()
POSTGRES_TENANT_SETTING = "foundry_lite.tenant_id"
