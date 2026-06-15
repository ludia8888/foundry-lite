from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import ObjectQueryItem, ObjectQueryResult, ObjectRecordRow
from foundry_lite.application.services.object_store.set_members import collect_dynamic_object_set_members
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed
from foundry_lite.infrastructure import schema as db

from tests.conftest import prepare_indexed_demo


def test_object_sets_static_dynamic_visibility_and_expiry(core: FoundryLite) -> None:
    ctx = prepare_indexed_demo(core)
    pending_filter = {"property": "status", "op": "eq", "value": "PENDING"}

    dynamic_set = core.objects.create_set(
        "Pending Orders",
        "Order",
        set_type="dynamic",
        filter_ast=pending_filter,
        ctx=ctx,
    )
    static_set = core.objects.create_set(
        "Snapshot Pending Orders",
        "Order",
        set_type="static",
        object_ids=["O-1001"],
        ctx=ctx,
    )

    assert dynamic_set["objectIds"] == ["O-1001"]
    assert static_set["objectIds"] == ["O-1001"]

    order = core.objects.get("Order", "O-1001", ctx=ctx)
    core.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key="object-set-dynamic-refresh",
        ctx=ctx,
    )

    assert core.objects.get_set(dynamic_set["id"], ctx=ctx)["objectIds"] == []
    assert core.objects.get_set(static_set["id"], ctx=ctx)["objectIds"] == ["O-1001"]

    other_user = RequestContext(actor_user_id="other-user", roles=("viewer",))
    with pytest.raises(NotFound):
        core.objects.get_set(dynamic_set["id"], ctx=other_user)

    temporary_set = core.objects.create_set(
        "Temporary Pending Orders",
        "Order",
        set_type="dynamic",
        filter_ast=pending_filter,
        visibility="temporary",
        ttl_seconds=60,
        ctx=ctx,
    )
    with core.engine.begin() as conn:
        conn.execute(
            db.object_sets.update()
            .where(db.object_sets.c.id == temporary_set["id"])
            .values(expires_at="2000-01-01T00:00:00+00:00")
        )

    with pytest.raises(NotFound):
        core.objects.get_set(temporary_set["id"], ctx=ctx)
    assert temporary_set["id"] not in {item["id"] for item in core.objects.query_sets(ctx=ctx)["items"]}
    assert core.objects.cleanup_expired_sets(ctx=ctx)["deleted"] == 1
    audit_events = core.operations.list_runs(ctx=ctx)["auditEvents"]
    assert any(event["event_type"] == "object_set.expired_deleted" for event in audit_events)


def test_static_object_set_rechecks_object_permission(core: FoundryLite) -> None:
    admin = prepare_indexed_demo(core)
    static_set = core.objects.create_set(
        "Public Margin Snapshot",
        "Order",
        set_type="static",
        object_ids=["O-1001"],
        visibility="public",
        ctx=admin,
    )
    viewer = RequestContext(actor_user_id="viewer-user", roles=("viewer",))

    payload = core.objects.get_set(static_set["id"], ctx=viewer)
    item = payload["items"][0]

    assert payload["objectIds"] == ["O-1001"]
    assert item["objectId"] == "O-1001"
    assert item["properties"]["margin"] == "***MASKED***"


class _PagedObjectQuery:
    def __init__(self, pages: dict[str | None, ObjectQueryResult]) -> None:
        self.pages = pages
        self.calls: list[tuple[int, str | None]] = []

    def query_objects(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        order_by: Sequence[Mapping[str, str]] | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ObjectQueryResult:
        del ctx, order_by
        assert object_type_api_name == "Order"
        assert filter_ast == {"property": "status", "op": "eq", "value": "PENDING"}
        assert limit == 500
        self.calls.append((limit, cursor))
        if cursor not in self.pages:
            raise AssertionError(f"unexpected cursor {cursor}")
        return self.pages[cursor]

    def _object_query_item(
        self,
        ctx: RequestContext,
        object_type_api_name: str,
        row: ObjectRecordRow,
    ) -> ObjectQueryItem:
        del ctx
        return {
            "objectType": object_type_api_name,
            "objectId": row["object_id"],
            "objectVersion": row["object_version"],
            "properties": row["properties"],
        }


def test_dynamic_object_set_members_page_with_public_query_limit() -> None:
    _assert_dynamic_object_set_members_page_with_public_query_limit()


def test_dynamic_object_set_cannot_bypass_page_limit() -> None:
    _assert_dynamic_object_set_members_page_with_public_query_limit()


def _assert_dynamic_object_set_members_page_with_public_query_limit() -> None:
    pending_filter = {"property": "status", "op": "eq", "value": "PENDING"}
    pages: dict[str | None, ObjectQueryResult] = {
        None: {
            "items": [
                {"objectType": "Order", "objectId": "O-1001", "objectVersion": 1, "properties": {}},
            ],
            "nextCursor": "second-page",
        },
        "second-page": {
            "items": [
                {"objectType": "Order", "objectId": "O-1002", "objectVersion": 1, "properties": {}},
            ],
            "nextCursor": None,
        },
    }
    query = _PagedObjectQuery(pages)

    object_ids, items = collect_dynamic_object_set_members(
        query,
        "Order",
        ctx=RequestContext(actor_user_id="demo-user", roles=("viewer",)),
        filter_ast=pending_filter,
        include_items=True,
    )

    assert object_ids == ["O-1001", "O-1002"]
    assert [item["objectId"] for item in items] == ["O-1001", "O-1002"]
    assert [call[1] for call in query.calls] == [None, "second-page"]


def test_object_set_definition_validation(core: FoundryLite) -> None:
    ctx = prepare_indexed_demo(core)
    valid_filter = {"property": "status", "op": "eq", "value": "PENDING"}

    static_from_definition = core.objects.create_set(
        "Static From Definition",
        "Order",
        set_type="static",
        definition={"ids": ["O-1002"]},
        ctx=ctx,
    )
    dynamic_from_definition = core.objects.create_set(
        "Public Review Orders",
        "Order",
        set_type="dynamic",
        definition={"filter": {"or": [{"property": "status", "op": "eq", "value": "REVIEW"}]}},
        visibility="public",
        ctx=ctx,
    )
    grouped_dynamic = core.objects.create_set(
        "Grouped Pending Orders",
        "Order",
        set_type="dynamic",
        filter_ast={"and": [valid_filter]},
        ctx=ctx,
    )

    assert static_from_definition["objectIds"] == ["O-1002"]
    assert grouped_dynamic["objectIds"] == ["O-1001"]
    assert core.objects.get_set(
        dynamic_from_definition["id"],
        ctx=RequestContext(actor_user_id="viewer-2", roles=("viewer",)),
    )["objectIds"] == ["O-1002"]

    invalid_creates = [
        {
            "name": "",
            "set_type": "dynamic",
            "filter_ast": valid_filter,
        },
        {
            "name": "Bad Type",
            "set_type": "unknown",
            "filter_ast": valid_filter,
        },
        {
            "name": "Bad Visibility",
            "set_type": "dynamic",
            "filter_ast": valid_filter,
            "visibility": "team",
        },
        {
            "name": "Bad TTL",
            "set_type": "dynamic",
            "filter_ast": valid_filter,
            "ttl_seconds": 0,
        },
        {
            "name": "Definition Mismatch",
            "set_type": "static",
            "definition": {"filter": valid_filter},
        },
        {
            "name": "Empty Static ID",
            "set_type": "static",
            "object_ids": [""],
        },
        {
            "name": "Missing Filter",
            "set_type": "dynamic",
        },
        {
            "name": "Bad Operation",
            "set_type": "dynamic",
            "filter_ast": {"property": "status", "op": "bad", "value": "PENDING"},
        },
        {
            "name": "Missing Value",
            "set_type": "dynamic",
            "filter_ast": {"property": "status", "op": "eq"},
        },
        {
            "name": "Empty Group",
            "set_type": "dynamic",
            "filter_ast": {"and": []},
        },
        {
            "name": "Non Object Group Item",
            "set_type": "dynamic",
            "filter_ast": {"and": ["bad"]},
        },
    ]
    for kwargs in invalid_creates:
        with pytest.raises(ValidationFailed):
            core.objects.create_set(kwargs.pop("name"), "Order", ctx=ctx, **kwargs)

    with pytest.raises(ValidationFailed):
        core.objects.create_set(
            "Bad Property",
            "Order",
            set_type="dynamic",
            filter_ast={"property": "missing", "op": "eq", "value": "PENDING"},
            ctx=ctx,
        )
    with pytest.raises(ValidationFailed):
        core.objects.create_set(
            "Missing Static Object",
            "Order",
            set_type="static",
            object_ids=["O-404"],
            ctx=ctx,
        )
