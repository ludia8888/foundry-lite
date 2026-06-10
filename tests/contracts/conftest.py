"""Shared PostgreSQL testcontainer harness for repository contract tests.

Sprint 9.4: every transactional repository contract suite must run against both
SQLite (fast inner loop) and PostgreSQL (production parity). This module owns the
container lifecycle, schema bootstrap, and per-test isolation so each contract
test file only has to declare a `postgres` harness class.

Design notes:

- One `PostgresContainer` per pytest session (session-scoped fixture). Spinning
  up Postgres for every test is too slow; truncation between tests is enough.
- Schema is created once via `schema.metadata.create_all` on first use.
- Between tests we TRUNCATE every table in the public schema with
  `RESTART IDENTITY CASCADE`. This is faster than dropping/recreating and keeps
  primary-key sequences clean.
- Per-test default tenant row is inserted to match SQLite harness behaviour.
- The container is shared, but each test gets a *fresh* `Engine` to avoid
  connection pool contamination across tests that exercise transactional
  contexts.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from foundry_lite.infrastructure import schema as db
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from testcontainers.postgres import PostgresContainer


def postgres_enabled() -> bool:
    """Sprint 9.4 contract: postgres pairing runs unless explicitly opted out.

    The skip flag exists only for environments without Docker (e.g. a developer
    laptop with colima paused). ``scripts/ci_gate.sh`` rejects the flag so
    release/CI evidence cannot accidentally omit PostgreSQL parity.
    """

    return os.environ.get("FOUNDRY_LITE_SKIP_POSTGRES_CONTRACTS") != "1"


skip_if_no_postgres = pytest.mark.skipif(
    not postgres_enabled(),
    reason="postgres contract tests disabled via FOUNDRY_LITE_SKIP_POSTGRES_CONTRACTS=1",
)


@pytest.fixture(scope="session")
def _postgres_container() -> Iterator[PostgresContainer]:
    if not postgres_enabled():
        pytest.skip("postgres contract tests disabled")
    container = PostgresContainer("postgres:16-alpine", driver="psycopg")
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="session")
def _postgres_url(_postgres_container: PostgresContainer) -> str:
    url = _postgres_container.get_connection_url()
    # testcontainers historically returns psycopg2 URL; force psycopg v3.
    if url.startswith("postgresql://") and "+psycopg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    bootstrap_engine = create_engine(url, future=True)
    db.create_database(bootstrap_engine)
    bootstrap_engine.dispose()
    return url


def _truncate_all(engine: Engine) -> None:
    table_names = [t.name for t in db.metadata.sorted_tables]
    if not table_names:
        return
    quoted = ", ".join(f'"{name}"' for name in table_names)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))


def _insert_default_tenant(engine: Engine) -> None:
    # Contract tests use both "tenant-test" (default) and "tenant-demo"
    # (audit/outbox/lineage records). PostgreSQL enforces FKs strictly,
    # whereas SQLite defaults to FOREIGN_KEYS=off, so the SQLite harness
    # got away with only tenant-test. Seed both tenants to match.
    with engine.begin() as conn:
        conn.execute(
            db.tenants.insert(),
            [
                {"id": "tenant-test", "name": "Test", "created_at": "2026-06-10T00:00:00Z"},
                {"id": "tenant-demo", "name": "Demo", "created_at": "2026-06-10T00:00:00Z"},
            ],
        )


@dataclass(frozen=True)
class PostgresFixture:
    engine: Engine


@pytest.fixture
def postgres_fixture(_postgres_url: str) -> Iterator[PostgresFixture]:
    """Per-test PostgreSQL engine with a clean schema and default tenant row."""

    engine = create_engine(_postgres_url, future=True)
    _truncate_all(engine)
    _insert_default_tenant(engine)
    try:
        yield PostgresFixture(engine=engine)
    finally:
        engine.dispose()
