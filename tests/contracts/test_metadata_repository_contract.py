from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import pytest
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyDestructiveDevelopmentAdmin, SqlAlchemyMetadataRepository
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine


class MetadataRepositoryHarness(Protocol):
    repository: Any

    def tenant_count(self) -> int: ...

    def user_count(self) -> int: ...


@dataclass
class FakeMetadataRepository:
    tenants: dict[str, dict[str, Any]] = field(default_factory=dict)
    users: dict[str, dict[str, Any]] = field(default_factory=dict)
    initialized: bool = False

    def initialize_schema(self) -> None:
        self.initialized = True

    def ensure_tenant(self, *, tenant_id: str, name: str, created_at: str) -> None:
        self.tenants.setdefault(tenant_id, {"id": tenant_id, "name": name, "created_at": created_at})

    def ensure_user(
        self,
        *,
        user_id: str,
        tenant_id: str,
        email: str,
        roles: list[str],
        created_at: str,
    ) -> None:
        self.users.setdefault(
            user_id,
            {
                "id": user_id,
                "tenant_id": tenant_id,
                "email": email,
                "roles": roles,
                "created_at": created_at,
            },
        )


@dataclass
class FakeDestructiveDevelopmentAdmin:
    repository: FakeMetadataRepository

    def reset_schema(self) -> None:
        self.repository.tenants.clear()
        self.repository.users.clear()
        self.repository.initialized = True


@dataclass
class FakeMetadataRepositoryHarness:
    repository: FakeMetadataRepository

    def tenant_count(self) -> int:
        return len(self.repository.tenants)

    def user_count(self) -> int:
        return len(self.repository.users)


@dataclass
class SqlAlchemyMetadataRepositoryHarness:
    repository: SqlAlchemyMetadataRepository
    destructive_admin: SqlAlchemyDestructiveDevelopmentAdmin
    engine: Engine

    def tenant_count(self) -> int:
        with self.engine.begin() as conn:
            return len(conn.execute(select(db.tenants)).all())

    def user_count(self) -> int:
        with self.engine.begin() as conn:
            return len(conn.execute(select(db.users)).all())


@pytest.fixture(params=["sqlalchemy", "fake", "postgres"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> MetadataRepositoryHarness:
    if request.param == "fake":
        repository = FakeMetadataRepository()
        repository.initialize_schema()
        return FakeMetadataRepositoryHarness(repository)
    if request.param == "sqlalchemy":
        engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}", future=True)
        repository = SqlAlchemyMetadataRepository(engine)
        repository.initialize_schema()
        return SqlAlchemyMetadataRepositoryHarness(repository, SqlAlchemyDestructiveDevelopmentAdmin(engine), engine)
    postgres_fixture = request.getfixturevalue("postgres_fixture")
    engine = postgres_fixture.engine
    repository = SqlAlchemyMetadataRepository(engine)
    destructive_admin = SqlAlchemyDestructiveDevelopmentAdmin(engine)
    destructive_admin.reset_schema()
    return SqlAlchemyMetadataRepositoryHarness(repository, destructive_admin, engine)


def test_metadata_repository_contract_idempotent_tenant_and_user_bootstrap(
    harness: MetadataRepositoryHarness,
) -> None:
    harness.repository.ensure_tenant(
        tenant_id="tenant-demo",
        name="Demo Tenant",
        created_at="2026-06-11T00:00:00Z",
    )
    harness.repository.ensure_tenant(
        tenant_id="tenant-demo",
        name="Demo Tenant",
        created_at="2026-06-11T00:00:00Z",
    )
    harness.repository.ensure_user(
        user_id="user-demo",
        tenant_id="tenant-demo",
        email="demo@foundry-lite.local",
        roles=["admin"],
        created_at="2026-06-11T00:00:00Z",
    )
    harness.repository.ensure_user(
        user_id="user-demo",
        tenant_id="tenant-demo",
        email="demo@foundry-lite.local",
        roles=["admin"],
        created_at="2026-06-11T00:00:00Z",
    )

    assert harness.tenant_count() == 1
    assert harness.user_count() == 1


def test_metadata_repository_contract_excludes_destructive_reset(
    harness: MetadataRepositoryHarness,
) -> None:
    assert not hasattr(harness.repository, "reset_schema")


def test_destructive_development_admin_contract_reset_schema_clears_bootstrap_rows(
    harness: MetadataRepositoryHarness,
) -> None:
    harness.repository.ensure_tenant(
        tenant_id="tenant-demo",
        name="Demo Tenant",
        created_at="2026-06-11T00:00:00Z",
    )
    harness.repository.ensure_user(
        user_id="user-demo",
        tenant_id="tenant-demo",
        email="demo@foundry-lite.local",
        roles=["admin"],
        created_at="2026-06-11T00:00:00Z",
    )

    if isinstance(harness, FakeMetadataRepositoryHarness):
        destructive_admin = FakeDestructiveDevelopmentAdmin(harness.repository)
    else:
        destructive_admin = harness.destructive_admin

    destructive_admin.reset_schema()

    assert harness.tenant_count() == 0
    assert harness.user_count() == 0
