from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from foundry_lite.application.ports.source_registry_repository import (
    SourceConnectionRecord,
    SourceConnectionRow,
    SourceConnectionUpdate,
    SourceRegistryRepository,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemySourceRegistryRepository
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine


class SourceRegistryHarness(Protocol):
    @property
    def repository(self) -> SourceRegistryRepository: ...

    def transaction(self) -> AbstractContextManager[Any]: ...

    def source_rows(self) -> list[dict[str, Any]]: ...


@dataclass
class FakeSourceRegistryRepository:
    sources: list[SourceConnectionRow] = field(default_factory=list)

    def create_source(self, *, transaction: Any, record: SourceConnectionRecord) -> None:
        del transaction
        self.sources.append(_source_row(record))

    def update_source(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        source_name: str,
        patch: SourceConnectionUpdate,
    ) -> SourceConnectionRow | None:
        del transaction
        for row in self.sources:
            if row["tenant_id"] == tenant_id and row["source_name"] == source_name:
                cast(dict[str, Any], row).update(_patch_values(patch))
                return cast(SourceConnectionRow, dict(row))
        return None

    def source_by_name(self, *, transaction: Any, tenant_id: str, source_name: str) -> SourceConnectionRow | None:
        del transaction
        for row in self.sources:
            if row["tenant_id"] == tenant_id and row["source_name"] == source_name:
                return cast(SourceConnectionRow, dict(row))
        return None

    def list_sources(self, *, tenant_id: str) -> list[SourceConnectionRow]:
        rows = [cast(SourceConnectionRow, dict(row)) for row in self.sources if row["tenant_id"] == tenant_id]
        return sorted(rows, key=lambda row: row["source_name"])


@dataclass
class FakeSourceRegistryHarness:
    repository: FakeSourceRegistryRepository = field(default_factory=FakeSourceRegistryRepository)

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        yield self

    def source_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.repository.sources]


@dataclass
class SqlAlchemySourceRegistryHarness:
    engine: Engine
    repository: SqlAlchemySourceRegistryRepository

    @classmethod
    def create(cls, tmp_path: Path) -> SqlAlchemySourceRegistryHarness:
        engine = create_engine(f"sqlite:///{tmp_path / 'source_registry.db'}", future=True)
        db.create_database(engine)
        return cls(engine=engine, repository=SqlAlchemySourceRegistryRepository(engine))

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.engine.begin() as conn:
            yield conn

    def source_rows(self) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            return [dict(row) for row in conn.execute(select(db.source_connections)).mappings().all()]


@pytest.fixture(params=["fake", "sqlalchemy"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> SourceRegistryHarness:
    if request.param == "fake":
        return FakeSourceRegistryHarness()
    return SqlAlchemySourceRegistryHarness.create(tmp_path)


def test_source_registry_create_list_get_update_are_tenant_scoped(harness: SourceRegistryHarness) -> None:
    with harness.transaction() as txn:
        harness.repository.create_source(transaction=txn, record=_source_record("tenant-a", "orders_csv"))
        harness.repository.create_source(transaction=txn, record=_source_record("tenant-b", "orders_csv"))
        updated = harness.repository.update_source(
            transaction=txn,
            tenant_id="tenant-a",
            source_name="orders_csv",
            patch=SourceConnectionUpdate(
                display_name="Orders CSV Updated",
                config_summary={"kind": "csv_upload", "fileName": "orders-v2.csv"},
                config_fingerprint="sha256:updated",
                last_run_id="sync_run_1",
                last_commit_ref={"versionId": "version_1"},
                updated_at="2026-06-28T00:00:01Z",
            ),
        )
        tenant_b_row = harness.repository.source_by_name(
            transaction=txn,
            tenant_id="tenant-b",
            source_name="orders_csv",
        )

    assert updated is not None
    assert updated["display_name"] == "Orders CSV Updated"
    assert updated["last_run_id"] == "sync_run_1"
    assert updated["last_commit_ref"] == {"versionId": "version_1"}
    assert [row["tenant_id"] for row in harness.repository.list_sources(tenant_id="tenant-a")] == ["tenant-a"]
    assert tenant_b_row is not None
    assert tenant_b_row["display_name"] == "Orders CSV"
    assert len(harness.source_rows()) == 2


def _source_record(tenant_id: str, source_name: str) -> SourceConnectionRecord:
    return SourceConnectionRecord(
        source_id=f"source-{tenant_id}",
        tenant_id=tenant_id,
        source_name=source_name,
        display_name="Orders CSV",
        kind="csv_upload",
        target_dataset_ref="raw.orders",
        target_media_set_id=None,
        status="active",
        config_summary={"kind": "csv_upload", "fileName": "orders.csv"},
        config_fingerprint=f"sha256:{tenant_id}",
        last_run_id=None,
        last_workflow_run_id=None,
        last_commit_ref=None,
        created_at="2026-06-28T00:00:00Z",
        updated_at="2026-06-28T00:00:00Z",
    )


def _source_row(record: SourceConnectionRecord) -> SourceConnectionRow:
    return {
        "id": record.source_id,
        "tenant_id": record.tenant_id,
        "source_name": record.source_name,
        "display_name": record.display_name,
        "kind": record.kind,
        "target_dataset_ref": record.target_dataset_ref,
        "target_media_set_id": record.target_media_set_id,
        "status": record.status,
        "config_summary": dict(record.config_summary),
        "config_fingerprint": record.config_fingerprint,
        "last_run_id": record.last_run_id,
        "last_workflow_run_id": record.last_workflow_run_id,
        "last_commit_ref": record.last_commit_ref,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _patch_values(patch: SourceConnectionUpdate) -> dict[str, object]:
    values: dict[str, object] = {}
    for field_name, value in patch.__dict__.items():
        if value is not None:
            values[_snake(field_name)] = value
    return values


def _snake(value: str) -> str:
    result = ""
    for char in value:
        result += f"_{char.lower()}" if char.isupper() else char
    return result
