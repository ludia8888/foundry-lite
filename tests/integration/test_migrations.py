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
from foundry_lite.infrastructure import schema as db
from sqlalchemy import create_engine, inspect

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _upgrade_fresh_db(database_url: str) -> None:
    config = Config(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


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
