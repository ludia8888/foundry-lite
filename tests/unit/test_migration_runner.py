from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import pytest
from alembic.config import Config
from foundry_lite.infrastructure.local_database_url import database_url_from_environment, local_database_url

from scripts.operations import run_migrations

ROOT = Path(__file__).resolve().parents[2]


def test_migration_runner_passes_locked_connection_to_alembic(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migrated.db'}"
    observed_connections: list[object] = []

    def upgrade(config: Config, revision: str) -> None:
        observed_connections.append(config.attributes["connection"])
        assert revision == "head"

    result = run_migrations.run_migrations_with_singleton_lock(database_url=database_url, upgrade=upgrade)

    assert result.has_acquired_lock is True
    assert result.has_run_migration is True
    assert result.is_lock_busy is False
    assert len(observed_connections) == 1


def test_alembic_does_not_disable_existing_application_loggers(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migrated.db'}"
    application_logger = logging.getLogger("foundry_lite.regression.migration_logging")
    application_logger.disabled = False

    run_migrations.run_migrations_with_singleton_lock(database_url=database_url)

    assert application_logger.disabled is False


def test_failed_migration_leaves_operator_evidence(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migrated.db'}"
    evidence_output = tmp_path / "migration_run.json"

    def failing_upgrade(_config: Config, _revision: str) -> None:
        raise RuntimeError("synthetic migration failure")

    with pytest.raises(RuntimeError, match="synthetic migration failure"):
        run_migrations.run_migrations_with_singleton_lock(
            database_url=database_url,
            upgrade=failing_upgrade,
            evidence_output=evidence_output,
        )

    evidence = json.loads(evidence_output.read_text(encoding="utf-8"))
    assert evidence["status"] == "failed"
    assert evidence["revision"] == "head"
    assert evidence["has_acquired_lock"] is True
    assert evidence["has_completed_migration"] is False
    assert evidence["is_lock_busy"] is False
    assert evidence["error_type"] == "RuntimeError"
    assert evidence["error_message"] == "synthetic migration failure"


def test_migration_runner_evidence_masks_database_password(tmp_path: Path) -> None:
    evidence_output = tmp_path / "migration_run.json"
    result = run_migrations.MigrationRunResult(
        database_url="postgresql+psycopg://foundry:super-secret@example.test/foundry",
        revision="head",
        has_acquired_lock=True,
        has_run_migration=True,
        is_lock_busy=False,
    )

    run_migrations.write_migration_evidence(evidence_output, result=result)

    evidence = json.loads(evidence_output.read_text(encoding="utf-8"))
    assert evidence["status"] == "succeeded"
    assert "super-secret" not in evidence["database_url"]
    assert "***" in evidence["database_url"]


def test_migration_connection_failure_leaves_redacted_operator_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = "postgresql+psycopg://foundry:super-secret@example.test/foundry"
    evidence_output = tmp_path / "migration_run.json"

    def fail_before_connect(url: str, *, lock_timeout_seconds: float):  # noqa: ANN202 - pytest monkeypatch stub
        del lock_timeout_seconds
        raise RuntimeError(f"could not connect to {url}; password=super-secret")

    monkeypatch.setattr(run_migrations, "create_engine_for_lock", fail_before_connect)

    with pytest.raises(RuntimeError, match="could not connect"):
        run_migrations.run_migrations_with_singleton_lock(
            database_url=database_url,
            evidence_output=evidence_output,
        )

    evidence = json.loads(evidence_output.read_text(encoding="utf-8"))
    assert evidence["status"] == "failed"
    assert evidence["has_acquired_lock"] is False
    assert evidence["has_completed_migration"] is False
    assert evidence["error_type"] == "RuntimeError"
    assert "super-secret" not in evidence["database_url"]
    assert "super-secret" not in evidence["error_message"]
    assert database_url not in evidence["error_message"]


def test_migration_is_singleton_for_concurrent_sqlite_jobs(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migrated.db'}"
    first_started = threading.Event()
    release_first = threading.Event()
    first_result: list[run_migrations.MigrationRunResult] = []
    executed_revisions: list[str] = []

    def slow_upgrade(config: Config, revision: str) -> None:
        assert config.attributes["connection"] is not None
        executed_revisions.append(revision)
        first_started.set()
        assert release_first.wait(timeout=5)

    def run_first_job() -> None:
        first_result.append(
            run_migrations.run_migrations_with_singleton_lock(
                database_url=database_url,
                lock_timeout_seconds=0.0,
                upgrade=slow_upgrade,
            )
        )

    first = threading.Thread(target=run_first_job)
    first.start()
    assert first_started.wait(timeout=5)

    second = run_migrations.run_migrations_with_singleton_lock(
        database_url=database_url,
        lock_timeout_seconds=0.0,
        upgrade=lambda _config, revision: executed_revisions.append(f"second:{revision}"),
    )
    release_first.set()
    first.join(timeout=5)

    assert not first.is_alive()
    assert first_result[0].has_run_migration is True
    assert second.is_lock_busy is True
    assert second.has_run_migration is False
    assert executed_revisions == ["head"]


def test_migration_target_matches_the_database_the_api_runtime_opens(tmp_path: Path) -> None:
    """Regression: the runner defaulted to ./foundry-lite.db while the API opened FOUNDRY_LITE_HOME's.

    Migrations then reported success against a file nobody serves, and the served database
    silently stayed behind on an older revision.
    """

    home = tmp_path / "state"
    environ = {"FOUNDRY_LITE_HOME": str(home)}

    runtime_target = local_database_url(environ["FOUNDRY_LITE_HOME"])
    migration_target = database_url_from_environment(environ)

    assert migration_target == runtime_target
    assert migration_target == f"sqlite:///{(home / 'foundry-lite.db').resolve()}"


def test_explicit_database_url_wins_over_the_local_default() -> None:
    explicit = "postgresql+psycopg://user@example.test/foundry"

    assert run_migrations.database_url_from_env(explicit) == explicit
    assert database_url_from_environment({"FOUNDRY_LITE_DB_URL": explicit}) == explicit


def test_migration_job_singleton_no_app_start_race() -> None:
    startup_paths = [
        ROOT / "apps" / "api" / "foundry_lite_api" / "main.py",
        ROOT / "apps" / "worker" / "foundry_lite_worker" / "stream_archive.py",
        ROOT / "libs" / "foundry_lite" / "infrastructure" / "local_runtime.py",
    ]

    for path in startup_paths:
        source = path.read_text(encoding="utf-8")
        assert "scripts.operations.run_migrations" not in source
        assert "alembic.command" not in source
