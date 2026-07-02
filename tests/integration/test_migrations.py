"""Alembic migrations must reproduce the SQLAlchemy metadata schema.

Production upgrades run `alembic upgrade head` rather than create_all, so the
migration chain and the in-code schema must never drift: applying all migrations
to an empty database must yield exactly the tables and columns that
`schema.metadata` declares. When `schema.py` changes, a migration must change too
or this fails — the runtime guard companion to the schema-revision fingerprint.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from foundry_lite.infrastructure import schema as db
from sqlalchemy import create_engine, inspect, text

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _upgrade_fresh_db(database_url: str) -> None:
    config = Config(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def _script_head() -> str:
    config = Config(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head() or ""


def test_migrations_to_head_match_metadata_tables_and_columns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'migrated.db'}"
    monkeypatch.setenv("FOUNDRY_LITE_DB_URL", database_url)

    _upgrade_fresh_db(database_url)

    inspector = inspect(create_engine(database_url))
    migrated_tables = set(inspector.get_table_names()) - {"alembic_version"}
    assert migrated_tables == set(db.metadata.tables)

    for table_name, table in db.metadata.tables.items():
        migrated_columns = {column["name"] for column in inspector.get_columns(table_name)}
        expected_columns = {column.name for column in table.columns}
        assert migrated_columns == expected_columns, f"column drift in {table_name}"


def test_create_all_bootstrap_stamps_head_and_stays_migration_compatible(tmp_path: Path) -> None:
    """A create_all-bootstrapped database must join the Alembic lineage: it is stamped at
    head so a later `alembic upgrade head` is a no-op instead of failing with 'table already
    exists'. This pins the fix for create_all vs. Alembic drift."""
    database_url = f"sqlite:///{tmp_path / 'bootstrapped.db'}"
    engine = create_engine(database_url)

    db.create_database(engine)

    inspector = inspect(engine)
    assert inspector.has_table("alembic_version")
    with engine.connect() as connection:
        stamped = connection.execute(text("select version_num from alembic_version")).scalar()
    assert stamped == _script_head()

    # Must not raise "table already exists"; the DB is already at head.
    _upgrade_fresh_db(database_url)
