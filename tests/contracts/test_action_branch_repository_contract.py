"""Contract proof for durable Action branch overlay CAS and idempotency."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from foundry_lite.application.action_branch_types import (
    ActionBranchEditRecord,
    ActionBranchLinkWrite,
    ActionBranchObjectWrite,
)
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
        overlays = repository.object_overlays_for_ids(
            transaction=transaction,
            tenant_id="tenant-a",
            branch_id="branch-a",
            object_type_api_name="Order",
            object_ids=["O-1", "O-missing"],
        )
        assert [row["object_id"] for row in overlays] == ["O-1"]
        assert len(repository.list_edits(transaction=transaction, tenant_id="tenant-a", branch_id="branch-a")) == 1


def test_action_branch_link_overlay_composes_base_identity_and_cas() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    repository = SqlAlchemyActionBranchRepository(engine)
    with engine.begin() as transaction:
        transaction.execute(db.object_links.insert().values(**_base_link()))
        base = repository.active_base_link(
            transaction=transaction,
            tenant_id="tenant-a",
            link_type_api_name="OrderDependency",
            from_object_id="O-1",
            to_object_id="O-2",
        )
        assert base is not None and base["link_version"] == 3
        first = repository.store_link_overlay(transaction=transaction, record=_link_overlay(None, 4, True))
        duplicate = repository.store_link_overlay(transaction=transaction, record=_link_overlay(None, 4, True))
        changed = repository.store_link_overlay(transaction=transaction, record=_link_overlay(4, 5, False))
        stale = repository.store_link_overlay(transaction=transaction, record=_link_overlay(4, 6, True))
        assert first is not None and first["deleted"] is True
        assert duplicate is None
        assert changed is not None and changed["overlay_version"] == 5 and changed["deleted"] is False
        assert stale is None
        listed = repository.list_link_overlays(transaction=transaction, tenant_id="tenant-a", branch_id="branch-a")
        assert [item["id"] for item in listed] == ["link-overlay-1"]
        outgoing = repository.link_overlays_from(
            transaction=transaction,
            tenant_id="tenant-a",
            branch_id="branch-a",
            link_type_api_name="OrderDependency",
            from_api_name="Order",
            from_object_id="O-1",
            limit=2,
        )
        incoming = repository.link_overlays_to(
            transaction=transaction,
            tenant_id="tenant-a",
            branch_id="branch-a",
            link_type_api_name="OrderDependency",
            to_api_name="Order",
            to_object_id="O-2",
            limit=2,
        )
        assert [row["id"] for row in outgoing] == ["link-overlay-1"]
        assert [row["id"] for row in incoming] == ["link-overlay-1"]


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


def test_postgres_action_branch_link_overlay_concurrent_create_has_one_winner(postgres_fixture) -> None:
    repository = SqlAlchemyActionBranchRepository(postgres_fixture.engine)
    barrier = Barrier(2)

    def create(index: int):
        with postgres_fixture.engine.begin() as transaction:
            barrier.wait()
            return repository.store_link_overlay(
                transaction=transaction,
                record=_link_overlay(None, 1, False, overlay_id=f"link-overlay-{index}"),
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


def _link_overlay(
    expected: int | None,
    version: int,
    is_deleted: bool,
    *,
    overlay_id: str = "link-overlay-1",
) -> ActionBranchLinkWrite:
    return ActionBranchLinkWrite(
        overlay_id=overlay_id,
        tenant_id="tenant-a",
        branch_id="branch-a",
        link_type_id="link-type-1",
        link_type_api_name="OrderDependency",
        from_object_type_id="type-order",
        from_api_name="Order",
        from_object_id="O-1",
        to_object_type_id="type-order",
        to_api_name="Order",
        to_object_id="O-2",
        base_link_version=3,
        expected_overlay_version=expected,
        overlay_version=version,
        is_deleted=is_deleted,
        action_run_id="run-1",
        created_at="2026-08-03T00:00:00Z",
        updated_at="2026-08-03T00:00:01Z",
    )


def _base_link() -> dict[str, object]:
    return {
        "id": "base-link-1",
        "tenant_id": "tenant-a",
        "link_type_id": "link-type-1",
        "link_type_api_name": "OrderDependency",
        "index_version": "active",
        "is_active": True,
        "from_object_type_id": "type-order",
        "from_api_name": "Order",
        "from_object_id": "O-1",
        "to_object_type_id": "type-order",
        "to_api_name": "Order",
        "to_object_id": "O-2",
        "properties": {},
        "source_dataset_version_id": None,
        "link_version": 3,
        "deleted": False,
        "deletion_reason": None,
        "updated_at": "2026-08-03T00:00:00Z",
    }
