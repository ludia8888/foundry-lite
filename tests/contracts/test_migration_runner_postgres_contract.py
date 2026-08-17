from __future__ import annotations

import json
import threading
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from scripts.operations import run_migrations
from tests.contracts.conftest import PostgresFixture


def test_postgres_migration_runner_commits_alembic_upgrade(_postgres_url: str, tmp_path: Path) -> None:
    """The runner must COMMIT the Alembic DDL, not roll it back while reporting success.

    Acquiring the advisory lock autobegins a transaction on the future engine, so
    migrations/env.py takes its ``in_transaction()`` branch and runs the upgrade
    without opening (or committing) a transaction of its own. When
    ``release_migration_lock`` returned for PostgreSQL without committing, closing
    the connection rolled every migration back while the evidence still recorded
    ``succeeded`` — a silent no-op deploy.

    The advisory-lock test above injects a stub ``upgrade`` and the shared
    ``postgres_fixture`` database is bootstrapped via ``create_all`` + stamp head
    (making ``upgrade head`` a no-op), so neither can observe persistence. This test
    runs the real Alembic chain against a genuinely empty database instead.
    """
    server_url = make_url(_postgres_url)
    database_name = "migration_runner_commit_contract"
    admin_engine = create_engine(server_url.set(database="postgres"), future=True, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            # Identifier is a hardcoded literal, not user input.
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        admin_engine.dispose()

    target_url = server_url.set(database=database_name).render_as_string(hide_password=False)
    try:
        result = run_migrations.run_migrations_with_singleton_lock(
            database_url=target_url,
            evidence_output=tmp_path / "migration.json",
        )

        assert result.has_acquired_lock is True
        assert result.has_run_migration is True
        evidence = json.loads((tmp_path / "migration.json").read_text(encoding="utf-8"))
        assert evidence["status"] == "succeeded"

        # A fresh connection proves the DDL survived the runner closing its own.
        verify_engine = create_engine(target_url, future=True)
        try:
            with verify_engine.connect() as connection:
                inspector = inspect(connection)
                assert inspector.has_table("alembic_version"), (
                    "alembic_version missing: the migration transaction was rolled back"
                )
                assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                assert inspector.has_table("action_runs")
                assert inspector.has_table("datasets")
                _assert_jsonb_object_store_migration(connection)
        finally:
            verify_engine.dispose()
    finally:
        cleanup_engine = create_engine(server_url.set(database="postgres"), future=True, isolation_level="AUTOCOMMIT")
        try:
            with cleanup_engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
        finally:
            cleanup_engine.dispose()


def test_existing_json_object_rows_upgrade_to_jsonb_without_breaking_old_writers(_postgres_url: str) -> None:
    server_url = make_url(_postgres_url)
    database_name = "object_store_jsonb_upgrade_contract"
    _recreate_database(server_url, database_name)
    target_url = server_url.set(database=database_name).render_as_string(hide_password=False)
    config = _alembic_config(target_url)
    engine = create_engine(target_url, future=True)
    try:
        command.upgrade(config, "f2d4b6e8a0c3")
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO object_records (
                      id, tenant_id, object_type_id, object_type_api_name, object_id,
                      index_version, is_active, properties, base_properties, edit_properties,
                      property_versions, source_dataset_version_id, source_hash, object_version,
                      object_change_sequence, deleted, deletion_reason, created_at, updated_at
                    ) VALUES (
                      'legacy-1', 'tenant-legacy', 'ot-order', 'Order', 'O-1',
                      'active', true, CAST(:initial_properties AS json),
                      CAST(:initial_properties AS json), CAST(:empty_document AS json),
                      CAST(:property_versions AS json), 'dsv-1', 'hash-1', 1, 1,
                      false, NULL, '2026-08-18T00:00:00Z', '2026-08-18T00:00:00Z'
                    )
                    """
                ),
                {
                    "initial_properties": '{"status":"PENDING","amount":12}',
                    "empty_document": "{}",
                    "property_versions": '{"status":1,"amount":1}',
                },
            )

        command.upgrade(config, "head")

        with engine.begin() as connection:
            column_type, properties = connection.execute(
                text("SELECT pg_typeof(properties)::text, properties FROM object_records WHERE id = 'legacy-1'")
            ).one()
            assert column_type == "jsonb"
            assert properties == {"status": "PENDING", "amount": 12}
            connection.execute(
                text(
                    """
                    INSERT INTO object_records (
                      id, tenant_id, object_type_id, object_type_api_name, object_id,
                      index_version, is_active, properties, base_properties, edit_properties,
                      property_versions, source_dataset_version_id, source_hash, object_version,
                      object_change_sequence, deleted, deletion_reason, created_at, updated_at
                    ) VALUES (
                      'legacy-writer-2', 'tenant-legacy', 'ot-order', 'Order', 'O-2',
                      'active', true, CAST(:properties AS json), CAST(:properties AS json),
                      CAST(:empty_document AS json), CAST(:legacy_versions AS json), 'dsv-2', 'hash-2',
                      1, 2, false, NULL, '2026-08-18T00:00:00Z', '2026-08-18T00:00:00Z'
                    )
                    """
                ),
                {
                    "properties": '{"status":"APPROVED"}',
                    "empty_document": "{}",
                    "legacy_versions": '{"status":1}',
                },
            )
            stored = connection.execute(
                text("SELECT properties FROM object_records WHERE id = 'legacy-writer-2'")
            ).scalar_one()
            assert stored == {"status": "APPROVED"}
    finally:
        engine.dispose()
        _drop_database(server_url, database_name)


def _alembic_config(database_url: str) -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _recreate_database(server_url, database_name: str) -> None:
    admin_engine = create_engine(server_url.set(database="postgres"), future=True, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        admin_engine.dispose()


def _drop_database(server_url, database_name: str) -> None:
    cleanup_engine = create_engine(server_url.set(database="postgres"), future=True, isolation_level="AUTOCOMMIT")
    try:
        with cleanup_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
    finally:
        cleanup_engine.dispose()


def _assert_jsonb_object_store_migration(connection) -> None:
    jsonb_columns = connection.execute(
        text(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND udt_name = 'jsonb'
              AND table_name = ANY(:table_names)
            """
        ),
        {
            "table_names": [
                "object_records",
                "object_record_versions",
                "object_links",
                "object_edits",
                "object_conflicts",
                "object_sets",
            ]
        },
    ).all()
    assert len(jsonb_columns) == 15
    object_security = connection.execute(
        text(
            """
            SELECT count(*)
            FROM pg_class
            WHERE relname = ANY(:table_names)
              AND relrowsecurity
              AND relforcerowsecurity
            """
        ),
        {
            "table_names": [
                "object_records",
                "object_record_versions",
                "object_links",
                "object_edits",
                "object_conflicts",
                "object_sets",
                "object_change_counters",
                "object_index_row_hashes",
                "object_index_versions",
            ]
        },
    ).scalar_one()
    assert object_security == 9
    index_names = {
        row[0]
        for row in connection.execute(
            text(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND tablename IN ('object_records', 'object_links')
                """
            )
        )
    }
    assert {"ix_object_records_properties_gin", "ix_object_links_properties_gin"} <= index_names


def test_postgres_migration_runner_allows_one_live_advisory_lock_winner(
    postgres_fixture: PostgresFixture,
    tmp_path: Path,
) -> None:
    database_url = postgres_fixture.engine.url.render_as_string(hide_password=False)
    first_started = threading.Event()
    release_first = threading.Event()
    first_result: list[run_migrations.MigrationRunResult] = []
    first_errors: list[BaseException] = []
    executed_revisions: list[str] = []

    def slow_upgrade(config: Config, revision: str) -> None:
        assert config.attributes["connection"].dialect.name == "postgresql"
        executed_revisions.append(revision)
        first_started.set()
        assert release_first.wait(timeout=10)

    def run_first_job() -> None:
        try:
            first_result.append(
                run_migrations.run_migrations_with_singleton_lock(
                    database_url=database_url,
                    upgrade=slow_upgrade,
                    evidence_output=tmp_path / "first_migration.json",
                )
            )
        except BaseException as exc:
            first_errors.append(exc)
            first_started.set()

    first = threading.Thread(target=run_first_job)
    first.start()
    assert first_started.wait(timeout=10)
    assert first_errors == []

    try:
        second = run_migrations.run_migrations_with_singleton_lock(
            database_url=database_url,
            upgrade=lambda _config, revision: executed_revisions.append(f"second:{revision}"),
            evidence_output=tmp_path / "second_migration.json",
        )
    finally:
        release_first.set()
        first.join(timeout=10)

    assert not first.is_alive()
    assert first_errors == []
    assert first_result

    first_evidence = json.loads((tmp_path / "first_migration.json").read_text(encoding="utf-8"))
    second_evidence = json.loads((tmp_path / "second_migration.json").read_text(encoding="utf-8"))

    assert first_result[0].has_acquired_lock is True
    assert first_result[0].has_run_migration is True
    assert second.is_lock_busy is True
    assert second.has_acquired_lock is False
    assert second.has_run_migration is False
    assert executed_revisions == ["head"]
    assert first_evidence["status"] == "succeeded"
    assert second_evidence["status"] == "lock_busy"
    assert second_evidence["has_acquired_lock"] is False
    assert second_evidence["has_completed_migration"] is False
    username = postgres_fixture.engine.url.username
    password = postgres_fixture.engine.url.password
    if username and password:
        unmasked_authority = f"{username}:{password}@"
        assert unmasked_authority not in json.dumps(first_evidence)
        assert unmasked_authority not in json.dumps(second_evidence)
        assert f"{username}:***@" in first_evidence["database_url"]
        assert f"{username}:***@" in second_evidence["database_url"]
