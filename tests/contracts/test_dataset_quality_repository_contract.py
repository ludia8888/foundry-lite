from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from foundry_lite.application.ports.dataset_quality_repository import (
    DatasetCheckRecord,
    DatasetCheckResultRecord,
    DatasetCheckResultRow,
    DatasetCheckRow,
    DatasetQualityRepository,
    DatasetSchemaRecord,
    DatasetSchemaRow,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyDatasetQualityRepository
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine


class QualityHarness(Protocol):
    @property
    def repository(self) -> DatasetQualityRepository: ...

    def transaction(self) -> AbstractContextManager[Any]: ...

    def schema_rows(self) -> list[dict[str, Any]]: ...

    def check_rows(self) -> list[dict[str, Any]]: ...

    def check_result_rows(self) -> list[dict[str, Any]]: ...


@dataclass
class FakeDatasetQualityRepository:
    schemas: list[dict[str, Any]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    check_results: list[dict[str, Any]] = field(default_factory=list)

    def schema_by_hash(
        self,
        *,
        transaction: Any,
        dataset_id: str,
        schema_hash: str,
    ) -> DatasetSchemaRow | None:
        del transaction
        for row in self.schemas:
            if row["dataset_id"] == dataset_id and row["schema_hash"] == schema_hash:
                return cast(DatasetSchemaRow, dict(row))
        return None

    def latest_schema_version(self, *, transaction: Any, dataset_id: str) -> int | None:
        del transaction
        versions = [row["version"] for row in self.schemas if row["dataset_id"] == dataset_id]
        return max(versions) if versions else None

    def insert_schema(self, *, transaction: Any, record: DatasetSchemaRecord) -> None:
        del transaction
        self.schemas.append(
            {
                "id": record.schema_id,
                "dataset_id": record.dataset_id,
                "version": record.version,
                "schema_json": dict(record.schema_json),
                "schema_hash": record.schema_hash,
                "created_at": record.created_at,
            }
        )

    def check_by_name(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        dataset_id: str,
        name: str,
    ) -> DatasetCheckRow | None:
        del transaction
        for row in self.checks:
            if row["tenant_id"] == tenant_id and row["dataset_id"] == dataset_id and row["name"] == name:
                return cast(DatasetCheckRow, dict(row))
        return None

    def insert_check(self, *, transaction: Any, record: DatasetCheckRecord) -> None:
        del transaction
        self.checks.append(
            {
                "id": record.check_id,
                "tenant_id": record.tenant_id,
                "dataset_id": record.dataset_id,
                "name": record.name,
                "check_type": record.check_type,
                "config": dict(record.config),
                "severity": record.severity,
                "enabled": record.enabled,
            }
        )

    def insert_check_result(self, *, transaction: Any, record: DatasetCheckResultRecord) -> None:
        del transaction
        self.check_results.append(
            {
                "id": record.check_result_id,
                "tenant_id": record.tenant_id,
                "check_id": record.check_id,
                "run_id": record.run_id,
                "transaction_id": record.transaction_id,
                "checked_manifest_hash": record.checked_manifest_hash,
                "validated_against_schema_version_id": record.validated_against_schema_version_id,
                "validated_against_schema_version": record.validated_against_schema_version,
                "status": record.status,
                "details": dict(record.details),
                "created_at": record.created_at,
            }
        )

    def check_results_for_transaction(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        transaction_id: str,
    ) -> list[DatasetCheckResultRow]:
        del transaction
        return [
            cast(DatasetCheckResultRow, dict(row))
            for row in sorted(self.check_results, key=lambda item: (item["created_at"], item["id"]))
            if row["tenant_id"] == tenant_id and row["transaction_id"] == transaction_id
        ]


@dataclass
class FakeDatasetQualityHarness:
    repository: FakeDatasetQualityRepository = field(default_factory=FakeDatasetQualityRepository)

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        yield self

    def schema_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.repository.schemas]

    def check_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.repository.checks]

    def check_result_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.repository.check_results]


@dataclass
class SqlAlchemyDatasetQualityHarness:
    engine: Engine
    repository: SqlAlchemyDatasetQualityRepository

    @classmethod
    def create(cls, tmp_path: Path) -> SqlAlchemyDatasetQualityHarness:
        engine = create_engine(f"sqlite:///{tmp_path / 'quality.db'}", future=True)
        db.create_database(engine)
        with engine.begin() as conn:
            conn.execute(
                db.tenants.insert().values(
                    id="tenant-test",
                    name="Test",
                    created_at="2026-06-10T00:00:00Z",
                )
            )
        return cls(engine=engine, repository=SqlAlchemyDatasetQualityRepository(engine))

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.engine.begin() as conn:
            yield conn

    def schema_rows(self) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            return [dict(row) for row in conn.execute(select(db.dataset_schemas)).mappings().all()]

    def check_rows(self) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            return [dict(row) for row in conn.execute(select(db.dataset_checks)).mappings().all()]

    def check_result_rows(self) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            return [dict(row) for row in conn.execute(select(db.dataset_check_results)).mappings().all()]


@pytest.fixture(params=["fake", "sqlalchemy", "postgres"])
def harness(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> QualityHarness:
    if request.param == "fake":
        return FakeDatasetQualityHarness()
    if request.param == "sqlalchemy":
        return SqlAlchemyDatasetQualityHarness.create(tmp_path)
    postgres_fixture = request.getfixturevalue("postgres_fixture")
    return SqlAlchemyDatasetQualityHarness(
        engine=postgres_fixture.engine,
        repository=SqlAlchemyDatasetQualityRepository(postgres_fixture.engine),
    )


def _schema_record(version: int = 1, schema_hash: str = "hash-a") -> DatasetSchemaRecord:
    return DatasetSchemaRecord(
        schema_id=f"schema_v{version}",
        dataset_id="ds_test",
        version=version,
        schema_json={"columns": [{"name": "id", "type": "string"}]},
        schema_hash=schema_hash,
        created_at="2026-06-10T00:00:00Z",
    )


def _check_record(check_id: str = "check_a", name: str = '{"type":"unique"}') -> DatasetCheckRecord:
    return DatasetCheckRecord(
        check_id=check_id,
        tenant_id="tenant-test",
        dataset_id="ds_test",
        name=name,
        check_type="unique",
        config={"type": "unique", "column": "id"},
        severity="error",
        enabled=True,
    )


def _check_result_record(check_id: str = "check_a") -> DatasetCheckResultRecord:
    return DatasetCheckResultRecord(
        check_result_id="cr_a",
        tenant_id="tenant-test",
        check_id=check_id,
        run_id="run_test",
        transaction_id="dstx_test",
        checked_manifest_hash="candidate_hash_v1",
        validated_against_schema_version_id="schema_v1",
        validated_against_schema_version=1,
        status="PASS",
        details={"status": "passed", "contract_status": "PASS"},
        created_at="2026-06-10T00:00:00Z",
    )


def test_schema_by_hash_returns_none_when_absent(harness: QualityHarness) -> None:
    with harness.transaction() as txn:
        assert harness.repository.schema_by_hash(transaction=txn, dataset_id="ds_test", schema_hash="missing") is None


def test_insert_schema_round_trips_via_hash(harness: QualityHarness) -> None:
    record = _schema_record()
    with harness.transaction() as txn:
        harness.repository.insert_schema(transaction=txn, record=record)
        found = harness.repository.schema_by_hash(
            transaction=txn, dataset_id=record.dataset_id, schema_hash=record.schema_hash
        )
    assert found is not None
    assert found["version"] == 1
    assert found["schema_json"] == {"columns": [{"name": "id", "type": "string"}]}


def test_latest_schema_version_handles_empty(harness: QualityHarness) -> None:
    with harness.transaction() as txn:
        assert harness.repository.latest_schema_version(transaction=txn, dataset_id="ds_test") is None


def test_latest_schema_version_returns_highest(harness: QualityHarness) -> None:
    with harness.transaction() as txn:
        harness.repository.insert_schema(transaction=txn, record=_schema_record(version=1, schema_hash="h1"))
        harness.repository.insert_schema(transaction=txn, record=_schema_record(version=3, schema_hash="h3"))
        harness.repository.insert_schema(transaction=txn, record=_schema_record(version=2, schema_hash="h2"))
        latest = harness.repository.latest_schema_version(transaction=txn, dataset_id="ds_test")
    assert latest == 3


def test_check_by_name_returns_none_when_absent(harness: QualityHarness) -> None:
    with harness.transaction() as txn:
        assert (
            harness.repository.check_by_name(
                transaction=txn,
                tenant_id="tenant-test",
                dataset_id="ds_test",
                name="missing",
            )
            is None
        )


def test_insert_check_round_trips_via_name(harness: QualityHarness) -> None:
    record = _check_record()
    with harness.transaction() as txn:
        harness.repository.insert_check(transaction=txn, record=record)
        found = harness.repository.check_by_name(
            transaction=txn,
            tenant_id=record.tenant_id,
            dataset_id=record.dataset_id,
            name=record.name,
        )
    assert found is not None
    assert found["check_type"] == "unique"
    assert found["enabled"] is True
    assert found["config"] == {"type": "unique", "column": "id"}


def test_check_by_name_isolated_by_tenant(harness: QualityHarness) -> None:
    if isinstance(harness, SqlAlchemyDatasetQualityHarness):
        with harness.engine.begin() as conn:
            conn.execute(
                db.tenants.insert().values(
                    id="tenant-other",
                    name="Other",
                    created_at="2026-06-10T00:00:00Z",
                )
            )
    record_a = _check_record(check_id="check_self", name="shared")
    record_b = _check_record(check_id="check_other", name="shared")
    record_b_other = DatasetCheckRecord(
        check_id=record_b.check_id,
        tenant_id="tenant-other",
        dataset_id=record_b.dataset_id,
        name=record_b.name,
        check_type=record_b.check_type,
        config=record_b.config,
        severity=record_b.severity,
        enabled=record_b.enabled,
    )
    with harness.transaction() as txn:
        harness.repository.insert_check(transaction=txn, record=record_a)
        harness.repository.insert_check(transaction=txn, record=record_b_other)
        found_self = harness.repository.check_by_name(
            transaction=txn,
            tenant_id="tenant-test",
            dataset_id="ds_test",
            name="shared",
        )
        found_other = harness.repository.check_by_name(
            transaction=txn,
            tenant_id="tenant-other",
            dataset_id="ds_test",
            name="shared",
        )
    assert found_self is not None and found_self["id"] == "check_self"
    assert found_other is not None and found_other["id"] == "check_other"


def test_insert_check_result_persists(harness: QualityHarness) -> None:
    with harness.transaction() as txn:
        harness.repository.insert_check(transaction=txn, record=_check_record())
        harness.repository.insert_check_result(transaction=txn, record=_check_result_record())
        found = harness.repository.check_results_for_transaction(
            transaction=txn,
            tenant_id="tenant-test",
            transaction_id="dstx_test",
        )
    rows = harness.check_result_rows()
    assert len(rows) == 1
    assert rows[0]["status"] == "PASS"
    assert rows[0]["check_id"] == "check_a"
    assert rows[0]["checked_manifest_hash"] == "candidate_hash_v1"
    assert rows[0]["validated_against_schema_version_id"] == "schema_v1"
    assert rows[0]["validated_against_schema_version"] == 1
    assert rows[0]["details"] == {"status": "passed", "contract_status": "PASS"}
    assert len(found) == 1
    assert found[0]["id"] == "cr_a"


def test_check_results_for_transaction_is_tenant_scoped(harness: QualityHarness) -> None:
    if isinstance(harness, SqlAlchemyDatasetQualityHarness):
        with harness.engine.begin() as conn:
            conn.execute(
                db.tenants.insert().values(
                    id="tenant-other",
                    name="Other",
                    created_at="2026-06-10T00:00:00Z",
                )
            )
    other_result = DatasetCheckResultRecord(
        check_result_id="cr_other",
        tenant_id="tenant-other",
        check_id="check_other",
        run_id="run_other",
        transaction_id="dstx_test",
        checked_manifest_hash="candidate_hash_other",
        validated_against_schema_version_id="schema_v1",
        validated_against_schema_version=1,
        status="WARN",
        details={"status": "failed", "contract_status": "WARN"},
        created_at="2026-06-10T00:00:01Z",
    )
    with harness.transaction() as txn:
        harness.repository.insert_check(transaction=txn, record=_check_record())
        harness.repository.insert_check_result(transaction=txn, record=_check_result_record())
        harness.repository.insert_check_result(transaction=txn, record=other_result)
        found = harness.repository.check_results_for_transaction(
            transaction=txn,
            tenant_id="tenant-test",
            transaction_id="dstx_test",
        )
    assert [row["id"] for row in found] == ["cr_a"]
