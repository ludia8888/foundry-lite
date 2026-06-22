from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import pytest
from foundry_lite.application.ports.materialization_repository import (
    ActionRunWatermark,
    MaterializationRecord,
    MaterializationRepository,
    MaterializationRow,
    MaterializationRunRecord,
    ObjectRecordVersionRow,
)
from foundry_lite.application.ports.transaction_context import (
    MATERIALIZATION_RUN_FAILED,
    MATERIALIZATION_RUN_SUCCEEDED,
    StatusTransition,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyMaterializationRepository
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine


class MaterializationHarness(Protocol):
    repository: MaterializationRepository

    def transaction(self) -> AbstractContextManager[Any]: ...

    def materialization_rows(self) -> list[dict[str, Any]]: ...

    def materialization_run_rows(self) -> list[dict[str, Any]]: ...

    def seed_action_run(self, *, run_id: str, created_at: str, completed_at: str | None = None) -> None: ...

    def seed_object_record(
        self,
        *,
        record_id: str,
        updated_at: str,
        object_change_sequence: int | None = None,
        index_version: str = "active",
    ) -> None: ...


@dataclass
class FakeMaterializationRepository:
    materializations: list[MaterializationRow] = field(default_factory=list)
    materialization_runs: list[dict[str, Any]] = field(default_factory=list)
    action_run_watermarks: list[dict[str, str | None]] = field(default_factory=list)
    object_record_watermarks: list[dict[str, object]] = field(default_factory=list)
    object_record_versions: list[dict[str, object]] = field(default_factory=list)
    active_index_versions: dict[tuple[str, str], str] = field(default_factory=dict)

    def materialization_by_api_name(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        api_name: str,
    ) -> MaterializationRow | None:
        del transaction
        for row in self.materializations:
            if row["tenant_id"] == tenant_id and row["api_name"] == api_name:
                return row.copy()
        return None

    def insert_materialization(self, *, transaction: Any, record: MaterializationRecord) -> None:
        del transaction
        row: MaterializationRow = {
            "id": record.materialization_id,
            "tenant_id": record.tenant_id,
            "api_name": record.api_name,
            "materialization_type": record.materialization_type,
            "source_ref": record.source_ref.copy(),
            "target_ref": record.target_ref.copy(),
            "trigger_config": record.trigger_config.copy(),
            "enabled": record.enabled,
        }
        self.materializations.append(row)

    def materialization_by_id(self, *, transaction: Any, materialization_id: str) -> MaterializationRow | None:
        del transaction
        for row in self.materializations:
            if row["id"] == materialization_id:
                return row.copy()
        return None

    def insert_materialization_run(self, *, transaction: Any, record: MaterializationRunRecord) -> None:
        del transaction
        self.materialization_runs.append(
            {
                "id": record.materialization_run_id,
                "tenant_id": record.tenant_id,
                "materialization_id": record.materialization_id,
                "api_name": record.api_name,
                "status": record.status,
                "source_cursor": dict(record.source_cursor),
                "object_store_watermark": dict(record.object_store_watermark),
                "consistency_level": record.consistency_level,
                "target_dataset_version_id": record.target_dataset_version_id,
                "row_count": record.row_count,
                "error": record.error,
                "created_at": record.created_at,
                "completed_at": record.completed_at,
            }
        )

    def update_materialization_run_terminal(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        materialization_run_id: str,
        transition: StatusTransition,
        target_dataset_version_id: str | None,
        row_count: int | None,
        error: Mapping[str, object] | None,
        completed_at: str,
    ) -> bool:
        del transaction
        for row in self.materialization_runs:
            if (
                row["tenant_id"] == tenant_id
                and row["id"] == materialization_run_id
                and row["status"] in transition.from_statuses
            ):
                row.update(
                    {
                        "status": transition.to_status,
                        "target_dataset_version_id": target_dataset_version_id,
                        "row_count": row_count,
                        "error": error,
                        "completed_at": completed_at,
                    }
                )
                return True
        return False

    def latest_action_run_watermark(self, *, transaction: Any, tenant_id: str) -> ActionRunWatermark:
        del transaction
        rows = [row for row in self.action_run_watermarks if row["tenant_id"] == tenant_id and row["completed_at"]]
        if not rows:
            return {"completed_at": None, "action_run_id": None}
        latest = max(rows, key=lambda row: (str(row["completed_at"]), str(row["action_run_id"])))
        return {"completed_at": str(latest["completed_at"]), "action_run_id": str(latest["action_run_id"])}

    def latest_object_record_watermark(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        index_version: str,
    ) -> int | None:
        del transaction
        sequences = [
            row["object_change_sequence"]
            for row in self.object_record_watermarks
            if row["tenant_id"] == tenant_id
            and row["object_type_api_name"] == object_type_api_name
            and row["index_version"] == index_version
            and isinstance(row["object_change_sequence"], int)
        ]
        return max(sequence for sequence in sequences if isinstance(sequence, int)) if sequences else None

    def active_object_index_version(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
    ) -> str:
        del transaction
        return self.active_index_versions.get((tenant_id, object_type_api_name), "active")

    def object_records_at_watermark(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        index_version: str,
        max_object_change_sequence: int,
    ) -> list[ObjectRecordVersionRow]:
        del transaction
        rows = [
            row
            for row in self.object_record_versions
            if row["tenant_id"] == tenant_id
            and row["object_type_api_name"] == object_type_api_name
            and row["index_version"] == index_version
            and isinstance(row["object_change_sequence"], int)
            and row["object_change_sequence"] <= max_object_change_sequence
        ]
        return _latest_object_record_versions(rows)


@dataclass
class FakeMaterializationHarness:
    repository: MaterializationRepository = field(default_factory=FakeMaterializationRepository)

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        yield self

    def materialization_rows(self) -> list[dict[str, Any]]:
        repository = self.repository
        assert isinstance(repository, FakeMaterializationRepository)
        return [dict(row) for row in repository.materializations]

    def materialization_run_rows(self) -> list[dict[str, Any]]:
        repository = self.repository
        assert isinstance(repository, FakeMaterializationRepository)
        return [dict(row) for row in repository.materialization_runs]

    def seed_action_run(self, *, run_id: str, created_at: str, completed_at: str | None = None) -> None:
        repository = self.repository
        assert isinstance(repository, FakeMaterializationRepository)
        repository.action_run_watermarks.append(
            {"tenant_id": "tenant-test", "action_run_id": run_id, "completed_at": completed_at or created_at}
        )

    def seed_object_record(
        self,
        *,
        record_id: str,
        updated_at: str,
        object_change_sequence: int | None = None,
        index_version: str = "active",
    ) -> None:
        repository = self.repository
        assert isinstance(repository, FakeMaterializationRepository)
        repository.object_record_watermarks.append(
            {
                "tenant_id": "tenant-test",
                "record_id": record_id,
                "object_type_api_name": "TestObject",
                "index_version": index_version,
                "object_change_sequence": object_change_sequence,
            }
        )
        if object_change_sequence is not None:
            repository.object_record_versions.append(
                _object_record_version_row(
                    record_id=record_id,
                    object_change_sequence=object_change_sequence,
                    updated_at=updated_at,
                    index_version=index_version,
                )
            )


@dataclass
class SqlAlchemyMaterializationHarness:
    engine: Engine
    repository: MaterializationRepository

    @classmethod
    def from_engine(cls, engine: Engine) -> SqlAlchemyMaterializationHarness:
        return cls(engine=engine, repository=SqlAlchemyMaterializationRepository(engine))

    @classmethod
    def create(cls, tmp_path: Path) -> SqlAlchemyMaterializationHarness:
        engine = create_engine(f"sqlite:///{tmp_path / 'materialization.db'}", future=True)
        db.create_database(engine)
        with engine.begin() as conn:
            conn.execute(
                db.tenants.insert().values(
                    id="tenant-test",
                    name="Test",
                    created_at="2025-01-01T00:00:00Z",
                )
            )
        return cls.from_engine(engine)

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.engine.begin() as conn:
            yield conn

    def materialization_rows(self) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            return [dict(row) for row in conn.execute(select(db.materializations)).mappings().all()]

    def materialization_run_rows(self) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            return [dict(row) for row in conn.execute(select(db.materialization_runs)).mappings().all()]

    def seed_action_run(self, *, run_id: str, created_at: str, completed_at: str | None = None) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                db.action_runs.insert().values(
                    id=run_id,
                    tenant_id="tenant-test",
                    action_type_id="at_test",
                    action_type_api_name="TestAction",
                    actor_user_id="user-test",
                    target_object_type_id="ot_test",
                    target_object_type_api_name="TestObject",
                    target_object_id="obj_test",
                    expected_object_version=1,
                    parameters={},
                    status="succeeded",
                    idempotency_key=f"key-{run_id}",
                    request_fingerprint=f"fingerprint-{run_id}",
                    error=None,
                    created_at=created_at,
                    completed_at=completed_at or created_at,
                )
            )

    def seed_object_record(
        self,
        *,
        record_id: str,
        updated_at: str,
        object_change_sequence: int | None = None,
        index_version: str = "active",
    ) -> None:
        with self.engine.begin() as conn:
            current = _object_record_current_row(
                record_id=record_id,
                object_change_sequence=object_change_sequence,
                updated_at=updated_at,
                index_version=index_version,
            )
            existing = conn.execute(
                select(db.object_records).where(
                    db.object_records.c.id == record_id,
                    db.object_records.c.tenant_id == "tenant-test",
                )
            ).first()
            if existing is None:
                conn.execute(db.object_records.insert().values(**current))
            else:
                conn.execute(db.object_records.update().where(db.object_records.c.id == record_id).values(**current))
            if object_change_sequence is not None:
                conn.execute(
                    db.object_record_versions.insert().values(
                        **_object_record_version_row(
                            record_id=record_id,
                            object_change_sequence=object_change_sequence,
                            updated_at=updated_at,
                            index_version=index_version,
                        )
                    )
                )


@pytest.fixture(params=["fake", "sqlalchemy", "postgres"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> MaterializationHarness:
    if request.param == "fake":
        return FakeMaterializationHarness()
    if request.param == "sqlalchemy":
        return SqlAlchemyMaterializationHarness.create(tmp_path)
    postgres_fixture = request.getfixturevalue("postgres_fixture")
    return SqlAlchemyMaterializationHarness.from_engine(postgres_fixture.engine)


def _materialization_record(api_name: str = "action_log") -> MaterializationRecord:
    return MaterializationRecord(
        materialization_id="mat_test",
        tenant_id="tenant-test",
        api_name=api_name,
        materialization_type="action_log",
        source_ref={"type": "action_runs"},
        target_ref={"dataset": "ops.action_log"},
        trigger_config={"type": "manual"},
        enabled=True,
    )


def _materialization_run_record() -> MaterializationRunRecord:
    return MaterializationRunRecord(
        materialization_run_id="mrun_test",
        tenant_id="tenant-test",
        materialization_id="mat_test",
        api_name="action_log",
        status="running",
        source_cursor={"action_run_completed_at_lte": None, "action_run_id_lte": None},
        object_store_watermark={"action_run_completed_at_lte": None, "action_run_id_lte": None},
        consistency_level="watermark",
        target_dataset_version_id=None,
        row_count=None,
        error=None,
        created_at="2025-01-01T00:00:00Z",
        completed_at=None,
    )


def _object_record_version_row(
    *,
    record_id: str,
    object_change_sequence: int,
    updated_at: str,
    index_version: str = "active",
) -> dict[str, object]:
    return {
        "id": f"orv_{record_id}_{object_change_sequence}",
        "tenant_id": "tenant-test",
        "object_record_id": record_id,
        "object_type_id": "ot_test",
        "object_type_api_name": "TestObject",
        "object_id": record_id,
        "index_version": index_version,
        "is_active": True,
        "properties": {"objectId": record_id, "sequence": object_change_sequence},
        "base_properties": {},
        "edit_properties": {},
        "property_versions": {},
        "source_dataset_version_id": "dsv_test",
        "source_hash": "hash-test",
        "object_version": object_change_sequence,
        "object_change_sequence": object_change_sequence,
        "deleted": False,
        "deletion_reason": None,
        "created_at": updated_at,
        "updated_at": updated_at,
    }


def _object_record_current_row(
    *,
    record_id: str,
    object_change_sequence: int | None,
    updated_at: str,
    index_version: str = "active",
) -> dict[str, object]:
    return {
        "id": record_id,
        "tenant_id": "tenant-test",
        "object_type_id": "ot_test",
        "object_type_api_name": "TestObject",
        "object_id": record_id,
        "object_version": object_change_sequence or 1,
        "base_properties": {},
        "edit_properties": {},
        "properties": {},
        "property_versions": {},
        "source_dataset_version_id": "dsv_test",
        "object_change_sequence": object_change_sequence,
        "index_version": index_version,
        "is_active": True,
        "deleted": False,
        "created_at": updated_at,
        "updated_at": updated_at,
    }


def _latest_object_record_versions(rows: list[dict[str, object]]) -> list[ObjectRecordVersionRow]:
    latest: dict[tuple[object, object, object], dict[str, object]] = {}
    for row in rows:
        key = (row["object_type_id"], row["object_id"], row["index_version"])
        existing = latest.get(key)
        row_sequence = row["object_change_sequence"]
        existing_sequence = existing["object_change_sequence"] if existing is not None else None
        if isinstance(row_sequence, int) and (
            not isinstance(existing_sequence, int) or row_sequence > existing_sequence
        ):
            latest[key] = row
    return [dict(row) for row in sorted(latest.values(), key=lambda item: str(item["object_id"]))]


def test_materialization_by_api_name_returns_none_when_absent(harness: MaterializationHarness) -> None:
    with harness.transaction() as txn:
        assert (
            harness.repository.materialization_by_api_name(transaction=txn, tenant_id="tenant-test", api_name="missing")
            is None
        )


def test_insert_materialization_round_trips_via_id(harness: MaterializationHarness) -> None:
    record = _materialization_record()
    with harness.transaction() as txn:
        harness.repository.insert_materialization(transaction=txn, record=record)
        row = harness.repository.materialization_by_id(transaction=txn, materialization_id=record.materialization_id)
    assert row is not None
    assert row["api_name"] == "action_log"
    assert row["target_ref"] == {"dataset": "ops.action_log"}
    assert row["enabled"] is True


def test_materialization_by_api_name_isolated_by_tenant(harness: MaterializationHarness) -> None:
    if isinstance(harness, SqlAlchemyMaterializationHarness):
        with harness.engine.begin() as conn:
            conn.execute(
                db.tenants.insert().values(
                    id="tenant-other",
                    name="Other",
                    created_at="2025-01-01T00:00:00Z",
                )
            )
    record_a = _materialization_record()
    record_b = MaterializationRecord(
        materialization_id="mat_other",
        tenant_id="tenant-other",
        api_name="action_log",
        materialization_type="action_log",
        source_ref={"type": "action_runs"},
        target_ref={"dataset": "ops.action_log"},
        trigger_config={"type": "manual"},
        enabled=True,
    )
    with harness.transaction() as txn:
        harness.repository.insert_materialization(transaction=txn, record=record_a)
        harness.repository.insert_materialization(transaction=txn, record=record_b)
        found_a = harness.repository.materialization_by_api_name(
            transaction=txn, tenant_id="tenant-test", api_name="action_log"
        )
        found_b = harness.repository.materialization_by_api_name(
            transaction=txn, tenant_id="tenant-other", api_name="action_log"
        )
    assert found_a is not None and found_a["id"] == "mat_test"
    assert found_b is not None and found_b["id"] == "mat_other"


def test_insert_materialization_run_persists(harness: MaterializationHarness) -> None:
    with harness.transaction() as txn:
        harness.repository.insert_materialization(transaction=txn, record=_materialization_record())
        harness.repository.insert_materialization_run(transaction=txn, record=_materialization_run_record())
    rows = harness.materialization_run_rows()
    assert len(rows) == 1
    assert rows[0]["status"] == "running"
    assert rows[0]["consistency_level"] == "watermark"


def test_update_materialization_run_terminal_succeeded(harness: MaterializationHarness) -> None:
    with harness.transaction() as txn:
        harness.repository.insert_materialization(transaction=txn, record=_materialization_record())
        harness.repository.insert_materialization_run(transaction=txn, record=_materialization_run_record())
        updated = harness.repository.update_materialization_run_terminal(
            transaction=txn,
            tenant_id="tenant-test",
            materialization_run_id="mrun_test",
            transition=MATERIALIZATION_RUN_SUCCEEDED,
            target_dataset_version_id="dsv_target",
            row_count=42,
            error=None,
            completed_at="2025-01-01T01:00:00Z",
        )
        stale = harness.repository.update_materialization_run_terminal(
            transaction=txn,
            tenant_id="tenant-test",
            materialization_run_id="mrun_test",
            transition=MATERIALIZATION_RUN_FAILED,
            target_dataset_version_id=None,
            row_count=None,
            error={"message": "late failure"},
            completed_at="2025-01-01T01:00:01Z",
        )
    row = harness.materialization_run_rows()[0]
    assert updated is True
    assert stale is False
    assert row["status"] == "succeeded"
    assert row["row_count"] == 42
    assert row["target_dataset_version_id"] == "dsv_target"


def test_update_materialization_run_terminal_failed(harness: MaterializationHarness) -> None:
    with harness.transaction() as txn:
        harness.repository.insert_materialization(transaction=txn, record=_materialization_record())
        harness.repository.insert_materialization_run(transaction=txn, record=_materialization_run_record())
        updated = harness.repository.update_materialization_run_terminal(
            transaction=txn,
            tenant_id="tenant-test",
            materialization_run_id="mrun_test",
            transition=MATERIALIZATION_RUN_FAILED,
            target_dataset_version_id=None,
            row_count=None,
            error={"message": "boom"},
            completed_at="2025-01-01T01:00:00Z",
        )
    row = harness.materialization_run_rows()[0]
    assert updated is True
    assert row["status"] == "failed"
    assert row["error"] == {"message": "boom"}


def test_latest_action_run_watermark_handles_empty(harness: MaterializationHarness) -> None:
    with harness.transaction() as txn:
        assert harness.repository.latest_action_run_watermark(transaction=txn, tenant_id="tenant-test") == {
            "completed_at": None,
            "action_run_id": None,
        }


def test_latest_action_run_watermark_returns_completed_at_cursor(harness: MaterializationHarness) -> None:
    harness.seed_action_run(run_id="ar_a", created_at="2025-01-04T00:00:00Z", completed_at="2025-01-01T00:00:00Z")
    harness.seed_action_run(run_id="ar_b", created_at="2025-01-01T00:00:00Z", completed_at="2025-01-02T00:00:00Z")
    harness.seed_action_run(run_id="ar_c", created_at="2025-01-01T12:00:00Z", completed_at="2025-01-02T00:00:00Z")
    with harness.transaction() as txn:
        watermark = harness.repository.latest_action_run_watermark(transaction=txn, tenant_id="tenant-test")
    assert watermark == {"completed_at": "2025-01-02T00:00:00Z", "action_run_id": "ar_c"}


def test_materialization_created_at_tie_does_not_skip_rows(harness: MaterializationHarness) -> None:
    harness.seed_action_run(
        run_id="ar_early_commit", created_at="2025-01-03T00:00:00Z", completed_at="2025-01-01T00:00:00Z"
    )
    harness.seed_action_run(run_id="ar_tie_a", created_at="2025-01-01T00:00:00Z", completed_at="2025-01-02T00:00:00Z")
    harness.seed_action_run(run_id="ar_tie_b", created_at="2025-01-01T00:00:00Z", completed_at="2025-01-02T00:00:00Z")

    with harness.transaction() as txn:
        watermark = harness.repository.latest_action_run_watermark(transaction=txn, tenant_id="tenant-test")

    assert watermark == {"completed_at": "2025-01-02T00:00:00Z", "action_run_id": "ar_tie_b"}


def test_latest_object_record_watermark_handles_empty(harness: MaterializationHarness) -> None:
    with harness.transaction() as txn:
        assert (
            harness.repository.latest_object_record_watermark(
                transaction=txn,
                tenant_id="tenant-test",
                object_type_api_name="TestObject",
                index_version="active",
            )
            is None
        )


def test_latest_object_record_watermark_returns_max_change_sequence(harness: MaterializationHarness) -> None:
    harness.seed_object_record(record_id="or_a", updated_at="2025-01-03T00:00:00Z", object_change_sequence=1)
    harness.seed_object_record(record_id="or_b", updated_at="2025-01-01T00:00:00Z", object_change_sequence=3)
    harness.seed_object_record(
        record_id="or_shadow",
        updated_at="2025-01-04T00:00:00Z",
        object_change_sequence=5,
        index_version="shadow",
    )
    with harness.transaction() as txn:
        watermark = harness.repository.latest_object_record_watermark(
            transaction=txn,
            tenant_id="tenant-test",
            object_type_api_name="TestObject",
            index_version="active",
        )
    assert watermark == 3


def test_object_records_at_watermark_returns_latest_version_per_object(harness: MaterializationHarness) -> None:
    harness.seed_object_record(record_id="or_a", updated_at="2025-01-01T00:00:00Z", object_change_sequence=1)
    harness.seed_object_record(record_id="or_a", updated_at="2025-01-02T00:00:00Z", object_change_sequence=3)
    harness.seed_object_record(record_id="or_b", updated_at="2025-01-03T00:00:00Z", object_change_sequence=2)
    harness.seed_object_record(
        record_id="or_shadow",
        updated_at="2025-01-04T00:00:00Z",
        object_change_sequence=4,
        index_version="shadow",
    )
    with harness.transaction() as txn:
        rows = harness.repository.object_records_at_watermark(
            transaction=txn,
            tenant_id="tenant-test",
            object_type_api_name="TestObject",
            index_version="active",
            max_object_change_sequence=2,
        )
    assert [row["object_id"] for row in rows] == ["or_a", "or_b"]
    assert [row["object_change_sequence"] for row in rows] == [1, 2]


def test_active_object_index_version_defaults_to_active(harness: MaterializationHarness) -> None:
    with harness.transaction() as txn:
        assert (
            harness.repository.active_object_index_version(
                transaction=txn,
                tenant_id="tenant-test",
                object_type_api_name="TestObject",
            )
            == "active"
        )
