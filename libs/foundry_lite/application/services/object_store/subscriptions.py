"""Application service helpers for subscriptions workflows."""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace
from typing import Protocol

from foundry_lite.application.ports import (
    ObjectQueryItem,
    ObjectQueryResult,
    OsdkResourceOperation,
    OsdkResourceType,
    RuntimeJsonObject,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext


class _ObjectQueryBoundary(Protocol):
    def query_objects(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        order_by: Sequence[Mapping[str, str]] | None = None,
        limit: int = 50,
        cursor: str | None = None,
        search_text: str | None = None,
        semantic_text: str | None = None,
    ) -> ObjectQueryResult: ...


class _OsdkScopeBoundary(Protocol):
    def require_resource_scope(
        self,
        ctx: RequestContext,
        *,
        resource_type: OsdkResourceType,
        resource_api_name: str,
        operation: OsdkResourceOperation,
    ) -> None: ...


class ObjectSubscriptionService(CoreService):
    required_dependencies = ("engine", "policy", "object_read_repository")
    required_collaborators = ("object_query_service", "osdk_application_service")
    object_query_service: _ObjectQueryBoundary
    osdk_application_service: _OsdkScopeBoundary

    def subscription_events(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        order_by: Sequence[Mapping[str, str]] | None = None,
        properties: Sequence[str] | None = None,
        page_size: int = 50,
        last_seen_object_change_sequence: int | None = None,
        max_events: int | None = None,
        poll_interval_seconds: float = 1.0,
    ) -> Iterator[RuntimeJsonObject]:
        """Stream snapshot/diff events for one object type.

        Every snapshot and diff page is materialized through
        ``query_objects``, so row policies (and masking) apply to streamed
        events with no separate enforcement here: a restricted subscriber's
        snapshot holds only visible rows and hidden rows never diff in.
        """
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "object:read")
        self.osdk_application_service.require_resource_scope(
            ctx,
            resource_type="object",
            resource_api_name=object_type_api_name,
            operation="subscribe",
        )
        yield from self._stream_events(
            object_type_api_name,
            ctx,
            filter_ast,
            order_by,
            properties,
            page_size,
            last_seen_object_change_sequence,
            max_events,
            poll_interval_seconds,
        )

    def _stream_events(
        self,
        object_type_api_name: str,
        ctx: RequestContext,
        filter_ast: Mapping[str, object] | None,
        order_by: Sequence[Mapping[str, str]] | None,
        properties: Sequence[str] | None,
        page_size: int,
        last_seen_object_change_sequence: int | None,
        max_events: int | None,
        poll_interval_seconds: float,
    ) -> Iterator[RuntimeJsonObject]:
        safe_ctx = _without_app_scope(ctx)
        previous = self._matching_items(object_type_api_name, safe_ctx, filter_ast, order_by, properties, page_size)
        watermark = self._watermark(object_type_api_name, safe_ctx)
        yielded = 0
        yield _snapshot_event(previous, watermark)
        yielded += 1
        if last_seen_object_change_sequence is not None and last_seen_object_change_sequence < watermark:
            yield _out_of_date_event(last_seen_object_change_sequence, watermark)
            yielded += 1
        yield from self._change_loop(
            object_type_api_name,
            safe_ctx,
            filter_ast,
            order_by,
            properties,
            page_size,
            watermark,
            previous,
            yielded,
            max_events,
            poll_interval_seconds,
        )

    def _change_loop(
        self,
        object_type_api_name: str,
        ctx: RequestContext,
        filter_ast: Mapping[str, object] | None,
        order_by: Sequence[Mapping[str, str]] | None,
        properties: Sequence[str] | None,
        page_size: int,
        watermark: int,
        previous: list[ObjectQueryItem],
        yielded: int,
        max_events: int | None,
        poll_interval_seconds: float,
    ) -> Iterator[RuntimeJsonObject]:
        while max_events is None or yielded < max_events:
            latest = self._watermark(object_type_api_name, ctx)
            if latest <= watermark:
                yielded += 1
                yield _heartbeat_event(watermark)
                _sleep(poll_interval_seconds)
                continue
            current = self._matching_items(object_type_api_name, ctx, filter_ast, order_by, properties, page_size)
            for event in _diff_events(previous, current, latest):
                yielded += 1
                yield event
                if max_events is not None and yielded >= max_events:
                    return
            previous, watermark = current, latest

    def _matching_items(
        self,
        object_type_api_name: str,
        ctx: RequestContext,
        filter_ast: Mapping[str, object] | None,
        order_by: Sequence[Mapping[str, str]] | None,
        properties: Sequence[str] | None,
        page_size: int,
    ) -> list[ObjectQueryItem]:
        result = self.object_query_service.query_objects(
            object_type_api_name,
            ctx=ctx,
            filter_ast=filter_ast,
            order_by=order_by,
            limit=page_size,
        )
        return _project_items(result["items"], properties)

    def _watermark(self, object_type_api_name: str, ctx: RequestContext) -> int:
        with self.engine.begin() as conn:
            return self.object_read_repository.latest_object_change_sequence(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                object_type_api_name=object_type_api_name,
                include_deleted=True,
            )


def _without_app_scope(ctx: RequestContext) -> RequestContext:
    return replace(ctx, application_id=None, client_id=None, token_scopes=())


def _snapshot_event(items: Sequence[ObjectQueryItem], watermark: int) -> RuntimeJsonObject:
    return {"event": "snapshot", "items": list(items), "watermark": watermark}


def _out_of_date_event(last_seen: int, watermark: int) -> RuntimeJsonObject:
    return {"event": "out_of_date", "lastSeenObjectChangeSequence": last_seen, "watermark": watermark}


def _heartbeat_event(watermark: int) -> RuntimeJsonObject:
    return {"event": "heartbeat", "watermark": watermark}


def _diff_events(
    previous: Sequence[ObjectQueryItem], current: Sequence[ObjectQueryItem], watermark: int
) -> list[RuntimeJsonObject]:
    previous_by_id = {item["objectId"]: item for item in previous}
    current_by_id = {item["objectId"]: item for item in current}
    events = [_changed_event(item, watermark) for item in current if previous_by_id.get(item["objectId"]) != item]
    events.extend(_removed_event(item, watermark) for key, item in previous_by_id.items() if key not in current_by_id)
    return events


def _changed_event(item: ObjectQueryItem, watermark: int) -> RuntimeJsonObject:
    return {"event": "object_changed", "state": "ADDED_OR_UPDATED", "object": item, "watermark": watermark}


def _removed_event(item: ObjectQueryItem, watermark: int) -> RuntimeJsonObject:
    return {
        "event": "object_changed",
        "state": "DELETED",
        "object": {"objectType": item["objectType"], "objectId": item["objectId"]},
        "watermark": watermark,
    }


def _project_items(items: Sequence[ObjectQueryItem], properties: Sequence[str] | None) -> list[ObjectQueryItem]:
    if properties is None:
        return list(items)
    allowed = set(properties)
    return [_project_item(item, allowed) for item in items]


def _project_item(item: ObjectQueryItem, properties: set[str]) -> ObjectQueryItem:
    projected: ObjectQueryItem = {
        "objectType": item["objectType"],
        "objectId": item["objectId"],
        "objectVersion": item["objectVersion"],
        "properties": {key: value for key, value in item["properties"].items() if key in properties},
    }
    if "searchProjectionVersion" in item:
        projected["searchProjectionVersion"] = item["searchProjectionVersion"]
    if "searchProjectionStale" in item:
        projected["searchProjectionStale"] = item["searchProjectionStale"]
    return projected


def _sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)
