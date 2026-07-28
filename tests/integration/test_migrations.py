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
from sqlalchemy import MetaData, Table, create_engine, inspect, text

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _upgrade_fresh_db(database_url: str) -> None:
    command.upgrade(_migration_config(database_url), "head")


def _migration_config(database_url: str) -> Config:
    config = Config(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


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


def test_pipeline_v2_migration_matches_declared_execution_indexes(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'pipeline-v2-indexes.db'}"

    _upgrade_fresh_db(database_url)

    inspector = inspect(create_engine(database_url))
    for table_name in (
        "pipeline_node_runs",
        "pipeline_run_artifacts",
        "pipeline_preview_runs",
    ):
        declared = {index.name for index in db.metadata.tables[table_name].indexes}
        migrated = {str(index["name"]) for index in inspector.get_indexes(table_name)}
        assert migrated == declared, f"index drift in {table_name}"


def test_pipeline_v2_migration_backfills_existing_schedule_runtime_state(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'pipeline-v2-schedule-upgrade.db'}"
    config = _migration_config(database_url)
    command.upgrade(config, "f0a2c4e6b8d0")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        legacy_schedules = Table("pipeline_schedules", MetaData(), autoload_with=connection)
        connection.execute(
            legacy_schedules.insert(),
            [
                {
                    "id": "active-schedule",
                    "tenant_id": "tenant-a",
                    "pipeline_id": "pipeline-a",
                    "version_id": "version-a",
                    "schedule": {"type": "interval", "intervalMinutes": 15, "timezone": "Asia/Seoul"},
                    "enabled": True,
                    "updated_by": "user-a",
                    "updated_at": "2026-07-15T01:02:03Z",
                    "last_tick_at": None,
                },
                {
                    "id": "disabled-schedule",
                    "tenant_id": "tenant-a",
                    "pipeline_id": "pipeline-b",
                    "version_id": "version-b",
                    "schedule": {"cronExpression": "0 * * * *"},
                    "enabled": False,
                    "updated_by": "user-a",
                    "updated_at": "2026-07-15T02:03:04Z",
                    "last_tick_at": None,
                },
            ],
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        rows = {
            row.id: row
            for row in connection.execute(
                text(
                    """
                    select id, trigger_type, timezone, next_due_at, status, paused_reason
                    from pipeline_schedules
                    order by id
                    """
                )
            )
        }
    assert rows["active-schedule"].trigger_type == "interval"
    assert rows["active-schedule"].timezone == "Asia/Seoul"
    assert rows["active-schedule"].next_due_at == "2026-07-15T01:02:03Z"
    assert rows["active-schedule"].status == "active"
    assert rows["disabled-schedule"].trigger_type == "cron"
    assert rows["disabled-schedule"].timezone == "UTC"
    assert rows["disabled-schedule"].next_due_at is None
    assert rows["disabled-schedule"].status == "paused"
    assert rows["disabled-schedule"].paused_reason == "disabled_before_pipeline_v2_upgrade"


def test_semantic_row_cache_migration_keeps_tenant_key_uniqueness(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'pipeline-semantic-cache.db'}"

    _upgrade_fresh_db(database_url)

    inspector = inspect(create_engine(database_url))
    constraints = inspector.get_unique_constraints("pipeline_semantic_row_cache")
    names = {str(constraint["name"]) for constraint in constraints}
    assert names == {"uq_pipeline_semantic_row_cache_key"}


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
