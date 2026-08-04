"""Contract proof for durable Action branch overlay CAS and idempotency."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from foundry_lite.application.action_branch_types import ActionBranchEditRecord, ActionBranchObjectWrite
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.action_branch_repository import SqlAlchemyActionBranchRepository
from sqlalchemy import create_engine


def test_action_branch_repository_contract_cas_and_operation_idempotency() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    repository = SqlAlchemyActionBranchRepository(engine)
    with engine.begin() as transaction:
        first = repository.store_object_overlay(transaction=transaction, record=_overlay(None, 2))
        duplicate = repository.store_object_overlay(transaction=transaction, record=_overlay(None, 2))
        assert first is not None and duplicate is None
        changed = repository.store_object_overlay(transaction=transaction, record=_overlay(2, 3))
        stale = repository.store_object_overlay(transaction=transaction, record=_overlay(2, 4))
        assert changed is not None and changed["overlay_version"] == 3
        assert stale is None
        edit = repository.insert_edit(transaction=transaction, record=_edit())
        repeated = repository.insert_edit(transaction=transaction, record=_edit())
        assert edit is not None and repeated is None
        assert len(repository.list_edits(transaction=transaction, tenant_id="tenant-a", branch_id="branch-a")) == 1


def test_postgres_action_branch_overlay_concurrent_create_has_one_winner(postgres_fixture) -> None:
    repository = SqlAlchemyActionBranchRepository(postgres_fixture.engine)
    barrier = Barrier(2)

    def create(index: int):
        with postgres_fixture.engine.begin() as transaction:
            barrier.wait()
            return repository.store_object_overlay(
                transaction=transaction,
                record=_overlay(None, 2, overlay_id=f"overlay-{index}"),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        rows = list(executor.map(create, (1, 2)))
    assert sum(row is not None for row in rows) == 1


def _overlay(
    expected: int | None,
    version: int,
    *,
    overlay_id: str = "overlay-1",
) -> ActionBranchObjectWrite:
    return ActionBranchObjectWrite(
        overlay_id=overlay_id,
        tenant_id="tenant-a",
        branch_id="branch-a",
        object_type_id="type-order",
        object_type_api_name="Order",
        object_id="O-1",
        base_object_version=1,
        expected_overlay_version=expected,
        overlay_version=version,
        properties={"status": "REVIEW"},
        is_deleted=False,
        action_run_id="run-1",
        created_at="2026-08-03T00:00:00Z",
        updated_at="2026-08-03T00:00:01Z",
    )


def _edit() -> ActionBranchEditRecord:
    return ActionBranchEditRecord(
        edit_id="edit-1",
        tenant_id="tenant-a",
        branch_id="branch-a",
        action_run_id="run-1",
        operation_key="idem:rule:O-1",
        ordinal=0,
        edit_kind="set_property",
        object_type_id="type-order",
        object_type_api_name="Order",
        object_id="O-1",
        before={"status": "PENDING"},
        after={"status": "REVIEW"},
        created_at="2026-08-03T00:00:01Z",
    )
