from __future__ import annotations

import pytest
from foundry_lite.application.core import FoundryLiteCore
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed
from foundry_lite.infrastructure import schema as db

from tests.conftest import prepare_indexed_demo


def test_object_sets_static_dynamic_visibility_and_expiry(core: FoundryLiteCore) -> None:
    ctx = prepare_indexed_demo(core)
    pending_filter = {"property": "status", "op": "eq", "value": "PENDING"}

    dynamic_set = core.create_object_set(
        "Pending Orders",
        "Order",
        set_type="dynamic",
        filter_ast=pending_filter,
        ctx=ctx,
    )
    static_set = core.create_object_set(
        "Snapshot Pending Orders",
        "Order",
        set_type="static",
        object_ids=["O-1001"],
        ctx=ctx,
    )

    assert dynamic_set["objectIds"] == ["O-1001"]
    assert static_set["objectIds"] == ["O-1001"]

    order = core.get_object("Order", "O-1001", ctx=ctx)
    core.apply_action(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key="object-set-dynamic-refresh",
        ctx=ctx,
    )

    assert core.get_object_set(dynamic_set["id"], ctx=ctx)["objectIds"] == []
    assert core.get_object_set(static_set["id"], ctx=ctx)["objectIds"] == ["O-1001"]

    other_user = RequestContext(actor_user_id="other-user", roles=("viewer",))
    with pytest.raises(NotFound):
        core.get_object_set(dynamic_set["id"], ctx=other_user)

    temporary_set = core.create_object_set(
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
        core.get_object_set(temporary_set["id"], ctx=ctx)
    assert temporary_set["id"] not in {item["id"] for item in core.query_object_sets(ctx=ctx)["items"]}
    assert core.cleanup_expired_object_sets(ctx=ctx)["deleted"] == 1


def test_object_set_definition_validation(core: FoundryLiteCore) -> None:
    ctx = prepare_indexed_demo(core)
    valid_filter = {"property": "status", "op": "eq", "value": "PENDING"}

    static_from_definition = core.create_object_set(
        "Static From Definition",
        "Order",
        set_type="static",
        definition={"ids": ["O-1002"]},
        ctx=ctx,
    )
    dynamic_from_definition = core.create_object_set(
        "Public Review Orders",
        "Order",
        set_type="dynamic",
        definition={"filter": {"or": [{"property": "status", "op": "eq", "value": "REVIEW"}]}},
        visibility="public",
        ctx=ctx,
    )
    grouped_dynamic = core.create_object_set(
        "Grouped Pending Orders",
        "Order",
        set_type="dynamic",
        filter_ast={"and": [valid_filter]},
        ctx=ctx,
    )

    assert static_from_definition["objectIds"] == ["O-1002"]
    assert grouped_dynamic["objectIds"] == ["O-1001"]
    assert core.get_object_set(
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
            core.create_object_set(kwargs.pop("name"), "Order", ctx=ctx, **kwargs)

    with pytest.raises(ValidationFailed):
        core.create_object_set(
            "Bad Property",
            "Order",
            set_type="dynamic",
            filter_ast={"property": "missing", "op": "eq", "value": "PENDING"},
            ctx=ctx,
        )
    with pytest.raises(ValidationFailed):
        core.create_object_set(
            "Missing Static Object",
            "Order",
            set_type="static",
            object_ids=["O-404"],
            ctx=ctx,
        )
