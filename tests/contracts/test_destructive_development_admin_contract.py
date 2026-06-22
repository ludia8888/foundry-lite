from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import (
    SqlAlchemyDestructiveDevelopmentAdmin,
    SqlAlchemyMetadataRepository,
)
from foundry_lite.infrastructure.repositories.metadata_repository import SchemaMutationDisabledError
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.engine import Engine


def _engine(tmp_path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{tmp_path / 'destructive.db'}", future=True)
    db.create_database(engine)
    return engine


def test_destructive_development_admin_contract_reset_schema_recreates_tables(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    tables_before = set(inspect(engine).get_table_names())
    assert tables_before

    SqlAlchemyDestructiveDevelopmentAdmin(engine).reset_schema()

    assert set(inspect(engine).get_table_names()) == tables_before


def test_destructive_development_admin_contract_reset_schema_clears_rows(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    SqlAlchemyMetadataRepository(engine).ensure_tenant(
        tenant_id="tenant-demo",
        name="Demo Tenant",
        created_at="2026-06-11T00:00:00Z",
    )
    with engine.begin() as conn:
        assert conn.execute(select(db.tenants.c.id)).first() is not None

    SqlAlchemyDestructiveDevelopmentAdmin(engine).reset_schema()

    with engine.begin() as conn:
        assert conn.execute(select(db.tenants.c.id)).first() is None


def test_destructive_development_admin_contract_reset_schema_disabled_raises(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    admin = SqlAlchemyDestructiveDevelopmentAdmin(engine, allow_schema_mutation=False)

    with pytest.raises(SchemaMutationDisabledError):
        admin.reset_schema()
