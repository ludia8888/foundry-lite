from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import ObjectQueryItem, ObjectQueryResult, ObjectRecordRow
from foundry_lite.application.services.object_store import set_members, set_search_around, set_validation
from foundry_lite.application.services.object_store.set_members import (
    collect_dynamic_object_set_members,
    resolve_search_around_object_ids,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, PermissionDenied, ValidationFailed
from foundry_lite.infrastructure import schema as db

from tests.conftest import prepare_indexed_demo


def test_object_sets_static_dynamic_visibility_and_expiry(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    pending_filter = {"property": "status", "op": "eq", "value": "PENDING"}

    dynamic_set = foundry.objects.create_set(
        "Pending Orders",
        "Order",
        set_type="dynamic",
        filter_ast=pending_filter,
        ctx=ctx,
    )
    static_set = foundry.objects.create_set(
        "Snapshot Pending Orders",
        "Order",
        set_type="static",
        object_ids=["O-1001"],
        ctx=ctx,
    )

    assert dynamic_set["objectIds"] == ["O-1001"]
    assert dynamic_set["accessScope"] == "private"
    assert dynamic_set["lifecycle"] == "permanent"
    assert static_set["objectIds"] == ["O-1001"]

    order = foundry.objects.get("Order", "O-1001", ctx=ctx)
    foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id="O-1001",
        expected_object_version=order["objectVersion"],
        params={"reason": "Inventory confirmed"},
        idempotency_key="object-set-dynamic-refresh",
        ctx=ctx,
    )

    assert foundry.objects.get_set(dynamic_set["id"], ctx=ctx)["objectIds"] == []
    assert foundry.objects.get_set(static_set["id"], ctx=ctx)["objectIds"] == ["O-1001"]

    other_user = RequestContext(actor_user_id="other-user", roles=("viewer",))
    with pytest.raises(NotFound):
        foundry.objects.get_set(dynamic_set["id"], ctx=other_user)

    temporary_set = foundry.objects.create_set(
        "Temporary Pending Orders",
        "Order",
        set_type="dynamic",
        filter_ast=pending_filter,
        visibility="temporary",
        ttl_seconds=60,
        ctx=ctx,
    )
    assert temporary_set["visibility"] == "temporary"
    assert temporary_set["accessScope"] == "private"
    assert temporary_set["lifecycle"] == "temporary"
    with foundry.engine.begin() as conn:
        cast(Any, conn).execute(
            db.object_sets.update()
            .where(db.object_sets.c.id == temporary_set["id"])
            .values(expires_at="2000-01-01T00:00:00+00:00")
        )

    with pytest.raises(NotFound):
        foundry.objects.get_set(temporary_set["id"], ctx=ctx)
    assert temporary_set["id"] not in {item["id"] for item in foundry.objects.query_sets(ctx=ctx)["items"]}
    assert foundry.objects.cleanup_expired_sets(ctx=ctx)["deleted"] == 1
    audit_events = foundry.operations.list_runs(ctx=ctx)["auditEvents"]
    assert any(event["event_type"] == "object_set.expired_deleted" for event in audit_events)


def test_object_set_access_scope_and_lifecycle_are_separate(foundry: FoundryLite) -> None:
    admin = prepare_indexed_demo(foundry)
    viewer = RequestContext(actor_user_id="viewer-user", roles=("viewer",))

    public_temporary = foundry.objects.create_set(
        "Public Temporary Review Orders",
        "Order",
        set_type="dynamic",
        filter_ast={"property": "status", "op": "eq", "value": "REVIEW"},
        access_scope="public",
        lifecycle="temporary",
        ttl_seconds=60,
        ctx=admin,
    )
    public_permanent = foundry.objects.create_set(
        "Public Permanent Snapshot",
        "Order",
        set_type="static",
        object_ids=["O-1001"],
        access_scope="public",
        lifecycle="permanent",
        ctx=admin,
    )

    assert public_temporary["visibility"] == "public"
    assert public_temporary["accessScope"] == "public"
    assert public_temporary["lifecycle"] == "temporary"
    assert public_temporary["expiresAt"] is not None
    assert public_permanent["visibility"] == "public"
    assert public_permanent["accessScope"] == "public"
    assert public_permanent["lifecycle"] == "permanent"
    assert public_permanent["expiresAt"] is None
    assert foundry.objects.get_set(public_temporary["id"], ctx=viewer)["accessScope"] == "public"


def test_static_object_set_rechecks_object_permission(foundry: FoundryLite) -> None:
    admin = prepare_indexed_demo(foundry)
    static_set = foundry.objects.create_set(
        "Public Margin Snapshot",
        "Order",
        set_type="static",
        object_ids=["O-1001"],
        visibility="public",
        ctx=admin,
    )
    viewer = RequestContext(actor_user_id="viewer-user", roles=("viewer",))

    payload = foundry.objects.get_set(static_set["id"], ctx=viewer)
    assert "items" in payload
    item = payload["items"][0]

    assert payload["objectIds"] == ["O-1001"]
    assert item["objectId"] == "O-1001"
    assert item["properties"]["margin"] == "***MASKED***"


def test_object_set_manage_permission_is_required_for_mutations(foundry: FoundryLite) -> None:
    admin = prepare_indexed_demo(foundry)
    viewer = RequestContext(actor_user_id="viewer-user", roles=("viewer",))
    public_set = foundry.objects.create_set(
        "Public Pending Orders",
        "Order",
        set_type="static",
        object_ids=["O-1001"],
        visibility="public",
        ctx=admin,
    )

    assert foundry.objects.get_set(public_set["id"], ctx=viewer)["objectIds"] == ["O-1001"]
    with pytest.raises(PermissionDenied):
        foundry.objects.create_set(
            "Viewer Mutation",
            "Order",
            set_type="static",
            object_ids=["O-1001"],
            ctx=viewer,
        )
    with pytest.raises(PermissionDenied):
        foundry.objects.cleanup_expired_sets(ctx=viewer)


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


def test_object_set_definition_validation(foundry: FoundryLite) -> None:
    ctx = prepare_indexed_demo(foundry)
    valid_filter = {"property": "status", "op": "eq", "value": "PENDING"}

    static_from_definition = foundry.objects.create_set(
        "Static From Definition",
        "Order",
        set_type="static",
        definition={"ids": ["O-1002"]},
        ctx=ctx,
    )
    dynamic_from_definition = foundry.objects.create_set(
        "Public Review Orders",
        "Order",
        set_type="dynamic",
        definition={"filter": {"or": [{"property": "status", "op": "eq", "value": "REVIEW"}]}},
        visibility="public",
        ctx=ctx,
    )
    grouped_dynamic = foundry.objects.create_set(
        "Grouped Pending Orders",
        "Order",
        set_type="dynamic",
        filter_ast={"and": [valid_filter]},
        ctx=ctx,
    )

    assert static_from_definition["objectIds"] == ["O-1002"]
    assert grouped_dynamic["objectIds"] == ["O-1001"]
    assert foundry.objects.get_set(
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
            "name": "Bad Access Scope",
            "set_type": "dynamic",
            "filter_ast": valid_filter,
            "access_scope": "team",
        },
        {
            "name": "Bad Lifecycle",
            "set_type": "dynamic",
            "filter_ast": valid_filter,
            "lifecycle": "forever",
        },
        {
            "name": "Temporary Without TTL",
            "set_type": "dynamic",
            "filter_ast": valid_filter,
            "lifecycle": "temporary",
        },
        {
            "name": "Permanent With TTL",
            "set_type": "dynamic",
            "filter_ast": valid_filter,
            "lifecycle": "permanent",
            "ttl_seconds": 60,
        },
        {
            "name": "Visibility Access Conflict",
            "set_type": "dynamic",
            "filter_ast": valid_filter,
            "visibility": "temporary",
            "access_scope": "public",
            "ttl_seconds": 60,
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
            foundry.objects.create_set(kwargs.pop("name"), "Order", ctx=ctx, **kwargs)

    with pytest.raises(ValidationFailed):
        foundry.objects.create_set(
            "Bad Property",
            "Order",
            set_type="dynamic",
            filter_ast={"property": "missing", "op": "eq", "value": "PENDING"},
            ctx=ctx,
        )
    with pytest.raises(ValidationFailed):
        foundry.objects.create_set(
            "Missing Static Object",
            "Order",
            set_type="static",
            object_ids=["O-404"],
            ctx=ctx,
        )


def test_object_set_definition_normalizer_is_directly_executable() -> None:
    """The pure ObjectSet rule remains testable without starting the full runtime."""
    normalized = set_validation.normalize_object_set_definition(
        "Pending orders",
        set_type="dynamic",
        definition=None,
        object_ids=None,
        filter_ast={"property": "status", "op": "eq", "value": "PENDING"},
        visibility="public",
        access_scope=None,
        lifecycle=None,
        ttl_seconds=None,
    )
    assert normalized["definition"] == {"filter": {"property": "status", "op": "eq", "value": "PENDING"}}
    assert normalized["visibility"] == "public"


def test_search_around_object_set_changes_object_type_across_a_link(foundry: FoundryLite) -> None:
    """Traversal is set-to-set: filter Orders, hop OrderCustomer, land on Customers.

    This is the capability a filter predicate cannot express — the answer is of a different type
    than the question, which is exactly why Palantir models it as `searchAround` rather than as
    another operator inside the filter language.
    """
    ctx = prepare_indexed_demo(foundry)

    customers = foundry.objects.create_set(
        "Customers Behind Pending Orders",
        "Customer",
        set_type="search_around",
        definition={
            "searchAround": {
                "from": {
                    "objectType": "Order",
                    "filter": {"property": "status", "op": "eq", "value": "PENDING"},
                },
                "hops": [{"link": "OrderCustomer"}],
            }
        },
        ctx=ctx,
    )

    assert customers["objectType"] == "Customer"
    assert customers["setType"] == "search_around"
    pending_orders = foundry.objects.query(
        "Order", filter_ast={"property": "status", "op": "eq", "value": "PENDING"}, ctx=ctx
    )["items"]
    expected = {
        link["to"]["objectId"]
        for order in pending_orders
        for link in foundry.objects.links("Order", order["objectId"], "OrderCustomer", ctx=ctx)
    }
    assert set(customers["objectIds"]) == expected
    assert expected


def test_search_around_object_set_declared_type_must_match_where_the_chain_lands(
    foundry: FoundryLite,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    with pytest.raises(ValidationFailed) as excinfo:
        foundry.objects.create_set(
            "Mistyped Traversal",
            "Order",
            set_type="search_around",
            definition={
                "searchAround": {
                    "from": {"objectType": "Order", "filter": {}},
                    "hops": [{"link": "OrderCustomer"}],
                }
            },
            ctx=ctx,
        )
    assert excinfo.value.details["resolved"] == "Customer"


def test_search_around_object_set_refuses_a_chain_longer_than_palantirs_limit(
    foundry: FoundryLite,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    with pytest.raises(ValidationFailed) as excinfo:
        foundry.objects.create_set(
            "Too Many Hops",
            "Customer",
            set_type="search_around",
            definition={
                "searchAround": {
                    "from": {"objectType": "Order", "filter": {}},
                    "hops": [{"link": "OrderCustomer"}] * 4,
                }
            },
            ctx=ctx,
        )
    assert excinfo.value.details == {"hops": 4, "maxHops": 3}


def test_search_around_object_set_follows_the_same_link_from_its_reverse_endpoint(
    foundry: FoundryLite,
) -> None:
    ctx = prepare_indexed_demo(foundry)
    customer = foundry.objects.query("Customer", ctx=ctx, limit=1)["items"][0]
    customer_id = customer["properties"]["customerId"]
    orders = foundry.objects.create_set(
        "Orders For One Customer",
        "Order",
        set_type="search_around",
        definition={
            "searchAround": {
                "from": {
                    "objectType": "Customer",
                    "filter": {"property": "customerId", "op": "eq", "value": customer_id},
                },
                "hops": [{"link": "OrderCustomer"}],
            }
        },
        ctx=ctx,
    )

    expected = {
        item["objectId"]
        for item in foundry.objects.query("Order", ctx=ctx, limit=200)["items"]
        if item["properties"]["customerId"] == customer_id
    }
    assert orders["objectType"] == "Order"
    assert set(orders["objectIds"]) == expected


def test_search_around_stops_before_reading_more_links_after_the_result_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The local cap must bound the work, not merely reject after buffering every candidate."""
    monkeypatch.setattr(set_members, "SEARCH_AROUND_RESULT_LIMIT", 2)

    class LinkReader:
        calls: list[str] = []

        def active_links_from(self, **kwargs: object) -> list[dict[str, object]]:
            object_id = str(kwargs["from_object_id"])
            self.calls.append(object_id)
            return [{"to_object_id": f"target-{index}"} for index in range(3)]

        def active_links_to(self, **kwargs: object) -> list[dict[str, object]]:
            raise AssertionError("the forward link must not use the reverse read")

    reader = LinkReader()
    with pytest.raises(ValidationFailed) as excinfo:
        resolve_search_around_object_ids(
            cast(Any, reader),
            transaction=cast(Any, object()),
            tenant_id="tenant-a",
            from_object_type_api_name="Order",
            from_object_ids=["first", "second"],
            hops=[{"link": "OrderCustomer"}],
            link_types_by_api={"OrderCustomer": {"from_api_name": "Order", "to_api_name": "Customer"}},
        )

    assert excinfo.value.details == {"linkType": "OrderCustomer", "limit": 2}
    assert reader.calls == ["first"]


@pytest.mark.parametrize(
    "definition",
    [
        None,
        {},
        {"from": {}},
        {"from": {"objectType": "Order", "filter": ["invalid"]}, "hops": [{"link": "OrderCustomer"}]},
        {"from": {"objectType": "Order"}, "hops": []},
        {"from": {"objectType": "Order"}, "hops": [{"link": "OrderCustomer"}] * 4},
        {"from": {"objectType": "Order"}, "hops": ["not-an-object"]},
    ],
)
def test_search_around_definition_rejects_malformed_source_and_hops(definition: object) -> None:
    with pytest.raises(ValidationFailed):
        set_search_around.search_around_parts(definition)


def test_search_around_definition_parses_a_bounded_source_and_hop() -> None:
    source_type, filter_ast, hops = set_search_around.search_around_parts(
        {
            "from": {"objectType": "Order", "filter": {"property": "status", "op": "eq", "value": "PENDING"}},
            "hops": [{"link": "OrderCustomer"}],
        }
    )

    assert source_type == "Order"
    assert filter_ast == {"property": "status", "op": "eq", "value": "PENDING"}
    assert hops == [{"link": "OrderCustomer"}]
