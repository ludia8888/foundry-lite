from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import pytest
from foundry_lite.application.ports.transform_repository import (
    TransformRecord,
    TransformRepository,
    TransformRunRecord,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyTransformRepository
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine


class TransformHarness(Protocol):
    repository: TransformRepository

    def transaction(self) -> AbstractContextManager[Any]: ...

    def transform_rows(self) -> list[dict[str, Any]]: ...

    def transform_run_rows(self) -> list[dict[str, Any]]: ...


@dataclass
class FakeTransformRepository:
    transforms: list[dict[str, Any]] = field(default_factory=list)
    transform_runs: list[dict[str, Any]] = field(default_factory=list)

    def transform_by_api_name(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        api_name: str,
    ) -> dict[str, Any] | None:
        del transaction
        for row in self.transforms:
            if row["tenant_id"] == tenant_id and row["api_name"] == api_name:
                return dict(row)
        return None

    def insert_transform(self, *, transaction: Any, record: TransformRecord) -> None:
        del transaction
        self.transforms.append(
            {
                "id": record.transform_id,
                "tenant_id": record.tenant_id,
                "api_name": record.api_name,
                "language": record.language,
                "entrypoint": record.entrypoint,
                "mode": record.mode,
                "inputs": dict(record.inputs),
                "output_dataset_ref": record.output_dataset_ref,
                "checks": list(record.checks),
            }
        )

    def update_transform_definition(
        self,
        *,
        transaction: Any,
        transform_id: str,
        language: str,
        entrypoint: str,
        mode: str,
        inputs: dict[str, str],
        output_dataset_ref: str,
        checks: list[dict[str, Any]],
    ) -> None:
        del transaction
        for row in self.transforms:
            if row["id"] == transform_id:
                row.update(
                    {
                        "language": language,
                        "entrypoint": entrypoint,
                        "mode": mode,
                        "inputs": dict(inputs),
                        "output_dataset_ref": output_dataset_ref,
                        "checks": list(checks),
                    }
                )
                return

    def transform_by_id(self, *, transaction: Any, transform_id: str) -> dict[str, Any] | None:
        del transaction
        for row in self.transforms:
            if row["id"] == transform_id:
                return dict(row)
        return None

    def insert_transform_run(self, *, transaction: Any, record: TransformRunRecord) -> None:
        del transaction
        self.transform_runs.append(
            {
                "id": record.transform_run_id,
                "tenant_id": record.tenant_id,
                "transform_id": record.transform_id,
                "status": record.status,
                "input_versions": dict(record.input_versions),
                "output_version_id": record.output_version_id,
                "transaction_id": record.transaction_id,
                "error": record.error,
                "created_at": record.created_at,
                "completed_at": record.completed_at,
            }
        )

    def update_transform_run_terminal(
        self,
        *,
        transaction: Any,
        transform_run_id: str,
        status: str,
        output_version_id: str | None,
        error: dict[str, Any] | None,
        completed_at: str,
    ) -> None:
        del transaction
        for row in self.transform_runs:
            if row["id"] == transform_run_id:
                row.update(
                    {
                        "status": status,
                        "output_version_id": output_version_id,
                        "error": error,
                        "completed_at": completed_at,
                    }
                )
                return


@dataclass
class FakeTransformHarness:
    repository: FakeTransformRepository = field(default_factory=FakeTransformRepository)

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        yield self

    def transform_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.repository.transforms]

    def transform_run_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.repository.transform_runs]


@dataclass
class SqlAlchemyTransformHarness:
    engine: Engine
    repository: SqlAlchemyTransformRepository

    @classmethod
    def create(cls, tmp_path: Path) -> SqlAlchemyTransformHarness:
        engine = create_engine(f"sqlite:///{tmp_path / 'transform.db'}", future=True)
        db.create_database(engine)
        with engine.begin() as conn:
            conn.execute(
                db.tenants.insert().values(
                    id="tenant-test",
                    name="Test",
                    created_at="2025-01-01T00:00:00Z",
                )
            )
        return cls(engine=engine, repository=SqlAlchemyTransformRepository(engine))

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.engine.begin() as conn:
            yield conn

    def transform_rows(self) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            return [dict(row) for row in conn.execute(select(db.transforms)).mappings().all()]

    def transform_run_rows(self) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            return [dict(row) for row in conn.execute(select(db.transform_runs)).mappings().all()]


@pytest.fixture(params=["fake", "sqlalchemy"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> TransformHarness:
    if request.param == "fake":
        return FakeTransformHarness()
    return SqlAlchemyTransformHarness.create(tmp_path)


def _transform_record(api_name: str = "clean_orders") -> TransformRecord:
    return TransformRecord(
        transform_id="tf_test",
        tenant_id="tenant-test",
        api_name=api_name,
        language="sql",
        entrypoint="/tmp/example.sql",
        mode="snapshot",
        inputs={"orders": "raw.orders"},
        output_dataset_ref="clean.orders",
        checks=[{"type": "primary_key"}],
    )


def _transform_run_record() -> TransformRunRecord:
    return TransformRunRecord(
        transform_run_id="trun_test",
        tenant_id="tenant-test",
        transform_id="tf_test",
        status="RUNNING",
        input_versions={"raw.orders": "dsv_1"},
        output_version_id=None,
        transaction_id="dtx_1",
        error=None,
        created_at="2025-01-01T00:00:00Z",
        completed_at=None,
    )


def test_transform_by_api_name_returns_none_when_absent(harness: TransformHarness) -> None:
    with harness.transaction() as txn:
        assert (
            harness.repository.transform_by_api_name(transaction=txn, tenant_id="tenant-test", api_name="missing")
            is None
        )


def test_insert_transform_persists_and_returns_row_by_id(harness: TransformHarness) -> None:
    record = _transform_record()
    with harness.transaction() as txn:
        harness.repository.insert_transform(transaction=txn, record=record)
        row = harness.repository.transform_by_id(transaction=txn, transform_id=record.transform_id)
        assert row is not None
        assert row["api_name"] == "clean_orders"
        assert row["output_dataset_ref"] == "clean.orders"
        assert row["checks"] == [{"type": "primary_key"}]
    assert len(harness.transform_rows()) == 1


def test_update_transform_definition_overwrites_fields(harness: TransformHarness) -> None:
    record = _transform_record()
    with harness.transaction() as txn:
        harness.repository.insert_transform(transaction=txn, record=record)
        harness.repository.update_transform_definition(
            transaction=txn,
            transform_id=record.transform_id,
            language="python",
            entrypoint="/tmp/new.py",
            mode="append",
            inputs={"orders": "raw.orders_v2"},
            output_dataset_ref="clean.orders_v2",
            checks=[{"type": "not_null", "column": "id"}],
        )
        row = harness.repository.transform_by_id(transaction=txn, transform_id=record.transform_id)
        assert row is not None
        assert row["language"] == "python"
        assert row["entrypoint"] == "/tmp/new.py"
        assert row["mode"] == "append"
        assert row["inputs"] == {"orders": "raw.orders_v2"}
        assert row["output_dataset_ref"] == "clean.orders_v2"
        assert row["checks"] == [{"type": "not_null", "column": "id"}]


def test_insert_transform_run_persists(harness: TransformHarness) -> None:
    with harness.transaction() as txn:
        harness.repository.insert_transform(transaction=txn, record=_transform_record())
        harness.repository.insert_transform_run(transaction=txn, record=_transform_run_record())
    rows = harness.transform_run_rows()
    assert len(rows) == 1
    assert rows[0]["status"] == "RUNNING"
    assert rows[0]["input_versions"] == {"raw.orders": "dsv_1"}


def test_update_transform_run_terminal_marks_success(harness: TransformHarness) -> None:
    with harness.transaction() as txn:
        harness.repository.insert_transform(transaction=txn, record=_transform_record())
        harness.repository.insert_transform_run(transaction=txn, record=_transform_run_record())
        harness.repository.update_transform_run_terminal(
            transaction=txn,
            transform_run_id="trun_test",
            status="SUCCESS",
            output_version_id="dsv_out",
            error=None,
            completed_at="2025-01-01T01:00:00Z",
        )
    row = harness.transform_run_rows()[0]
    assert row["status"] == "SUCCESS"
    assert row["output_version_id"] == "dsv_out"
    assert row["completed_at"] == "2025-01-01T01:00:00Z"


def test_update_transform_run_terminal_marks_failure(harness: TransformHarness) -> None:
    with harness.transaction() as txn:
        harness.repository.insert_transform(transaction=txn, record=_transform_record())
        harness.repository.insert_transform_run(transaction=txn, record=_transform_run_record())
        harness.repository.update_transform_run_terminal(
            transaction=txn,
            transform_run_id="trun_test",
            status="FAILED",
            output_version_id=None,
            error={"message": "boom"},
            completed_at="2025-01-01T01:00:00Z",
        )
    row = harness.transform_run_rows()[0]
    assert row["status"] == "FAILED"
    assert row["error"] == {"message": "boom"}
    assert row["output_version_id"] is None


def test_transform_by_api_name_isolated_by_tenant(harness: TransformHarness) -> None:
    if isinstance(harness, SqlAlchemyTransformHarness):
        with harness.engine.begin() as conn:
            conn.execute(
                db.tenants.insert().values(
                    id="tenant-other",
                    name="Other",
                    created_at="2025-01-01T00:00:00Z",
                )
            )
    record_a = _transform_record(api_name="shared_name")
    record_b = TransformRecord(
        transform_id="tf_other",
        tenant_id="tenant-other",
        api_name="shared_name",
        language="sql",
        entrypoint="/tmp/other.sql",
        mode="snapshot",
        inputs={},
        output_dataset_ref="other.target",
        checks=[],
    )
    with harness.transaction() as txn:
        harness.repository.insert_transform(transaction=txn, record=record_a)
        harness.repository.insert_transform(transaction=txn, record=record_b)
        found_a = harness.repository.transform_by_api_name(
            transaction=txn, tenant_id="tenant-test", api_name="shared_name"
        )
        found_b = harness.repository.transform_by_api_name(
            transaction=txn, tenant_id="tenant-other", api_name="shared_name"
        )
    assert found_a is not None and found_a["id"] == "tf_test"
    assert found_b is not None and found_b["id"] == "tf_other"
