from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MethodType
from typing import cast

from foundry_lite.application.ports import ObjectQueryItem
from foundry_lite.application.services.object_store.subscriptions import (
    ObjectSubscriptionService,
    _project_items,
    _sleep,
    _without_app_scope,
)
from foundry_lite.domain.context import RequestContext


def test_object_subscription_stream_helper_emits_snapshot_out_of_date_heartbeat_and_diffs() -> None:
    service = cast(ObjectSubscriptionService, object.__new__(ObjectSubscriptionService))
    watermark_values = iter([5, 5, 6])
    item_pages = iter([[_item("O-2", status="REVIEW")], [_item("O-3", status="PENDING")]])

    def watermark(_self: ObjectSubscriptionService, object_type: str, ctx: RequestContext) -> int:
        del object_type, ctx
        return next(watermark_values)

    def matching_items(
        _self: ObjectSubscriptionService,
        object_type: str,
        ctx: RequestContext,
        filter_ast: Mapping[str, object] | None,
        order_by: Sequence[Mapping[str, str]] | None,
        properties: Sequence[str] | None,
        page_size: int,
    ) -> list[ObjectQueryItem]:
        del _self, object_type, ctx, filter_ast, order_by, properties, page_size
        return next(item_pages)

    service._watermark = MethodType(watermark, service)  # type: ignore[method-assign]
    service._matching_items = MethodType(matching_items, service)  # type: ignore[method-assign]
    events = list(
        service._stream_events(
            "Order",
            RequestContext(application_id="app-1", client_id="client-1", token_scopes=("scope",)),
            None,
            None,
            None,
            50,
            4,
            5,
            0,
        )
    )

    assert [event["event"] for event in events] == [
        "snapshot",
        "out_of_date",
        "heartbeat",
        "object_changed",
        "object_changed",
    ]
    assert events[0]["items"] == [_item("O-2", status="REVIEW")]
    assert events[1]["lastSeenObjectChangeSequence"] == 4
    assert events[2]["watermark"] == 5
    assert events[3]["state"] == "ADDED_OR_UPDATED"
    assert events[4]["state"] == "DELETED"


def test_object_subscription_projection_keeps_search_metadata_and_strips_app_scope(
    monkeypatch,
) -> None:
    item: ObjectQueryItem = {
        "objectType": "Order",
        "objectId": "O-1",
        "objectVersion": 3,
        "properties": {"status": "PENDING", "margin": 42},
        "searchProjectionVersion": 7,
        "searchProjectionStale": True,
    }
    scoped = RequestContext(application_id="app-1", client_id="client-1", token_scopes=("scope",))
    slept: list[float] = []

    monkeypatch.setattr("foundry_lite.application.services.object_store.subscriptions.time.sleep", slept.append)

    unscoped = _without_app_scope(scoped)
    projected = _project_items([item], ["status"])
    _sleep(0)
    _sleep(0.25)

    assert unscoped.application_id is None
    assert unscoped.client_id is None
    assert unscoped.token_scopes == ()
    assert projected == [
        {
            "objectType": "Order",
            "objectId": "O-1",
            "objectVersion": 3,
            "properties": {"status": "PENDING"},
            "searchProjectionVersion": 7,
            "searchProjectionStale": True,
        }
    ]
    assert slept == [0.25]


def _item(object_id: str, *, status: str) -> ObjectQueryItem:
    return {
        "objectType": "Order",
        "objectId": object_id,
        "objectVersion": 1,
        "properties": {"status": status},
    }
