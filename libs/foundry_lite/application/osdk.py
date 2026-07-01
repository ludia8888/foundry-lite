"""Application-layer models and helpers for osdk."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import TypedDict, cast

from foundry_lite.application.action_types import ActionApplyResponse, ActionValidationResponse
from foundry_lite.application.osdk_aggregation import (
    OsdkAggregateResult,
    aggregate_items,
    aggregate_query_order,
    build_aggregate_plan,
)
from foundry_lite.application.osdk_protocols import OsdkHost
from foundry_lite.application.ports import ObjectLinkPayload, ObjectPayload, ObjectQueryItem, ObjectQueryResult
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

_UNSET = object()


class OsdkObjectPage(TypedDict):
    items: tuple[OsdkObject, ...]
    nextPageToken: str | None


@dataclass(frozen=True)
class OsdkObjectType:
    api_name: str
    property_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class OsdkActionType:
    api_name: str
    target_object_type: str
    parameter_names: tuple[str, ...] = ()
    required_parameters: tuple[str, ...] = ()


@dataclass(frozen=True)
class OsdkLinkBinding:
    alias: str
    link_api_name: str
    target_object_type: OsdkObjectType


@dataclass(frozen=True)
class OsdkActionBinding:
    alias: str
    action_type: OsdkActionType


@dataclass(frozen=True)
class OsdkObject:
    _client: OsdkHost
    _ctx: RequestContext | None
    object_type: str
    object_id: str
    object_version: int
    properties: Mapping[str, object]

    @property
    def links(self) -> OsdkLinks:
        return OsdkLinks(self)

    @property
    def actions(self) -> OsdkActions:
        return OsdkActions(self)


@dataclass(frozen=True)
class OsdkObjectSet:
    _client: OsdkHost
    object_type: OsdkObjectType
    _ctx: RequestContext | None = None
    _filter_ast: Mapping[str, object] | None = None
    _order_by: tuple[Mapping[str, str], ...] = ()

    def where(self, filter_ast: Mapping[str, object] | None = None, **property_filters: object) -> OsdkObjectSet:
        compiled = _compile_where(self.object_type, filter_ast, property_filters)
        return self._replace(filter_ast=_combine_filters(self._filter_ast, compiled))

    def order_by(self, order_by: Mapping[str, str] | None = None, **properties: str) -> OsdkObjectSet:
        return self._replace(order_by=tuple(_compile_order_by(self.object_type, order_by, properties)))

    def aggregate(self, request: Mapping[str, object]) -> OsdkAggregateResult:
        plan = build_aggregate_plan(self.object_type.api_name, self.object_type.property_names, request)
        order_by = aggregate_query_order(self._order_by, plan.group_by)
        return aggregate_items(plan, self._replace(order_by=order_by).iterate(page_size=500))

    def fetch_one(self, primary_key: str, *, include_explain: bool = False) -> OsdkObject:
        payload = self._client.objects.get(
            self.object_type.api_name,
            primary_key,
            ctx=self._ctx,
            include_explain=include_explain,
        )
        return _object_from_payload(self._client, self._ctx, payload)

    def fetch_page(self, *, page_size: int = 50, page_token: str | None = None) -> OsdkObjectPage:
        result = self._client.objects.query(
            self.object_type.api_name,
            ctx=self._ctx,
            filter_ast=self._filter_ast,
            order_by=self._order_by,
            limit=page_size,
            cursor=page_token,
        )
        return _page_from_query(self._client, self._ctx, result)

    def iterate(self, *, page_size: int = 50) -> Iterator[OsdkObject]:
        page_token: str | None = None
        while True:
            page = self.fetch_page(page_size=page_size, page_token=page_token)
            yield from page["items"]
            page_token = page["nextPageToken"]
            if page_token is None:
                return

    async def async_iter(self, *, page_size: int = 50) -> AsyncIterator[OsdkObject]:
        for item in self.iterate(page_size=page_size):
            yield item

    def _replace(
        self,
        *,
        filter_ast: Mapping[str, object] | None | object = _UNSET,
        order_by: tuple[Mapping[str, str], ...] | object = _UNSET,
    ) -> OsdkObjectSet:
        next_filter = self._filter_ast if filter_ast is _UNSET else cast(Mapping[str, object] | None, filter_ast)
        next_order = self._order_by if order_by is _UNSET else cast(tuple[Mapping[str, str], ...], order_by)
        return OsdkObjectSet(self._client, self.object_type, self._ctx, next_filter, next_order)


@dataclass(frozen=True)
class OsdkLinkSet:
    source: OsdkObject
    binding: OsdkLinkBinding

    def fetch_page(self, *, page_size: int = 50, page_token: str | None = None) -> OsdkObjectPage:
        if page_token is not None:
            return {"items": (), "nextPageToken": None}
        links = self.source._client.objects.links(
            self.source.object_type,
            self.source.object_id,
            self.binding.link_api_name,
            ctx=self.source._ctx,
        )
        items = tuple(self._target_object(link) for link in links[:page_size] if not _target_missing(link))
        return {"items": items, "nextPageToken": None}

    def _target_object(self, link: ObjectLinkPayload) -> OsdkObject:
        target = link["to"]
        payload = self.source._client.objects.get(
            target["objectType"],
            target["objectId"],
            ctx=self.source._ctx,
        )
        return _object_from_payload(self.source._client, self.source._ctx, payload)


@dataclass(frozen=True)
class OsdkActionInvoker:
    _client: OsdkHost
    action_type: OsdkActionType
    _ctx: RequestContext | None = None

    def apply_action(
        self,
        params: Mapping[str, object],
        *,
        idempotency_key: str,
        target: OsdkObject | None = None,
        object_id: str | None = None,
        expected_object_version: int | None = None,
        ctx: RequestContext | None = None,
    ) -> ActionApplyResponse:
        _validate_action_params(self.action_type, params)
        target_ref = _action_target_ref(self.action_type, target, object_id, expected_object_version)
        if not idempotency_key:
            raise ValidationFailed("Python OSDK action requires idempotency_key")
        return self._client.actions.apply(
            self.action_type.api_name,
            object_type=self.action_type.target_object_type,
            object_id=target_ref.object_id,
            expected_object_version=target_ref.object_version,
            params=params,
            idempotency_key=idempotency_key,
            ctx=ctx or self._ctx,
        )

    def validate_action(
        self,
        params: Mapping[str, object],
        *,
        target: OsdkObject | None = None,
        object_id: str | None = None,
        expected_object_version: int | None = None,
        ctx: RequestContext | None = None,
    ) -> ActionValidationResponse:
        _validate_action_params(self.action_type, params)
        target_ref = _action_target_ref(self.action_type, target, object_id, expected_object_version)
        return self._client.actions.validate(
            self.action_type.api_name,
            object_type=self.action_type.target_object_type,
            object_id=target_ref.object_id,
            expected_object_version=target_ref.object_version,
            params=params,
            ctx=ctx or self._ctx,
        )


@dataclass(frozen=True)
class OsdkBoundAction:
    source: OsdkObject
    action_type: OsdkActionType

    def apply_action(self, params: Mapping[str, object], *, idempotency_key: str) -> ActionApplyResponse:
        invoker = OsdkActionInvoker(self.source._client, self.action_type, self.source._ctx)
        return invoker.apply_action(params, idempotency_key=idempotency_key, target=self.source)

    def validate_action(self, params: Mapping[str, object]) -> ActionValidationResponse:
        invoker = OsdkActionInvoker(self.source._client, self.action_type, self.source._ctx)
        return invoker.validate_action(params, target=self.source)


@dataclass(frozen=True)
class OsdkTargetRef:
    object_id: str
    object_version: int


class OsdkLinks:
    def __init__(self, source: OsdkObject) -> None:
        self._source = source

    def __getattr__(self, alias: str) -> OsdkLinkSet:
        for binding in _link_bindings(self._source.object_type):
            if binding.alias == alias:
                return OsdkLinkSet(self._source, binding)
        raise AttributeError(alias)


class OsdkActions:
    def __init__(self, source: OsdkObject) -> None:
        self._source = source

    def __getattr__(self, alias: str) -> OsdkBoundAction:
        for binding in _action_bindings(self._source.object_type):
            if binding.alias == alias:
                return OsdkBoundAction(self._source, binding.action_type)
        raise AttributeError(alias)


def object_type(api_name: str, *, property_names: Sequence[str] = ()) -> OsdkObjectType:
    return OsdkObjectType(api_name=api_name, property_names=tuple(property_names))


def action_type(
    api_name: str,
    *,
    target_object_type: str,
    parameter_names: Sequence[str] = (),
    required_parameters: Sequence[str] = (),
) -> OsdkActionType:
    return OsdkActionType(api_name, target_object_type, tuple(parameter_names), tuple(required_parameters))


def osdk_resource(
    client: OsdkHost,
    resource: OsdkObjectType | OsdkActionType,
    *,
    ctx: RequestContext | None = None,
) -> OsdkObjectSet | OsdkActionInvoker:
    if isinstance(resource, OsdkObjectType):
        return OsdkObjectSet(client, resource, ctx)
    if isinstance(resource, OsdkActionType):
        return OsdkActionInvoker(client, resource, ctx)
    raise ValidationFailed("unknown Python OSDK resource", details={"resource": repr(resource)})


def _compile_where(
    object_type_resource: OsdkObjectType,
    filter_ast: Mapping[str, object] | None,
    property_filters: Mapping[str, object],
) -> Mapping[str, object] | None:
    compiled = _compile_property_filters(object_type_resource, property_filters)
    if filter_ast is None:
        return compiled
    _validate_filter_property_names(object_type_resource, filter_ast)
    return _combine_filters(filter_ast, compiled)


def _compile_property_filters(
    object_type_resource: OsdkObjectType,
    property_filters: Mapping[str, object],
) -> Mapping[str, object] | None:
    filters = [_property_filter_ast(object_type_resource, prop, value) for prop, value in property_filters.items()]
    if not filters:
        return None
    if len(filters) == 1:
        return filters[0]
    return {"and": filters}


def _property_filter_ast(object_type_resource: OsdkObjectType, prop: str, value: object) -> Mapping[str, object]:
    _validate_osdk_property(object_type_resource, prop, source="where")
    if not isinstance(value, Mapping):
        return {"property": prop, "op": "eq", "value": value}
    if len(value) != 1:
        raise ValidationFailed("Python OSDK property filter must contain exactly one operator")
    operator, operand = next(iter(value.items()))
    return {"property": prop, "op": _filter_operator(str(operator)), "value": operand}


def _filter_operator(operator: str) -> str:
    normalized = operator.removeprefix("$")
    aliases = {"eq": "eq", "in": "in", "gte": "gte", "lte": "lte", "contains": "contains"}
    if normalized in aliases:
        return aliases[normalized]
    raise ValidationFailed("unsupported Python OSDK filter operator", details={"operator": operator})


def _compile_order_by(
    object_type_resource: OsdkObjectType,
    order_by: Mapping[str, str] | None,
    properties: Mapping[str, str],
) -> list[Mapping[str, str]]:
    order_input = dict(order_by or {})
    order_input.update(properties)
    return [_order_by_item(object_type_resource, prop, direction) for prop, direction in order_input.items()]


def _order_by_item(object_type_resource: OsdkObjectType, prop: str, direction: str) -> Mapping[str, str]:
    _validate_osdk_property(object_type_resource, prop, source="order_by")
    if direction not in {"asc", "desc"}:
        raise ValidationFailed("Python OSDK order_by direction must be asc or desc", details={"direction": direction})
    return {"property": prop, "direction": direction}


def _validate_filter_property_names(object_type_resource: OsdkObjectType, filter_ast: Mapping[str, object]) -> None:
    if "and" in filter_ast or "or" in filter_ast:
        for item in _logical_filter_items(filter_ast):
            _validate_filter_property_names(object_type_resource, item)
        return
    prop = filter_ast.get("property")
    if isinstance(prop, str):
        _validate_osdk_property(object_type_resource, prop, source="where")


def _logical_filter_items(filter_ast: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    value = filter_ast.get("and", filter_ast.get("or"))
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ValidationFailed("Python OSDK logical filter must be a list")
    return tuple(cast(Mapping[str, object], item) for item in value)


def _validate_osdk_property(object_type_resource: OsdkObjectType, prop: str, *, source: str) -> None:
    if object_type_resource.property_names and prop not in object_type_resource.property_names:
        raise ValidationFailed(
            "Python OSDK references unknown property",
            details={"objectType": object_type_resource.api_name, "property": prop, "source": source},
        )


def _combine_filters(
    existing: Mapping[str, object] | None,
    compiled: Mapping[str, object] | None,
) -> Mapping[str, object] | None:
    if existing is None:
        return compiled
    if compiled is None:
        return existing
    return {"and": [existing, compiled]}


def _page_from_query(client: OsdkHost, ctx: RequestContext | None, result: ObjectQueryResult) -> OsdkObjectPage:
    return {
        "items": tuple(_object_from_query_item(client, ctx, item) for item in result["items"]),
        "nextPageToken": result["nextCursor"],
    }


def _object_from_query_item(client: OsdkHost, ctx: RequestContext | None, item: ObjectQueryItem) -> OsdkObject:
    return OsdkObject(client, ctx, item["objectType"], item["objectId"], item["objectVersion"], item["properties"])


def _object_from_payload(client: OsdkHost, ctx: RequestContext | None, payload: ObjectPayload) -> OsdkObject:
    return OsdkObject(
        client,
        ctx,
        payload["objectType"],
        payload["objectId"],
        payload["objectVersion"],
        payload["properties"],
    )


def _target_missing(link: ObjectLinkPayload) -> bool:
    return bool(link["to"].get("targetMissing"))


def _validate_action_params(action_type_resource: OsdkActionType, params: Mapping[str, object]) -> None:
    missing = sorted(set(action_type_resource.required_parameters) - set(params))
    if missing:
        raise ValidationFailed("Python OSDK action params missing required parameter", details={"missing": missing})
    unknown = sorted(set(params) - set(action_type_resource.parameter_names))
    if action_type_resource.parameter_names and unknown:
        raise ValidationFailed("Python OSDK action params include unknown parameter", details={"unknown": unknown})


def _action_target_ref(
    action_type_resource: OsdkActionType,
    target: OsdkObject | None,
    object_id: str | None,
    expected_object_version: int | None,
) -> OsdkTargetRef:
    if target is not None:
        _validate_target_object_type(action_type_resource, target.object_type)
        return OsdkTargetRef(target.object_id, target.object_version)
    if object_id is None or expected_object_version is None:
        raise ValidationFailed("Python OSDK action target requires object_id and expected_object_version")
    return OsdkTargetRef(object_id, expected_object_version)


def _validate_target_object_type(action_type_resource: OsdkActionType, object_type_name: str) -> None:
    if object_type_name != action_type_resource.target_object_type:
        raise ValidationFailed(
            "Python OSDK action target object type mismatch",
            details={"expected": action_type_resource.target_object_type, "actual": object_type_name},
        )


Order = object_type(
    "Order",
    property_names=(
        "orderId",
        "customerId",
        "status",
        "amount",
        "margin",
        "riskScore",
        "operatorNote",
    ),
)
Customer = object_type(
    "Customer",
    property_names=("customerId", "name", "segment", "region", "riskScore", "approvedOrderCount"),
)
ApproveOrder = action_type(
    "ApproveOrder",
    target_object_type="Order",
    parameter_names=("reason",),
    required_parameters=("reason",),
)
OrderCustomer = OsdkLinkBinding("customer", "OrderCustomer", Customer)
CustomerOrder = OsdkLinkBinding("order", "OrderCustomer", Order)
_ORDER_ACTIONS = (OsdkActionBinding("approve_order", ApproveOrder), OsdkActionBinding("ApproveOrder", ApproveOrder))
_OBJECT_LINKS = {"Order": (OrderCustomer,), "Customer": (CustomerOrder,)}
_OBJECT_ACTIONS = {"Order": _ORDER_ACTIONS}


def _link_bindings(object_type_name: str) -> tuple[OsdkLinkBinding, ...]:
    return _OBJECT_LINKS.get(object_type_name, ())


def _action_bindings(object_type_name: str) -> tuple[OsdkActionBinding, ...]:
    return _OBJECT_ACTIONS.get(object_type_name, ())
