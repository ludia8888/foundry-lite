from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import pytest
from foundry_lite.application.ports import DatasetAlreadyExistsError, DatasetRepository
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyDatasetRepository
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


class DatasetRepositoryHarness(Protocol):
    repository: DatasetRepository

    def create_dataset(self, **kwargs: Any) -> None: ...

    def active_dataset_by_ref(self, tenant_id: str, namespace: str, name: str) -> dict[str, Any] | None: ...

    def dataset_by_id(self, tenant_id: str, dataset_id: str) -> dict[str, Any] | None: ...


@dataclass
class FakeDatasetRepository:
    datasets: list[dict[str, Any]] = field(default_factory=list)

    def create_dataset(self, *, transaction: Any, **kwargs: Any) -> None:
        del transaction
        if self.find_active_dataset(
            tenant_id=kwargs["tenant_id"],
            namespace=kwargs["namespace"],
            name=kwargs["name"],
        ):
            raise DatasetAlreadyExistsError
        self.datasets.append({**kwargs, "id": kwargs["dataset_id"], "status": "active"})

    def find_active_dataset(self, *, tenant_id: str, namespace: str, name: str) -> dict[str, Any] | None:
        return self.active_dataset_by_ref(
            transaction=None,
            tenant_id=tenant_id,
            namespace=namespace,
            name=name,
        )

    def active_dataset_by_ref(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        namespace: str,
        name: str,
    ) -> dict[str, Any] | None:
        del transaction
        for dataset in self.datasets:
            if (
                dataset["tenant_id"] == tenant_id
                and dataset["namespace"] == namespace
                and dataset["name"] == name
                and dataset["status"] == "active"
            ):
                return dict(dataset)
        return None

    def dataset_by_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        dataset_id: str,
    ) -> dict[str, Any] | None:
        del transaction
        for dataset in self.datasets:
            if dataset["tenant_id"] == tenant_id and dataset["id"] == dataset_id:
                return dict(dataset)
        return None

    def list_active_datasets(self, *, tenant_id: str) -> list[dict[str, Any]]:
        rows = [dict(dataset) for dataset in self.datasets if dataset["tenant_id"] == tenant_id]
        return sorted(rows, key=lambda row: (row["namespace"], row["name"]))


@dataclass
class FakeDatasetRepositoryHarness:
    repository: FakeDatasetRepository

    def create_dataset(self, **kwargs: Any) -> None:
        self.repository.create_dataset(transaction=None, **kwargs)

    def active_dataset_by_ref(self, tenant_id: str, namespace: str, name: str) -> dict[str, Any] | None:
        return self.repository.active_dataset_by_ref(
            transaction=None,
            tenant_id=tenant_id,
            namespace=namespace,
            name=name,
        )

    def dataset_by_id(self, tenant_id: str, dataset_id: str) -> dict[str, Any] | None:
        return self.repository.dataset_by_id(transaction=None, tenant_id=tenant_id, dataset_id=dataset_id)


@dataclass
class SqlAlchemyDatasetRepositoryHarness:
    repository: SqlAlchemyDatasetRepository
    engine: Engine

    def create_dataset(self, **kwargs: Any) -> None:
        with self.engine.begin() as conn:
            self.repository.create_dataset(transaction=conn, **kwargs)

    def active_dataset_by_ref(self, tenant_id: str, namespace: str, name: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            return self.repository.active_dataset_by_ref(
                transaction=conn,
                tenant_id=tenant_id,
                namespace=namespace,
                name=name,
            )

    def dataset_by_id(self, tenant_id: str, dataset_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            return self.repository.dataset_by_id(
                transaction=conn,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
            )


def _dataset_payload(dataset_id: str, *, tenant_id: str = "tenant-demo") -> dict[str, Any]:
    return {
        "dataset_id": dataset_id,
        "tenant_id": tenant_id,
        "namespace": "raw",
        "name": "orders",
        "description": "Orders",
        "storage_kind": "parquet_manifest",
        "storage_uri": f"memory://{dataset_id}",
        "owner_team": "ops",
        "classification": "internal",
        "primary_key": ["order_id"],
        "partition_spec": ["region"],
        "sort_order": ["order_id"],
        "target_file_size_bytes": 134217728,
        "created_at": "2026-06-10T00:00:00Z",
        "updated_at": "2026-06-10T00:00:00Z",
    }


@pytest.fixture(params=["sqlalchemy", "fake", "postgres"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> DatasetRepositoryHarness:
    if request.param == "fake":
        return FakeDatasetRepositoryHarness(FakeDatasetRepository())
    if request.param == "sqlalchemy":
        engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}", future=True)
        db.create_database(engine)
        return SqlAlchemyDatasetRepositoryHarness(SqlAlchemyDatasetRepository(engine), engine)
    postgres_fixture = request.getfixturevalue("postgres_fixture")
    return SqlAlchemyDatasetRepositoryHarness(
        SqlAlchemyDatasetRepository(postgres_fixture.engine),
        postgres_fixture.engine,
    )


def test_dataset_repository_contract_create_find_and_duplicate(harness: DatasetRepositoryHarness) -> None:
    payload = _dataset_payload("ds_orders")
    harness.create_dataset(**payload)

    found = harness.repository.find_active_dataset(tenant_id="tenant-demo", namespace="raw", name="orders")
    assert found is not None
    assert found["id"] == "ds_orders"
    assert found["primary_key"] == ["order_id"]
    assert found["partition_spec"] == ["region"]
    assert found["sort_order"] == ["order_id"]
    assert found["target_file_size_bytes"] == 134217728
    assert harness.active_dataset_by_ref("tenant-demo", "raw", "orders") == found
    assert harness.active_dataset_by_ref("tenant-other", "raw", "orders") is None
    assert harness.dataset_by_id("tenant-demo", "ds_orders") == found
    assert harness.dataset_by_id("tenant-other", "ds_orders") is None

    with pytest.raises(DatasetAlreadyExistsError):
        harness.create_dataset(**_dataset_payload("ds_duplicate"))

    other_tenant_payload = _dataset_payload("ds_other_tenant", tenant_id="tenant-other")
    harness.create_dataset(**other_tenant_payload)
    assert harness.repository.find_active_dataset(tenant_id="tenant-other", namespace="raw", name="orders") is not None


def test_dataset_repository_contract_lists_active_datasets_by_tenant(
    harness: DatasetRepositoryHarness,
) -> None:
    orders = _dataset_payload("ds_orders")
    customers = {**_dataset_payload("ds_customers"), "namespace": "clean", "name": "customers"}
    other = _dataset_payload("ds_other", tenant_id="tenant-other")
    harness.create_dataset(**orders)
    harness.create_dataset(**customers)
    harness.create_dataset(**other)

    rows = harness.repository.list_active_datasets(tenant_id="tenant-demo")

    assert [(row["namespace"], row["name"], row["id"]) for row in rows] == [
        ("clean", "customers", "ds_customers"),
        ("raw", "orders", "ds_orders"),
    ]
