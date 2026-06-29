from __future__ import annotations

import json
import threading
from pathlib import Path

from alembic.config import Config

from scripts.operations import run_migrations
from tests.contracts.conftest import PostgresFixture


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
