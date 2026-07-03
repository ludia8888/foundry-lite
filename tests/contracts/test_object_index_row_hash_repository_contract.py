from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import pytest
from foundry_lite.application.ports import ObjectIndexRowHashRepository
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyObjectIndexRowHashRepository
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


class ObjectIndexRowHashHarness(Protocol):
    repository: ObjectIndexRowHashRepository

    def transaction(self) -> AbstractContextManager[Any]: ...


@dataclass
class FakeObjectIndexRowHashRepository:
    rows: dict[tuple[str, str, str], dict[str, str]] = field(default_factory=dict)

    def load_row_hashes(self, *, transaction: Any, tenant_id: str, object_type_id: str) -> dict[str, str]:
        del transaction
        return {
            key[2]: row["row_hash"]
            for key, row in self.rows.items()
            if key[0] == tenant_id and key[1] == object_type_id
        }

    def replace_row_hashes(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_id: str,
        dataset_version_id: str,
        row_hashes: Mapping[str, str],
        updated_at: str,
    ) -> None:
        del transaction
        self.rows = {key: row for key, row in self.rows.items() if key[0] != tenant_id or key[1] != object_type_id}
        for object_id, row_hash in row_hashes.items():
            self.rows[(tenant_id, object_type_id, object_id)] = {
                "row_hash": row_hash,
                "dataset_version_id": dataset_version_id,
                "updated_at": updated_at,
            }

    def apply_row_hash_changes(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_id: str,
        dataset_version_id: str,
        upserted: Mapping[str, str],
        removed_object_ids: Collection[str],
        updated_at: str,
    ) -> None:
        del transaction
        for object_id in removed_object_ids:
            self.rows.pop((tenant_id, object_type_id, object_id), None)
        for object_id, row_hash in upserted.items():
            self.rows[(tenant_id, object_type_id, object_id)] = {
                "row_hash": row_hash,
                "dataset_version_id": dataset_version_id,
                "updated_at": updated_at,
            }


@dataclass
class FakeObjectIndexRowHashHarness:
    repository: ObjectIndexRowHashRepository

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        yield None


@dataclass
class SqlAlchemyObjectIndexRowHashHarness:
    repository: ObjectIndexRowHashRepository
    engine: Engine

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.engine.begin() as conn:
            yield conn


@pytest.fixture(params=["sqlalchemy", "fake", "postgres"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> ObjectIndexRowHashHarness:
    if request.param == "fake":
        return FakeObjectIndexRowHashHarness(FakeObjectIndexRowHashRepository())
    if request.param == "sqlalchemy":
        engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}", future=True)
        db.create_database(engine)
        return SqlAlchemyObjectIndexRowHashHarness(SqlAlchemyObjectIndexRowHashRepository(engine), engine)
    postgres_fixture = request.getfixturevalue("postgres_fixture")
    return SqlAlchemyObjectIndexRowHashHarness(
        SqlAlchemyObjectIndexRowHashRepository(postgres_fixture.engine),
        postgres_fixture.engine,
    )


def test_object_index_row_hash_repository_contract_empty_baseline_reads_as_no_map(
    harness: ObjectIndexRowHashHarness,
) -> None:
    with harness.transaction() as transaction:
        stored = harness.repository.load_row_hashes(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
        )

    # An empty map is the "no baseline yet" signal that forces a full rebuild,
    # so a fresh store must read as exactly {} rather than raising.
    assert stored == {}


def test_object_index_row_hash_repository_contract_replace_seeds_and_supersedes(
    harness: ObjectIndexRowHashHarness,
) -> None:
    with harness.transaction() as transaction:
        harness.repository.replace_row_hashes(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
            dataset_version_id="dsv_orders_1",
            row_hashes={"O-1": "hash-1a", "O-2": "hash-2a"},
            updated_at="2026-06-10T00:00:00Z",
        )
        harness.repository.replace_row_hashes(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
            dataset_version_id="dsv_orders_2",
            row_hashes={"O-2": "hash-2b", "O-3": "hash-3a"},
            updated_at="2026-06-10T00:01:00Z",
        )
        stored = harness.repository.load_row_hashes(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
        )

    # Replacement must drop keys absent from the new snapshot (O-1), otherwise
    # the next incremental diff would tombstone objects that a full rebuild
    # already reconciled.
    assert stored == {"O-2": "hash-2b", "O-3": "hash-3a"}


def test_object_index_row_hash_repository_contract_applies_incremental_delta(
    harness: ObjectIndexRowHashHarness,
) -> None:
    with harness.transaction() as transaction:
        harness.repository.replace_row_hashes(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
            dataset_version_id="dsv_orders_1",
            row_hashes={"O-1": "hash-1a", "O-2": "hash-2a", "O-3": "hash-3a"},
            updated_at="2026-06-10T00:00:00Z",
        )
        harness.repository.apply_row_hash_changes(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
            dataset_version_id="dsv_orders_2",
            upserted={"O-1": "hash-1b", "O-4": "hash-4a"},
            removed_object_ids=["O-3"],
            updated_at="2026-06-10T00:01:00Z",
        )
        stored = harness.repository.load_row_hashes(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
        )

    # Changed keys refresh, new keys insert, removed keys disappear, and the
    # untouched key (O-2) survives without being rewritten.
    assert stored == {"O-1": "hash-1b", "O-2": "hash-2a", "O-4": "hash-4a"}


def test_object_index_row_hash_repository_contract_reapplying_same_delta_is_idempotent(
    harness: ObjectIndexRowHashHarness,
) -> None:
    with harness.transaction() as transaction:
        harness.repository.replace_row_hashes(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
            dataset_version_id="dsv_orders_1",
            row_hashes={"O-1": "hash-1a", "O-2": "hash-2a"},
            updated_at="2026-06-10T00:00:00Z",
        )
        for _ in range(2):
            harness.repository.apply_row_hash_changes(
                transaction=transaction,
                tenant_id="tenant-demo",
                object_type_id="ot_order",
                dataset_version_id="dsv_orders_2",
                upserted={"O-1": "hash-1b"},
                removed_object_ids=["O-2"],
                updated_at="2026-06-10T00:01:00Z",
            )
        stored = harness.repository.load_row_hashes(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
        )

    # A late-arriving duplicate refresh replays the same delta; the map must
    # converge instead of raising on the unique (tenant, type, object) key.
    assert stored == {"O-1": "hash-1b"}


def test_object_index_row_hash_repository_contract_scopes_by_tenant_and_object_type(
    harness: ObjectIndexRowHashHarness,
) -> None:
    with harness.transaction() as transaction:
        harness.repository.replace_row_hashes(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
            dataset_version_id="dsv_orders_1",
            row_hashes={"O-1": "hash-order"},
            updated_at="2026-06-10T00:00:00Z",
        )
        harness.repository.replace_row_hashes(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_customer",
            dataset_version_id="dsv_customers_1",
            row_hashes={"C-1": "hash-customer"},
            updated_at="2026-06-10T00:00:00Z",
        )
        harness.repository.replace_row_hashes(
            transaction=transaction,
            tenant_id="tenant-test",
            object_type_id="ot_order",
            dataset_version_id="dsv_other_1",
            row_hashes={"O-1": "hash-other-tenant"},
            updated_at="2026-06-10T00:00:00Z",
        )
        harness.repository.replace_row_hashes(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
            dataset_version_id="dsv_orders_2",
            row_hashes={"O-2": "hash-order-2"},
            updated_at="2026-06-10T00:01:00Z",
        )
        demo_orders = harness.repository.load_row_hashes(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_order",
        )
        demo_customers = harness.repository.load_row_hashes(
            transaction=transaction,
            tenant_id="tenant-demo",
            object_type_id="ot_customer",
        )
        other_tenant_orders = harness.repository.load_row_hashes(
            transaction=transaction,
            tenant_id="tenant-test",
            object_type_id="ot_order",
        )

    # Replacing one object type's map must not clear neighbouring object types
    # or other tenants; a reindex of Order must never forget Customer state.
    assert demo_orders == {"O-2": "hash-order-2"}
    assert demo_customers == {"C-1": "hash-customer"}
    assert other_tenant_orders == {"O-1": "hash-other-tenant"}
