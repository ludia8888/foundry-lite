from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import pytest
from foundry_lite.application.ports import DatasetVersionRepository
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyDatasetVersionRepository
from sqlalchemy import create_engine, insert
from sqlalchemy.engine import Engine


class DatasetVersionRepositoryHarness(Protocol):
    repository: DatasetVersionRepository

    def add_schema(self, *, dataset_id: str, version: int, schema_hash: str) -> None: ...

    def add_version(self, *, dataset_id: str, version_id: str, version_number: int) -> None: ...

    def next_version_number(self, *, dataset_id: str) -> int: ...

    def latest_version_by_dataset_id(self, *, dataset_id: str) -> dict[str, Any] | None: ...

    def version_by_id(self, *, version_id: str) -> dict[str, Any] | None: ...

    def version_by_dataset_id_and_id(self, *, dataset_id: str, version_id: str) -> dict[str, Any] | None: ...


@dataclass
class FakeDatasetVersionRepository:
    schemas: list[dict[str, Any]] = field(default_factory=list)
    versions: list[dict[str, Any]] = field(default_factory=list)

    def next_version_number(self, *, transaction: Any, dataset_id: str) -> int:
        del transaction
        numbers = [version["version_number"] for version in self.versions if version["dataset_id"] == dataset_id]
        return max(numbers, default=0) + 1

    def schema_for_version(self, *, dataset_id: str, schema_version: int) -> dict[str, Any] | None:
        for schema in self.schemas:
            if schema["dataset_id"] == dataset_id and schema["version"] == schema_version:
                return dict(schema)
        return None

    def latest_version_by_dataset_id(self, *, transaction: Any, dataset_id: str) -> dict[str, Any] | None:
        del transaction
        versions = [version for version in self.versions if version["dataset_id"] == dataset_id]
        if not versions:
            return None
        return dict(max(versions, key=lambda version: version["version_number"]))

    def version_by_id(self, *, transaction: Any, version_id: str) -> dict[str, Any] | None:
        del transaction
        for version in self.versions:
            if version["id"] == version_id:
                return dict(version)
        return None

    def version_by_dataset_id_and_id(
        self, *, transaction: Any, dataset_id: str, version_id: str
    ) -> dict[str, Any] | None:
        del transaction
        for version in self.versions:
            if version["dataset_id"] == dataset_id and version["id"] == version_id:
                return dict(version)
        return None

    def list_versions(self, *, dataset_id: str) -> list[dict[str, Any]]:
        return sorted(
            [dict(version) for version in self.versions if version["dataset_id"] == dataset_id],
            key=lambda version: version["version_number"],
        )


@dataclass
class FakeDatasetVersionRepositoryHarness:
    repository: FakeDatasetVersionRepository

    def add_schema(self, *, dataset_id: str, version: int, schema_hash: str) -> None:
        self.repository.schemas.append(_schema_row(dataset_id=dataset_id, version=version, schema_hash=schema_hash))

    def add_version(self, *, dataset_id: str, version_id: str, version_number: int) -> None:
        self.repository.versions.append(
            _version_row(dataset_id=dataset_id, version_id=version_id, version_number=version_number)
        )

    def next_version_number(self, *, dataset_id: str) -> int:
        return self.repository.next_version_number(transaction=None, dataset_id=dataset_id)

    def latest_version_by_dataset_id(self, *, dataset_id: str) -> dict[str, Any] | None:
        return self.repository.latest_version_by_dataset_id(transaction=None, dataset_id=dataset_id)

    def version_by_id(self, *, version_id: str) -> dict[str, Any] | None:
        return self.repository.version_by_id(transaction=None, version_id=version_id)

    def version_by_dataset_id_and_id(self, *, dataset_id: str, version_id: str) -> dict[str, Any] | None:
        return self.repository.version_by_dataset_id_and_id(
            transaction=None,
            dataset_id=dataset_id,
            version_id=version_id,
        )


@dataclass
class SqlAlchemyDatasetVersionRepositoryHarness:
    repository: SqlAlchemyDatasetVersionRepository
    engine: Engine

    def add_schema(self, *, dataset_id: str, version: int, schema_hash: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(db.dataset_schemas).values(
                    **_schema_row(dataset_id=dataset_id, version=version, schema_hash=schema_hash)
                )
            )

    def add_version(self, *, dataset_id: str, version_id: str, version_number: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(db.dataset_versions).values(
                    **_version_row(dataset_id=dataset_id, version_id=version_id, version_number=version_number)
                )
            )

    def next_version_number(self, *, dataset_id: str) -> int:
        with self.engine.begin() as conn:
            return self.repository.next_version_number(transaction=conn, dataset_id=dataset_id)

    def latest_version_by_dataset_id(self, *, dataset_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            return self.repository.latest_version_by_dataset_id(transaction=conn, dataset_id=dataset_id)

    def version_by_id(self, *, version_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            return self.repository.version_by_id(transaction=conn, version_id=version_id)

    def version_by_dataset_id_and_id(self, *, dataset_id: str, version_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            return self.repository.version_by_dataset_id_and_id(
                transaction=conn,
                dataset_id=dataset_id,
                version_id=version_id,
            )


def _schema_row(*, dataset_id: str, version: int, schema_hash: str) -> dict[str, Any]:
    return {
        "id": f"dss_{dataset_id}_{version}",
        "dataset_id": dataset_id,
        "version": version,
        "schema_json": [{"name": "order_id", "type": "INTEGER"}],
        "schema_hash": schema_hash,
        "created_at": "2026-06-10T00:00:00Z",
    }


def _version_row(*, dataset_id: str, version_id: str, version_number: int) -> dict[str, Any]:
    return {
        "id": version_id,
        "tenant_id": "tenant-demo",
        "dataset_id": dataset_id,
        "branch": "main",
        "version_number": version_number,
        "transaction_id": f"dstx_{version_number}",
        "schema_version": 1,
        "manifest_uri": f"memory://{version_id}/manifest.json",
        "row_count": 1,
        "byte_size": 10,
        "status": "active",
        "superseded_by_version_id": None,
        "created_at": "2026-06-10T00:00:00Z",
    }


@pytest.fixture(params=["sqlalchemy", "fake", "postgres"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> DatasetVersionRepositoryHarness:
    if request.param == "fake":
        return FakeDatasetVersionRepositoryHarness(FakeDatasetVersionRepository())
    if request.param == "sqlalchemy":
        engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}", future=True)
        db.create_database(engine)
        return SqlAlchemyDatasetVersionRepositoryHarness(SqlAlchemyDatasetVersionRepository(engine), engine)
    postgres_fixture = request.getfixturevalue("postgres_fixture")
    return SqlAlchemyDatasetVersionRepositoryHarness(
        SqlAlchemyDatasetVersionRepository(postgres_fixture.engine),
        postgres_fixture.engine,
    )


def test_dataset_version_repository_contract_lists_versions_and_calculates_next(
    harness: DatasetVersionRepositoryHarness,
) -> None:
    harness.add_version(dataset_id="ds_orders", version_id="dsv_2", version_number=2)
    harness.add_version(dataset_id="ds_orders", version_id="dsv_1", version_number=1)

    versions = harness.repository.list_versions(dataset_id="ds_orders")

    assert [version["id"] for version in versions] == ["dsv_1", "dsv_2"]
    assert harness.next_version_number(dataset_id="ds_orders") == 3
    assert harness.next_version_number(dataset_id="ds_empty") == 1


def test_dataset_version_repository_contract_reads_latest_and_version_by_id(
    harness: DatasetVersionRepositoryHarness,
) -> None:
    harness.add_version(dataset_id="ds_orders", version_id="dsv_1", version_number=1)
    harness.add_version(dataset_id="ds_orders", version_id="dsv_2", version_number=2)

    latest = harness.latest_version_by_dataset_id(dataset_id="ds_orders")
    selected = harness.version_by_id(version_id="dsv_1")

    assert latest is not None
    assert latest["id"] == "dsv_2"
    assert selected is not None
    assert selected["version_number"] == 1
    assert harness.latest_version_by_dataset_id(dataset_id="ds_empty") is None
    assert harness.version_by_id(version_id="dsv_missing") is None


def test_dataset_version_repository_contract_scopes_version_id_to_dataset(
    harness: DatasetVersionRepositoryHarness,
) -> None:
    harness.add_version(dataset_id="ds_orders", version_id="dsv_shared", version_number=1)

    selected = harness.version_by_dataset_id_and_id(dataset_id="ds_orders", version_id="dsv_shared")

    assert selected is not None
    assert selected["id"] == "dsv_shared"
    assert harness.version_by_dataset_id_and_id(dataset_id="ds_customers", version_id="dsv_shared") is None


def test_dataset_version_repository_contract_reads_schema_by_dataset_version(
    harness: DatasetVersionRepositoryHarness,
) -> None:
    harness.add_schema(dataset_id="ds_orders", version=1, schema_hash="hash_1")
    harness.add_schema(dataset_id="ds_orders", version=2, schema_hash="hash_2")

    schema = harness.repository.schema_for_version(dataset_id="ds_orders", schema_version=2)

    assert schema is not None
    assert schema["schema_hash"] == "hash_2"
    assert harness.repository.schema_for_version(dataset_id="ds_orders", schema_version=99) is None
