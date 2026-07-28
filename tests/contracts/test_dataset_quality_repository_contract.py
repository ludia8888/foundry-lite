from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from foundry_lite.application.ports.dataset_quality_repository import (
    DatasetCheckRecord,
    DatasetCheckResultHistoryRow,
    DatasetCheckResultRecord,
    DatasetCheckResultRow,
    DatasetCheckResultStatusCountRow,
    DatasetCheckResultTypeStatusCountRow,
    DatasetCheckRow,
    DatasetQualityContractVersionRecord,
    DatasetQualityContractVersionRow,
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

    def contract_version_rows(self) -> list[dict[str, Any]]: ...


@dataclass
class FakeDatasetQualityRepository:
    schemas: list[dict[str, Any]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    check_results: list[dict[str, Any]] = field(default_factory=list)
    contract_versions: list[dict[str, Any]] = field(default_factory=list)

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

    def latest_schema(self, *, transaction: Any, dataset_id: str) -> DatasetSchemaRow | None:
        del transaction
        rows = [row for row in self.schemas if row["dataset_id"] == dataset_id]
        if not rows:
            return None
        return cast(DatasetSchemaRow, dict(sorted(rows, key=lambda item: item["version"], reverse=True)[0]))

    def schema_by_version(
        self,
        *,
        transaction: Any,
        dataset_id: str,
        schema_version: int,
    ) -> DatasetSchemaRow | None:
        del transaction
        for row in self.schemas:
            if row["dataset_id"] == dataset_id and row["version"] == schema_version:
                return cast(DatasetSchemaRow, dict(row))
        return None

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
        if self.check_by_name(
            transaction=None,
            tenant_id=record.tenant_id,
            dataset_id=record.dataset_id,
            name=record.name,
        ):
            return
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

    def update_check(self, *, transaction: Any, record: DatasetCheckRecord) -> bool:
        del transaction
        for row in self.checks:
            if (
                row["tenant_id"] == record.tenant_id
                and row["dataset_id"] == record.dataset_id
                and row["id"] == record.check_id
            ):
                row.update(
                    name=record.name,
                    check_type=record.check_type,
                    config=dict(record.config),
                    severity=record.severity,
                    enabled=record.enabled,
                )
                return True
        return False

    def check_by_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        dataset_id: str,
        check_id: str,
    ) -> DatasetCheckRow | None:
        del transaction
        for row in self.checks:
            if row["tenant_id"] == tenant_id and row["dataset_id"] == dataset_id and row["id"] == check_id:
                return cast(DatasetCheckRow, dict(row))
        return None

    def checks_for_dataset(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        dataset_id: str,
    ) -> list[DatasetCheckRow]:
        del transaction
        return [
            cast(DatasetCheckRow, dict(row))
            for row in sorted(self.checks, key=lambda item: (item["name"], item["id"]))
            if row["tenant_id"] == tenant_id and row["dataset_id"] == dataset_id
        ]

    def insert_check_result(self, *, transaction: Any, record: DatasetCheckResultRecord) -> None:
        del transaction
        self.check_results.append(
            {
                "id": record.check_result_id,
                "tenant_id": record.tenant_id,
                "check_id": record.check_id,
                "data_contract_version_id": record.data_contract_version_id,
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

    def latest_contract_version_number(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        dataset_id: str,
        contract_key: str,
    ) -> int | None:
        del transaction
        versions = [
            row["version"] for row in self.contract_versions if _same_contract(row, tenant_id, dataset_id, contract_key)
        ]
        return max(versions) if versions else None

    def insert_contract_version(
        self,
        *,
        transaction: Any,
        record: DatasetQualityContractVersionRecord,
    ) -> None:
        del transaction
        self.contract_versions.append(
            {
                "id": record.contract_version_id,
                "tenant_id": record.tenant_id,
                "dataset_id": record.dataset_id,
                "contract_key": record.contract_key,
                "version": record.version,
                "status": record.status,
                "owner_user_id": record.owner_user_id,
                "description": record.description,
                "checks_snapshot": [dict(item) for item in record.checks_snapshot],
                "schema_version_id": record.schema_version_id,
                "schema_version": record.schema_version,
                "created_at": record.created_at,
                "activated_at": record.activated_at,
            }
        )

    def contract_version_by_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        dataset_id: str,
        contract_version_id: str,
    ) -> DatasetQualityContractVersionRow | None:
        del transaction
        for row in self.contract_versions:
            if row["tenant_id"] == tenant_id and row["dataset_id"] == dataset_id and row["id"] == contract_version_id:
                return cast(DatasetQualityContractVersionRow, dict(row))
        return None

    def contract_versions_for_dataset(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        dataset_id: str,
        limit: int,
    ) -> list[DatasetQualityContractVersionRow]:
        del transaction
        rows = [
            row for row in self.contract_versions if row["tenant_id"] == tenant_id and row["dataset_id"] == dataset_id
        ]
        ordered = sorted(rows, key=lambda item: (item["contract_key"], -int(item["version"])))
        return [cast(DatasetQualityContractVersionRow, dict(row)) for row in ordered[:limit]]

    def active_contract_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        dataset_id: str,
        contract_key: str,
    ) -> DatasetQualityContractVersionRow | None:
        del transaction
        rows = [
            row
            for row in self.contract_versions
            if _same_contract(row, tenant_id, dataset_id, contract_key) and row["status"] == "ACTIVE"
        ]
        if not rows:
            return None
        return cast(DatasetQualityContractVersionRow, dict(sorted(rows, key=lambda item: item["version"])[-1]))

    def activate_contract_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        dataset_id: str,
        contract_key: str,
        contract_version_id: str,
        activated_at: str,
    ) -> bool:
        del transaction
        matched = False
        for row in self.contract_versions:
            if _same_contract(row, tenant_id, dataset_id, contract_key) and row["status"] == "ACTIVE":
                row["status"] = "SUPERSEDED"
            if _same_contract(row, tenant_id, dataset_id, contract_key) and row["id"] == contract_version_id:
                row["status"] = "ACTIVE"
                row["activated_at"] = activated_at
                matched = True
        return matched

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

    def check_results_for_dataset(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        dataset_id: str,
        limit: int,
    ) -> list[DatasetCheckResultHistoryRow]:
        del transaction
        check_rows = {
            row["id"]: row for row in self.checks if row["tenant_id"] == tenant_id and row["dataset_id"] == dataset_id
        }
        rows = [
            _history_row(row, check_rows[row["check_id"]])
            for row in self.check_results
            if row["tenant_id"] == tenant_id and row["check_id"] in check_rows
        ]
        return sorted(rows, key=lambda item: (item["created_at"], item["id"]), reverse=True)[:limit]

    def check_result_status_counts_for_dataset(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        dataset_id: str,
    ) -> list[DatasetCheckResultStatusCountRow]:
        del transaction
        rows = self.check_results_for_dataset(
            transaction=None,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            limit=len(self.check_results),
        )
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        return [cast(DatasetCheckResultStatusCountRow, {"status": key, "count": counts[key]}) for key in sorted(counts)]

    def check_result_type_status_counts_for_dataset(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        dataset_id: str,
    ) -> list[DatasetCheckResultTypeStatusCountRow]:
        del transaction
        rows = self.check_results_for_dataset(
            transaction=None,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            limit=len(self.check_results),
        )
        counts: dict[tuple[str, str], int] = {}
        for row in rows:
            key = (row["check_type"], row["status"])
            counts[key] = counts.get(key, 0) + 1
        return [
            cast(DatasetCheckResultTypeStatusCountRow, {"check_type": key[0], "status": key[1], "count": counts[key]})
            for key in sorted(counts)
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

    def contract_version_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.repository.contract_versions]


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

    def contract_version_rows(self) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            return [dict(row) for row in conn.execute(select(db.dataset_quality_contract_versions)).mappings().all()]


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


def _same_contract(row: dict[str, Any], tenant_id: str, dataset_id: str, contract_key: str) -> bool:
    return row["tenant_id"] == tenant_id and row["dataset_id"] == dataset_id and row["contract_key"] == contract_key


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


def _check_result_record(
    check_id: str = "check_a",
    *,
    check_result_id: str = "cr_a",
    tenant_id: str = "tenant-test",
    transaction_id: str = "dstx_test",
    created_at: str = "2026-06-10T00:00:00Z",
    status: str = "PASS",
    details: dict[str, object] | None = None,
) -> DatasetCheckResultRecord:
    result_details = details or {"status": "passed", "contract_status": status}
    return DatasetCheckResultRecord(
        check_result_id=check_result_id,
        tenant_id=tenant_id,
        check_id=check_id,
        run_id=f"run_{check_result_id}",
        transaction_id=transaction_id,
        checked_manifest_hash="candidate_hash_v1",
        validated_against_schema_version_id="schema_v1",
        validated_against_schema_version=1,
        status=status,
        details=result_details,
        created_at=created_at,
    )


def _contract_version_record(
    contract_version_id: str = "dqcv_a",
    *,
    version: int = 1,
    status: str = "DRAFT",
    tenant_id: str = "tenant-test",
    dataset_id: str = "ds_test",
    contract_key: str = "default",
) -> DatasetQualityContractVersionRecord:
    return DatasetQualityContractVersionRecord(
        contract_version_id=contract_version_id,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        contract_key=contract_key,
        version=version,
        status=status,
        owner_user_id="owner-a",
        description="orders contract",
        checks_snapshot=[
            {
                "checkId": "check_a",
                "name": "unique-id",
                "checkType": "unique",
                "config": {"type": "unique", "column": "id"},
                "severity": "error",
                "enabled": True,
            }
        ],
        schema_version_id="schema_v1",
        schema_version=1,
        created_at="2026-06-10T00:00:00Z",
    )


def _history_row(row: dict[str, Any], check: dict[str, Any]) -> DatasetCheckResultHistoryRow:
    return cast(
        DatasetCheckResultHistoryRow,
        {
            **dict(row),
            "dataset_id": check["dataset_id"],
            "check_name": check["name"],
            "check_type": check["check_type"],
            "severity": check["severity"],
        },
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


def test_latest_schema_returns_highest_row(harness: QualityHarness) -> None:
    with harness.transaction() as txn:
        harness.repository.insert_schema(transaction=txn, record=_schema_record(version=1, schema_hash="h1"))
        harness.repository.insert_schema(transaction=txn, record=_schema_record(version=3, schema_hash="h3"))
        latest = harness.repository.latest_schema(transaction=txn, dataset_id="ds_test")
    assert latest is not None
    assert latest["id"] == "schema_v3"
    assert latest["version"] == 3


def test_schema_by_version_returns_only_the_exact_dataset_schema(harness: QualityHarness) -> None:
    with harness.transaction() as txn:
        harness.repository.insert_schema(transaction=txn, record=_schema_record(version=1, schema_hash="h1"))
        harness.repository.insert_schema(transaction=txn, record=_schema_record(version=2, schema_hash="h2"))
        exact = harness.repository.schema_by_version(
            transaction=txn,
            dataset_id="ds_test",
            schema_version=1,
        )
        missing = harness.repository.schema_by_version(
            transaction=txn,
            dataset_id="other-dataset",
            schema_version=1,
        )
    assert exact is not None
    assert exact["id"] == "schema_v1"
    assert exact["schema_hash"] == "h1"
    assert missing is None


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


def test_insert_check_is_idempotent_by_tenant_dataset_and_name(harness: QualityHarness) -> None:
    first = _check_record(check_id="check_first", name="shared-check")
    duplicate = _check_record(check_id="check_duplicate", name="shared-check")

    with harness.transaction() as txn:
        harness.repository.insert_check(transaction=txn, record=first)
        harness.repository.insert_check(transaction=txn, record=duplicate)
        found = harness.repository.check_by_name(
            transaction=txn,
            tenant_id=first.tenant_id,
            dataset_id=first.dataset_id,
            name=first.name,
        )

    matching_rows = [
        row
        for row in harness.check_rows()
        if row["tenant_id"] == first.tenant_id and row["dataset_id"] == first.dataset_id and row["name"] == first.name
    ]
    assert len(matching_rows) == 1
    assert found is not None
    assert found["id"] == "check_first"


def test_checks_for_dataset_are_tenant_scoped_and_ordered(harness: QualityHarness) -> None:
    if isinstance(harness, SqlAlchemyDatasetQualityHarness):
        with harness.engine.begin() as conn:
            conn.execute(
                db.tenants.insert().values(
                    id="tenant-other",
                    name="Other",
                    created_at="2026-06-10T00:00:00Z",
                )
            )
    first = _check_record(check_id="check_b", name="b-check")
    second = _check_record(check_id="check_a", name="a-check")
    other_dataset = _check_record(check_id="check_other_dataset", name="c-check")
    other_dataset = DatasetCheckRecord(
        check_id=other_dataset.check_id,
        tenant_id=other_dataset.tenant_id,
        dataset_id="ds_other",
        name=other_dataset.name,
        check_type=other_dataset.check_type,
        config=other_dataset.config,
        severity=other_dataset.severity,
        enabled=other_dataset.enabled,
    )
    other_tenant = DatasetCheckRecord(
        check_id="check_other_tenant",
        tenant_id="tenant-other",
        dataset_id="ds_test",
        name="a-check",
        check_type="unique",
        config={"type": "unique", "column": "id"},
        severity="error",
        enabled=True,
    )
    with harness.transaction() as txn:
        harness.repository.insert_check(transaction=txn, record=first)
        harness.repository.insert_check(transaction=txn, record=second)
        harness.repository.insert_check(transaction=txn, record=other_dataset)
        harness.repository.insert_check(transaction=txn, record=other_tenant)
        rows = harness.repository.checks_for_dataset(
            transaction=txn,
            tenant_id="tenant-test",
            dataset_id="ds_test",
        )
        found = harness.repository.check_by_id(
            transaction=txn,
            tenant_id="tenant-test",
            dataset_id="ds_test",
            check_id="check_a",
        )

    assert [row["id"] for row in rows] == ["check_a", "check_b"]
    assert found is not None
    assert found["name"] == "a-check"


def test_update_check_is_tenant_scoped_and_rewrites_definition(harness: QualityHarness) -> None:
    record = _check_record()
    updated = DatasetCheckRecord(
        check_id=record.check_id,
        tenant_id=record.tenant_id,
        dataset_id=record.dataset_id,
        name='{"min":5,"type":"row_count_min"}',
        check_type="row_count_min",
        config={"type": "row_count_min", "min": 5, "severity": "warn"},
        severity="warn",
        enabled=False,
    )
    wrong_tenant = DatasetCheckRecord(
        check_id=record.check_id,
        tenant_id="tenant-other",
        dataset_id=record.dataset_id,
        name=updated.name,
        check_type=updated.check_type,
        config=updated.config,
        severity=updated.severity,
        enabled=updated.enabled,
    )

    with harness.transaction() as txn:
        harness.repository.insert_check(transaction=txn, record=record)
        assert not harness.repository.update_check(transaction=txn, record=wrong_tenant)
        assert harness.repository.update_check(transaction=txn, record=updated)
        found = harness.repository.check_by_id(
            transaction=txn,
            tenant_id=record.tenant_id,
            dataset_id=record.dataset_id,
            check_id=record.check_id,
        )

    assert found is not None
    assert found["name"] == updated.name
    assert found["check_type"] == "row_count_min"
    assert found["config"] == {"type": "row_count_min", "min": 5, "severity": "warn"}
    assert found["severity"] == "warn"
    assert found["enabled"] is False


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
    assert rows[0]["data_contract_version_id"] is None
    assert rows[0]["checked_manifest_hash"] == "candidate_hash_v1"
    assert rows[0]["validated_against_schema_version_id"] == "schema_v1"
    assert rows[0]["validated_against_schema_version"] == 1
    assert rows[0]["details"] == {"status": "passed", "contract_status": "PASS"}
    assert len(found) == 1
    assert found[0]["id"] == "cr_a"


def test_contract_versions_round_trip_and_activate_one_winner(harness: QualityHarness) -> None:
    first = _contract_version_record("dqcv_first", version=1)
    second = _contract_version_record("dqcv_second", version=2)

    with harness.transaction() as txn:
        harness.repository.insert_contract_version(transaction=txn, record=first)
        harness.repository.insert_contract_version(transaction=txn, record=second)
        latest_version = harness.repository.latest_contract_version_number(
            transaction=txn,
            tenant_id="tenant-test",
            dataset_id="ds_test",
            contract_key="default",
        )
        first_activated = harness.repository.activate_contract_version(
            transaction=txn,
            tenant_id="tenant-test",
            dataset_id="ds_test",
            contract_key="default",
            contract_version_id="dqcv_first",
            activated_at="2026-06-10T01:00:00Z",
        )
        second_activated = harness.repository.activate_contract_version(
            transaction=txn,
            tenant_id="tenant-test",
            dataset_id="ds_test",
            contract_key="default",
            contract_version_id="dqcv_second",
            activated_at="2026-06-10T02:00:00Z",
        )
        active = harness.repository.active_contract_version(
            transaction=txn,
            tenant_id="tenant-test",
            dataset_id="ds_test",
            contract_key="default",
        )
        listed = harness.repository.contract_versions_for_dataset(
            transaction=txn,
            tenant_id="tenant-test",
            dataset_id="ds_test",
            limit=10,
        )

    rows = {row["id"]: row for row in harness.contract_version_rows()}
    assert latest_version == 2
    assert first_activated and second_activated
    assert active is not None and active["id"] == "dqcv_second"
    assert [row["id"] for row in listed] == ["dqcv_second", "dqcv_first"]
    assert rows["dqcv_first"]["status"] == "SUPERSEDED"
    assert rows["dqcv_second"]["status"] == "ACTIVE"
    assert rows["dqcv_second"]["checks_snapshot"][0]["config"] == {"type": "unique", "column": "id"}


def test_check_result_can_reference_contract_version(harness: QualityHarness) -> None:
    record = _check_result_record()
    with harness.transaction() as txn:
        harness.repository.insert_check(transaction=txn, record=_check_record())
        harness.repository.insert_contract_version(transaction=txn, record=_contract_version_record())
        harness.repository.insert_check_result(
            transaction=txn,
            record=DatasetCheckResultRecord(
                check_result_id=record.check_result_id,
                tenant_id=record.tenant_id,
                check_id=record.check_id,
                run_id=record.run_id,
                transaction_id=record.transaction_id,
                checked_manifest_hash=record.checked_manifest_hash,
                validated_against_schema_version_id=record.validated_against_schema_version_id,
                validated_against_schema_version=record.validated_against_schema_version,
                status=record.status,
                details=record.details,
                created_at=record.created_at,
                data_contract_version_id="dqcv_a",
            ),
        )
        found = harness.repository.check_results_for_transaction(
            transaction=txn,
            tenant_id="tenant-test",
            transaction_id="dstx_test",
        )

    assert found[0]["data_contract_version_id"] == "dqcv_a"


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


def test_check_results_for_dataset_are_tenant_dataset_scoped_and_limited(harness: QualityHarness) -> None:
    row_count_check = DatasetCheckRecord(
        check_id="check_b",
        tenant_id="tenant-test",
        dataset_id="ds_test",
        name='{"min":1,"type":"row_count_min"}',
        check_type="row_count_min",
        config={"type": "row_count_min", "min": 1},
        severity="error",
        enabled=True,
    )
    other_dataset_check = DatasetCheckRecord(
        check_id="check_other_dataset",
        tenant_id="tenant-test",
        dataset_id="ds_other",
        name="other-dataset",
        check_type="unique",
        config={"type": "unique", "column": "id"},
        severity="error",
        enabled=True,
    )
    other_tenant_check = DatasetCheckRecord(
        check_id="check_other_tenant",
        tenant_id="tenant-other",
        dataset_id="ds_test",
        name="other-tenant",
        check_type="unique",
        config={"type": "unique", "column": "id"},
        severity="error",
        enabled=True,
    )

    with harness.transaction() as txn:
        harness.repository.insert_check(transaction=txn, record=_check_record())
        harness.repository.insert_check(transaction=txn, record=row_count_check)
        harness.repository.insert_check(transaction=txn, record=other_dataset_check)
        harness.repository.insert_check(transaction=txn, record=other_tenant_check)
        harness.repository.insert_check_result(
            transaction=txn,
            record=_check_result_record(check_result_id="cr_old", created_at="2026-06-10T00:00:00Z"),
        )
        harness.repository.insert_check_result(
            transaction=txn,
            record=_check_result_record(
                "check_b",
                check_result_id="cr_new",
                transaction_id="dstx_new",
                created_at="2026-06-10T00:00:02Z",
            ),
        )
        harness.repository.insert_check_result(
            transaction=txn,
            record=_check_result_record(
                "check_other_dataset",
                check_result_id="cr_other_dataset",
                transaction_id="dstx_other_dataset",
                created_at="2026-06-10T00:00:03Z",
            ),
        )
        harness.repository.insert_check_result(
            transaction=txn,
            record=_check_result_record(
                "check_other_tenant",
                check_result_id="cr_other_tenant",
                tenant_id="tenant-other",
                transaction_id="dstx_other_tenant",
                created_at="2026-06-10T00:00:04Z",
            ),
        )
        rows = harness.repository.check_results_for_dataset(
            transaction=txn,
            tenant_id="tenant-test",
            dataset_id="ds_test",
            limit=10,
        )
        limited = harness.repository.check_results_for_dataset(
            transaction=txn,
            tenant_id="tenant-test",
            dataset_id="ds_test",
            limit=1,
        )

    assert [row["id"] for row in rows] == ["cr_new", "cr_old"]
    assert [row["id"] for row in limited] == ["cr_new"]
    assert rows[0]["check_name"] == row_count_check.name
    assert rows[0]["check_type"] == "row_count_min"
    assert rows[0]["dataset_id"] == "ds_test"


def test_check_result_summary_counts_are_tenant_dataset_scoped(harness: QualityHarness) -> None:
    row_count_check = DatasetCheckRecord(
        check_id="check_b",
        tenant_id="tenant-test",
        dataset_id="ds_test",
        name='{"min":1,"type":"row_count_min"}',
        check_type="row_count_min",
        config={"type": "row_count_min", "min": 1},
        severity="warn",
        enabled=True,
    )
    other_dataset_check = DatasetCheckRecord(
        check_id="check_other_dataset",
        tenant_id="tenant-test",
        dataset_id="ds_other",
        name="other-dataset",
        check_type="unique",
        config={"type": "unique", "column": "id"},
        severity="error",
        enabled=True,
    )
    other_tenant_check = DatasetCheckRecord(
        check_id="check_other_tenant",
        tenant_id="tenant-other",
        dataset_id="ds_test",
        name="other-tenant",
        check_type="unique",
        config={"type": "unique", "column": "id"},
        severity="error",
        enabled=True,
    )

    with harness.transaction() as txn:
        harness.repository.insert_check(transaction=txn, record=_check_record())
        harness.repository.insert_check(transaction=txn, record=row_count_check)
        harness.repository.insert_check(transaction=txn, record=other_dataset_check)
        harness.repository.insert_check(transaction=txn, record=other_tenant_check)
        harness.repository.insert_check_result(transaction=txn, record=_check_result_record())
        harness.repository.insert_check_result(
            transaction=txn,
            record=_check_result_record("check_b", check_result_id="cr_warn", status="WARN"),
        )
        harness.repository.insert_check_result(
            transaction=txn,
            record=_check_result_record("check_b", check_result_id="cr_block", status="BLOCK_COMMIT"),
        )
        harness.repository.insert_check_result(
            transaction=txn,
            record=_check_result_record("check_other_dataset", check_result_id="cr_other_dataset"),
        )
        harness.repository.insert_check_result(
            transaction=txn,
            record=_check_result_record(
                "check_other_tenant",
                check_result_id="cr_other_tenant",
                tenant_id="tenant-other",
            ),
        )
        status_counts = harness.repository.check_result_status_counts_for_dataset(
            transaction=txn,
            tenant_id="tenant-test",
            dataset_id="ds_test",
        )
        type_counts = harness.repository.check_result_type_status_counts_for_dataset(
            transaction=txn,
            tenant_id="tenant-test",
            dataset_id="ds_test",
        )

    assert status_counts == [
        {"status": "BLOCK_COMMIT", "count": 1},
        {"status": "PASS", "count": 1},
        {"status": "WARN", "count": 1},
    ]
    assert type_counts == [
        {"check_type": "row_count_min", "status": "BLOCK_COMMIT", "count": 1},
        {"check_type": "row_count_min", "status": "WARN", "count": 1},
        {"check_type": "unique", "status": "PASS", "count": 1},
    ]
