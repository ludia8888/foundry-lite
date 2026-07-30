"""Run Alembic migrations through the S55 singleton execution boundary.

This is the dedicated migration-job entrypoint. It keeps application and worker
startup from becoming hidden migration runners, then uses a database-level lock
so concurrent jobs cannot execute the migration chain at the same time.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import OperationalError

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_URL = "sqlite:///foundry-lite.db"
DEFAULT_EVIDENCE_OUTPUT = ROOT / "artifacts" / "operations" / "migration_run.json"
POSTGRES_LOCK_NAMESPACE = 5_735_501
POSTGRES_LOCK_ID = 55
SQLITE_LOCKED_MARKERS = ("database is locked", "database table is locked")

UpgradeFn = Callable[[Config, str], None]


@dataclass(frozen=True)
class MigrationRunResult:
    database_url: str
    revision: str
    has_acquired_lock: bool
    has_run_migration: bool
    is_lock_busy: bool


@dataclass(frozen=True)
class MigrationRunEvidence:
    status: str
    database_url: str
    revision: str
    has_acquired_lock: bool
    has_completed_migration: bool
    is_lock_busy: bool
    error_type: str | None = None
    error_message: str | None = None


def database_url_from_env(explicit_url: str | None = None) -> str:
    return explicit_url or os.getenv("FOUNDRY_LITE_DB_URL") or DEFAULT_DATABASE_URL


def run_migrations_with_singleton_lock(
    *,
    database_url: str,
    revision: str = "head",
    lock_timeout_seconds: float = 0.0,
    upgrade: UpgradeFn | None = None,
    evidence_output: Path | None = None,
) -> MigrationRunResult:
    has_acquired_lock = False
    try:
        engine = create_engine_for_lock(database_url, lock_timeout_seconds=lock_timeout_seconds)
        with engine.connect() as connection:
            if not try_acquire_migration_lock(connection):
                result = MigrationRunResult(
                    database_url=database_url,
                    revision=revision,
                    has_acquired_lock=False,
                    has_run_migration=False,
                    is_lock_busy=True,
                )
                write_migration_evidence(evidence_output, result=result)
                return result
            has_acquired_lock = True
            return _run_locked_migration(
                connection,
                database_url=database_url,
                revision=revision,
                upgrade=upgrade,
                evidence_output=evidence_output,
            )
    except Exception as exc:
        write_migration_evidence(
            evidence_output,
            result=MigrationRunResult(
                database_url=database_url,
                revision=revision,
                has_acquired_lock=has_acquired_lock,
                has_run_migration=False,
                is_lock_busy=False,
            ),
            error=exc,
        )
        raise


def _run_locked_migration(
    connection: Connection,
    *,
    database_url: str,
    revision: str,
    upgrade: UpgradeFn | None,
    evidence_output: Path | None,
) -> MigrationRunResult:
    has_migration_succeeded = False
    try:
        migration_upgrade = upgrade or _alembic_upgrade
        migration_upgrade(alembic_config(database_url, connection), revision)
        has_migration_succeeded = True
        result = MigrationRunResult(
            database_url=database_url,
            revision=revision,
            has_acquired_lock=True,
            has_run_migration=True,
            is_lock_busy=False,
        )
        write_migration_evidence(evidence_output, result=result)
        return result
    finally:
        release_migration_lock(connection, has_migration_succeeded=has_migration_succeeded)


def write_migration_evidence(
    output: Path | None,
    *,
    result: MigrationRunResult,
    error: BaseException | None = None,
) -> None:
    if output is None:
        return
    status = _evidence_status(result, error)
    evidence = MigrationRunEvidence(
        status=status,
        database_url=_safe_database_url(result.database_url),
        revision=result.revision,
        has_acquired_lock=result.has_acquired_lock,
        has_completed_migration=result.has_run_migration,
        is_lock_busy=result.is_lock_busy,
        error_type=type(error).__name__ if error is not None else None,
        error_message=_safe_error_message(error, result.database_url) if error is not None else None,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(asdict(evidence), indent=2, sort_keys=True), encoding="utf-8")


def _evidence_status(result: MigrationRunResult, error: BaseException | None) -> str:
    if error is not None:
        return "failed"
    if result.is_lock_busy:
        return "lock_busy"
    return "succeeded"


def _safe_database_url(database_url: str) -> str:
    try:
        return make_url(database_url).render_as_string(hide_password=True)
    except Exception:
        return "<unparseable>"


def _safe_error_message(error: BaseException, database_url: str) -> str:
    message = str(error)
    replacements = _sensitive_database_fragments(database_url)
    for fragment in replacements:
        message = message.replace(fragment, "***")
    return message


def _sensitive_database_fragments(database_url: str) -> set[str]:
    fragments = {database_url}
    try:
        url = make_url(database_url)
    except Exception:
        return {fragment for fragment in fragments if fragment}
    if isinstance(url.password, str) and url.password:
        fragments.add(url.password)
    rendered = url.render_as_string(hide_password=False)
    if rendered:
        fragments.add(rendered)
    return {fragment for fragment in fragments if fragment}


def create_engine_for_lock(database_url: str, *, lock_timeout_seconds: float) -> Engine:
    if database_url.startswith("sqlite"):
        return create_engine(database_url, future=True, connect_args={"timeout": lock_timeout_seconds})
    return create_engine(database_url, future=True)


def alembic_config(database_url: str, connection: Connection) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    config.attributes["connection"] = connection
    return config


def try_acquire_migration_lock(connection: Connection) -> bool:
    if connection.dialect.name == "postgresql":
        return _try_acquire_postgres_lock(connection)
    if connection.dialect.name == "sqlite":
        return _try_acquire_sqlite_lock(connection)
    raise RuntimeError(f"unsupported migration lock dialect: {connection.dialect.name}")


def release_migration_lock(connection: Connection, *, has_migration_succeeded: bool) -> None:
    if connection.dialect.name == "postgresql":
        # Acquiring the advisory lock autobegins a transaction on the future
        # engine, so Alembic runs its DDL inside it (migrations/env.py takes the
        # `in_transaction()` branch and does not open — or commit — its own).
        # Without an explicit commit here the `engine.connect()` block closes
        # with that transaction still open and PostgreSQL rolls every migration
        # back while the runner reports success. Session-level advisory locks are
        # not transactional, so ending the transaction does not release the lock
        # and the unlock below still applies.
        if connection.in_transaction():
            if has_migration_succeeded:
                connection.commit()
            else:
                connection.rollback()
        connection.execute(
            text("SELECT pg_advisory_unlock(:namespace, :lock_id)"),
            {"namespace": POSTGRES_LOCK_NAMESPACE, "lock_id": POSTGRES_LOCK_ID},
        )
        if connection.in_transaction():
            connection.commit()
        return
    if connection.dialect.name == "sqlite" and connection.in_transaction():
        if has_migration_succeeded:
            connection.commit()
        else:
            connection.rollback()
        return
    if connection.dialect.name != "sqlite":
        raise RuntimeError(f"unsupported migration lock dialect: {connection.dialect.name}")


def _try_acquire_postgres_lock(connection: Connection) -> bool:
    result = connection.execute(
        text("SELECT pg_try_advisory_lock(:namespace, :lock_id)"),
        {"namespace": POSTGRES_LOCK_NAMESPACE, "lock_id": POSTGRES_LOCK_ID},
    ).scalar_one()
    return bool(result)


def _try_acquire_sqlite_lock(connection: Connection) -> bool:
    try:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    except OperationalError as exc:
        message = str(exc).lower()
        if any(marker in message for marker in SQLITE_LOCKED_MARKERS):
            return False
        raise
    return True


def _alembic_upgrade(config: Config, revision: str) -> None:
    command.upgrade(config, revision)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Alembic migrations under the S55 singleton DB lock.")
    parser.add_argument("--database-url", default=None, help="Target DB URL. Defaults to FOUNDRY_LITE_DB_URL.")
    parser.add_argument("--revision", default="head", help="Alembic revision target.")
    parser.add_argument("--lock-timeout-seconds", type=float, default=0.0)
    parser.add_argument("--evidence-output", type=Path, default=DEFAULT_EVIDENCE_OUTPUT)
    parser.add_argument(
        "--allow-locked-skip",
        action="store_true",
        help="Exit 0 when another migration runner already holds the singleton lock.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    database_url = database_url_from_env(args.database_url)
    try:
        result = run_migrations_with_singleton_lock(
            database_url=database_url,
            revision=args.revision,
            lock_timeout_seconds=args.lock_timeout_seconds,
            evidence_output=args.evidence_output,
        )
    except Exception as exc:
        print(f"Migration runner failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if result.is_lock_busy:
        print("Migration runner skipped: another process holds the singleton migration lock.")
        return 0 if args.allow_locked_skip else 75
    print(f"Migration runner applied Alembic revision target {result.revision} under singleton lock.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
