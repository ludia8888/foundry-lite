"""Atomic multi-object/link commit of a validated EditPlan against fakes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from foundry_lite.application.ports.action_repository import (
    ObjectCreateWrite,
    ObjectDeleteWrite,
    ObjectEditRecord,
    ObjectLinkDeleteWrite,
    ObjectLinkWrite,
    ObjectTargetUpdate,
)
from foundry_lite.application.services.action_edit_plan_committer import ActionEditPlanCommitter
from foundry_lite.domain.action_runtime.edit_plan import (
    EditPlan,
    LinkCreate,
    LinkDelete,
    ObjectCreate,
    ObjectDelete,
    ObjectModify,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected

CTX = RequestContext(tenant_id="tenant-demo", actor_user_id="actor-1")


class _FakeObjectStore:
    """A minimal object store shared by the fake repository and lookup."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.links: list[dict[str, Any]] = []
        self.edits: list[ObjectEditRecord] = []
        self.terminal_calls: list[dict[str, Any]] = []
        self.terminal_result = True

    def seed(
        self, *, record_id: str, object_type: str, object_id: str, version: int, properties: dict[str, Any]
    ) -> None:
        self.rows[(object_type, object_id)] = {
            "id": record_id,
            "tenant_id": CTX.tenant_id,
            "object_type_id": f"ot_{object_type.lower()}",
            "object_type_api_name": object_type,
            "object_id": object_id,
            "object_version": version,
            "properties": dict(properties),
            "base_properties": dict(properties),
            "edit_properties": {},
        }


class _FakeRepository:
    def __init__(self, store: _FakeObjectStore) -> None:
        self.store = store

    def create_object_record(self, *, transaction: Any, record: ObjectCreateWrite) -> bool:
        del transaction
        key = (record.object_type_api_name, record.object_id)
        if key in self.store.rows:
            return False
        self.store.rows[key] = {
            "id": record.object_record_id,
            "tenant_id": record.tenant_id,
            "object_type_id": record.object_type_id,
            "object_type_api_name": record.object_type_api_name,
            "object_id": record.object_id,
            "object_version": 1,
            "properties": dict(record.properties),
            "base_properties": {},
            "edit_properties": dict(record.properties),
        }
        return True

    def update_object_target(self, *, transaction: Any, record: ObjectTargetUpdate) -> bool:
        del transaction
        for row in self.store.rows.values():
            if row["id"] == record.object_record_id and row["object_version"] == record.expected_object_version:
                row.update(
                    edit_properties=dict(record.edit_properties),
                    properties=dict(record.properties),
                    object_version=record.next_object_version,
                )
                return True
        return False

    def soft_delete_object_target(self, *, transaction: Any, record: ObjectDeleteWrite) -> bool:
        del transaction
        for row in self.store.rows.values():
            if row["id"] == record.object_record_id and row["object_version"] == record.expected_object_version:
                row.update(object_version=record.expected_object_version + 1, deleted=True)
                return True
        return False

    def create_object_link(self, *, transaction: Any, record: ObjectLinkWrite) -> None:
        del transaction
        self.store.links.append(
            {
                "link_type_api_name": record.link_type_api_name,
                "from_object_id": record.from_object_id,
                "to_object_id": record.to_object_id,
            }
        )

    def soft_delete_object_link(self, *, transaction: Any, record: ObjectLinkDeleteWrite) -> bool:
        del transaction
        for link in self.store.links:
            if link["from_object_id"] == record.from_object_id and link["to_object_id"] == record.to_object_id:
                link["deleted"] = True
                return True
        return False

    def insert_object_edit(self, *, transaction: Any, record: ObjectEditRecord) -> None:
        del transaction
        self.store.edits.append(record)

    def update_action_run_terminal(self, *, transaction: Any, **kwargs: Any) -> bool:
        del transaction
        self.store.terminal_calls.append(kwargs)
        return self.store.terminal_result


class _FakeLookup:
    def __init__(self, store: _FakeObjectStore) -> None:
        self.store = store

    def _object_record(
        self, conn: Any, ctx: RequestContext, api_name: str, object_id: str, object_type_id: str | None = None
    ) -> dict[str, Any] | None:
        del conn, ctx, object_type_id
        return self.store.rows.get((api_name, object_id))


class _FakeOntology:
    def _active_object_type(self, conn: Any, ctx: RequestContext, api_name: str) -> dict[str, Any]:
        del conn, ctx
        return {"id": f"ot_{api_name.lower()}", "api_name": api_name}


class _FakeIndexer:
    def _merge_properties(
        self, conn: Any, object_type_id: str, base: Mapping[str, object], edits: Mapping[str, object]
    ) -> dict[str, object]:
        del conn, object_type_id
        return {**base, **edits}


class _FakeLinkTypes:
    def link_type(self, conn: Any, ctx: RequestContext, api_name: str) -> dict[str, Any]:
        del conn, ctx
        return {
            "id": f"lt_{api_name.lower()}",
            "from_object_type_id": "ot_order",
            "from_api_name": "Order",
            "to_object_type_id": "ot_shipment",
            "to_api_name": "Shipment",
        }


class _FakeRuntime:
    def __init__(self) -> None:
        self.outbox: list[dict[str, Any]] = []
        self.audits: list[dict[str, Any]] = []

    def _outbox(
        self,
        conn: Any,
        ctx: RequestContext,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> str | None:
        del conn, ctx
        self.outbox.append(
            {
                "event_type": event_type,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "payload": dict(payload),
                "idempotency_key": idempotency_key,
            }
        )
        return f"outbox_{len(self.outbox)}"

    def _audit(self, conn: Any, ctx: RequestContext, **kwargs: Any) -> None:
        del conn, ctx
        self.audits.append(kwargs)


def _committer(store: _FakeObjectStore, runtime: _FakeRuntime) -> ActionEditPlanCommitter:
    return ActionEditPlanCommitter(
        action_repository=_FakeRepository(store),  # type: ignore[arg-type]
        object_indexer=_FakeIndexer(),  # type: ignore[arg-type]
        object_lookup=_FakeLookup(store),  # type: ignore[arg-type]
        ontology_lookup=_FakeOntology(),  # type: ignore[arg-type]
        link_type_lookup=_FakeLinkTypes(),  # type: ignore[arg-type]
        runtime=runtime,  # type: ignore[arg-type]
    )


def _fulfill_plan(*, modify_version: int = 7) -> EditPlan:
    return EditPlan(
        objects_to_modify=(
            ObjectModify("op-modify", "close", "Order", "O-1001", modify_version, {"status": "FULFILLED"}),
        ),
        objects_to_create=(ObjectCreate("op-create", "mk", "Shipment", "S-1", {"carrier": "UPS"}),),
        links_to_create=(LinkCreate("op-link", "ln", "OrderShipment", "O-1001", "S-1"),),
    )


def test_commit_fulfill_order_plan_applies_every_edit_atomically() -> None:
    store = _FakeObjectStore()
    store.seed(
        record_id="obj_order_1", object_type="Order", object_id="O-1001", version=7, properties={"status": "PENDING"}
    )
    runtime = _FakeRuntime()

    result = _committer(store, runtime).commit_plan(conn=None, ctx=CTX, action_run_id="arun_1", plan=_fulfill_plan())

    order = store.rows[("Order", "O-1001")]
    shipment = store.rows[("Shipment", "S-1")]
    assert order["object_version"] == 8
    assert order["properties"] == {"status": "FULFILLED"}
    assert shipment["object_version"] == 1
    assert shipment["properties"] == {"carrier": "UPS"}
    assert store.links == [{"link_type_api_name": "OrderShipment", "from_object_id": "O-1001", "to_object_id": "S-1"}]
    # Order: creates, then modifies, then link creates — each recorded in the unified edit log.
    assert [(edit.object_id, edit.edit_type) for edit in store.edits] == [
        ("S-1", "create_object"),
        ("O-1001", "set_property"),
        ("O-1001", "create_link"),
    ]
    assert result.links_created == 1
    assert set(result.created_object_ids) == {"S-1"}
    assert len(store.terminal_calls) == 1
    assert store.terminal_calls[0]["transition"].to_status == "succeeded"
    # One run-level event plus one object.changed per edit (create, modify, link).
    assert [event["event_type"] for event in runtime.outbox] == [
        "action.run.committed",
        "object.changed",
        "object.changed",
        "object.changed",
    ]
    assert len(runtime.audits) == 1


def test_commit_rolls_back_when_a_modify_target_is_stale() -> None:
    store = _FakeObjectStore()
    store.seed(
        record_id="obj_order_1", object_type="Order", object_id="O-1001", version=9, properties={"status": "PENDING"}
    )
    runtime = _FakeRuntime()

    with pytest.raises(ConflictDetected, match="version conflict"):
        _committer(store, runtime).commit_plan(
            conn=None, ctx=CTX, action_run_id="arun_1", plan=_fulfill_plan(modify_version=7)
        )

    # The terminal transition never ran, so the surrounding transaction rolls the whole plan back.
    assert store.terminal_calls == []
    assert runtime.outbox == []


def test_commit_rejects_a_create_whose_identity_already_exists() -> None:
    store = _FakeObjectStore()
    store.seed(
        record_id="obj_ship_1", object_type="Shipment", object_id="S-1", version=1, properties={"carrier": "DHL"}
    )
    runtime = _FakeRuntime()
    plan = EditPlan(objects_to_create=(ObjectCreate("op-create", "mk", "Shipment", "S-1", {"carrier": "UPS"}),))

    with pytest.raises(ConflictDetected, match="already exists"):
        _committer(store, runtime).commit_plan(conn=None, ctx=CTX, action_run_id="arun_1", plan=plan)

    assert store.terminal_calls == []


def test_commit_create_or_modify_creates_the_object_when_absent() -> None:
    store = _FakeObjectStore()
    runtime = _FakeRuntime()
    plan = EditPlan(
        objects_to_modify=(
            ObjectModify("op-upsert", "up", "Customer", "C-1", 0, {"tier": "GOLD"}, should_create_if_absent=True),
        )
    )

    result = _committer(store, runtime).commit_plan(conn=None, ctx=CTX, action_run_id="arun_1", plan=plan)

    created = store.rows[("Customer", "C-1")]
    assert created["properties"] == {"tier": "GOLD"}
    assert [edit.edit_type for edit in store.edits] == ["create_object"]
    assert set(result.created_object_ids) == {"C-1"}


def test_commit_soft_deletes_an_object_and_records_the_prior_values() -> None:
    store = _FakeObjectStore()
    store.seed(
        record_id="obj_order_1", object_type="Order", object_id="O-1001", version=4, properties={"status": "OPEN"}
    )
    runtime = _FakeRuntime()
    plan = EditPlan(objects_to_delete=(ObjectDelete("op-del", "rm", "Order", "O-1001", 4),))

    result = _committer(store, runtime).commit_plan(conn=None, ctx=CTX, action_run_id="arun_1", plan=plan)

    assert store.rows[("Order", "O-1001")]["deleted"] is True
    delete_edit = store.edits[0]
    assert delete_edit.edit_type == "delete_object"
    assert delete_edit.previous_values == {"status": "OPEN"}
    assert set(result.deleted_object_ids) == {"O-1001"}


def test_commit_soft_deletes_a_link_and_records_it_in_the_unified_edit_log() -> None:
    store = _FakeObjectStore()
    store.links.append({"link_type_api_name": "OrderShipment", "from_object_id": "O-1001", "to_object_id": "S-1"})
    runtime = _FakeRuntime()
    plan = EditPlan(links_to_delete=(LinkDelete("op-unlink", "unlink", "OrderShipment", "O-1001", "S-1"),))

    result = _committer(store, runtime).commit_plan(conn=None, ctx=CTX, action_run_id="arun_1", plan=plan)

    assert store.links[0]["deleted"] is True
    assert result.links_deleted == 1
    assert [(edit.object_id, edit.edit_type) for edit in store.edits] == [("O-1001", "delete_link")]


def test_commit_rejects_deleting_a_link_that_is_no_longer_active() -> None:
    store = _FakeObjectStore()
    runtime = _FakeRuntime()
    plan = EditPlan(links_to_delete=(LinkDelete("op-unlink", "unlink", "OrderShipment", "O-1001", "S-1"),))

    with pytest.raises(ConflictDetected, match="link no longer exists"):
        _committer(store, runtime).commit_plan(conn=None, ctx=CTX, action_run_id="arun_1", plan=plan)

    assert store.edits == []
    assert store.terminal_calls == []
    assert runtime.outbox == []
