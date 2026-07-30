from __future__ import annotations

import json
import threading
from pathlib import Path

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
        finally:
            verify_engine.dispose()
    finally:
        cleanup_engine = create_engine(server_url.set(database="postgres"), future=True, isolation_level="AUTOCOMMIT")
        try:
            with cleanup_engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
        finally:
            cleanup_engine.dispose()


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
