"""Minimal Python Ontology SDK surface for the networkless function runner.

This module is trusted runner code, not user code. It only builds immutable query plans and
exchanges bounded JSON messages with the host-side query bridge. No credential or database
address crosses into the sandbox.
"""

from __future__ import annotations

import json
import sys
import time
import types
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

_bridge: QueryBridge | None = None


@dataclass(frozen=True)
class QueryBridge:
    directory: Path
    nonce: str
    timeout_seconds: float

    def call(self, request: Mapping[str, object]) -> Mapping[str, object]:
        request_id = uuid4().hex
        request_path = self.directory / f"request-{self.nonce}-{request_id}.json"
        response_path = self.directory / f"response-{self.nonce}-{request_id}.json"
        temporary_path = request_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(dict(request), sort_keys=True),
            encoding="utf-8",
        )
        temporary_path.replace(request_path)
        deadline = time.monotonic() + self.timeout_seconds
        while not response_path.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("Ontology ObjectSet query timed out")
            time.sleep(0.005)
        payload = json.loads(response_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping) or payload.get("status") != "succeeded":
            raise RuntimeError("Ontology ObjectSet query failed")
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise RuntimeError("Ontology ObjectSet query returned an invalid result")
        return result


@dataclass(frozen=True)
class PropertyExpression:
    name: str

    def __eq__(self, value: object) -> Mapping[str, object]:  # type: ignore[override]
        return _property_filter(self.name, "$eq", value)

    def __gt__(self, value: object) -> Mapping[str, object]:
        return _property_filter(self.name, "$gt", value)

    def __ge__(self, value: object) -> Mapping[str, object]:
        return _property_filter(self.name, "$gte", value)

    def __lt__(self, value: object) -> Mapping[str, object]:
        return _property_filter(self.name, "$lt", value)

    def __le__(self, value: object) -> Mapping[str, object]:
        return _property_filter(self.name, "$lte", value)


class ObjectTypeMeta(type):
    def __getattr__(cls, name: str) -> object:
        if name == "object_type":
            return cls
        return PropertyExpression(name)


class OntologyObjectType(metaclass=ObjectTypeMeta):
    pass


class OntologyObject(Mapping[str, object]):
    def __init__(self, payload: Mapping[str, object]) -> None:
        self._payload = dict(payload)
        properties = payload.get("properties")
        self._properties = dict(properties) if isinstance(properties, Mapping) else dict(payload)

    def __getattr__(self, name: str) -> object:
        if name in self._properties:
            return self._properties[name]
        if name in self._payload:
            return self._payload[name]
        raise AttributeError(name)

    def __getitem__(self, key: str) -> object:
        if key in self._properties:
            return self._properties[key]
        return self._payload[key]

    def __iter__(self) -> Iterator[str]:
        return iter({**self._payload, **self._properties})

    def __len__(self) -> int:
        return len({**self._payload, **self._properties})

    def to_payload(self) -> dict[str, object]:
        return dict(self._payload)


@dataclass(frozen=True)
class ObjectPage:
    items: tuple[OntologyObject, ...]
    next_page_token: str | None

    @property
    def nextPageToken(self) -> str | None:
        return self.next_page_token


@dataclass(frozen=True)
class ObjectSet:
    object_type: str
    filter_ast: Mapping[str, object] | None = None
    order_by: tuple[Mapping[str, str], ...] = ()

    def where(self, filter_ast: Mapping[str, object] | None = None, **filters: object) -> ObjectSet:
        compiled = _where_filter(filter_ast, filters)
        return replace(self, filter_ast=_and_filter(self.filter_ast, compiled))

    def order_by_fields(self, **properties: str) -> ObjectSet:
        order = tuple({"property": key, "direction": value} for key, value in properties.items())
        return replace(self, order_by=order)

    def fetch_page(self, *, page_size: int = 500, page_token: str | None = None) -> ObjectPage:
        result = _required_bridge().call(self._request("fetchPage", pageSize=page_size, pageToken=page_token))
        rows = result.get("items")
        if not isinstance(rows, Sequence) or isinstance(rows, str | bytes):
            raise RuntimeError("Ontology ObjectSet page did not contain items")
        items = tuple(OntologyObject(item) for item in rows if isinstance(item, Mapping))
        token = result.get("nextCursor")
        return ObjectPage(items, token if isinstance(token, str) else None)

    def all(self) -> list[OntologyObject]:
        objects: list[OntologyObject] = []
        token: str | None = None
        while True:
            page = self.fetch_page(page_token=token)
            objects.extend(page.items)
            token = page.next_page_token
            if token is None:
                return objects

    def aggregate(
        self,
        *,
        group_by: Sequence[str] = (),
        select: Sequence[Mapping[str, object]],
    ) -> Mapping[str, object]:
        return _required_bridge().call(
            self._request("aggregate", groupBy=list(group_by), select=[dict(item) for item in select])
        )

    def to_descriptor(self) -> dict[str, object]:
        return {
            "$foundryObjectSet": {
                "objectType": self.object_type,
                "filter": dict(self.filter_ast) if self.filter_ast is not None else None,
                "orderBy": [dict(item) for item in self.order_by],
            }
        }

    def _request(self, operation: str, **fields: object) -> dict[str, object]:
        return {
            "operation": operation,
            "objectType": self.object_type,
            "filter": dict(self.filter_ast) if self.filter_ast is not None else None,
            "orderBy": [dict(item) for item in self.order_by],
            **fields,
        }


class _ObjectSearch:
    def __getattr__(self, object_type: str) -> ObjectSet:
        return ObjectSet(object_type)


class _OntologyClient:
    def __init__(self) -> None:
        self.objects = _ObjectSearch()


class FoundryClient:
    def __init__(self, *_: object, **__: object) -> None:
        self.ontology = _OntologyClient()


def configure(manifest: Mapping[str, object]) -> None:
    global _bridge  # noqa: PLW0603 - runner-scoped configuration is immutable after startup
    config = manifest.get("queryBridge")
    if not isinstance(config, Mapping):
        _bridge = None
        return
    directory = config.get("directory")
    nonce = config.get("nonce")
    timeout = config.get("timeoutSeconds")
    if not isinstance(directory, str) or not isinstance(nonce, str) or not isinstance(timeout, int | float):
        raise ValueError("queryBridge manifest is invalid")
    _bridge = QueryBridge(Path(directory), nonce, float(timeout))


def wrap_inputs(inputs: Mapping[str, object]) -> dict[str, object]:
    return {name: _wrapped_value(value) for name, value in inputs.items()}


def serialize_output(value: object) -> object:
    if isinstance(value, ObjectSet):
        return value.to_descriptor()
    if isinstance(value, OntologyObject):
        return value.to_payload()
    if isinstance(value, list):
        return [serialize_output(item) for item in value]
    if isinstance(value, dict):
        return {str(key): serialize_output(item) for key, item in value.items()}
    return value


def install_import_compatibility() -> None:
    functions_api = types.ModuleType("functions.api")
    functions_api.__dict__.update(
        function=lambda callable_: callable_,
        Integer=int,
        Long=int,
        Float=float,
        Double=float,
    )
    functions = types.ModuleType("functions")
    functions.__dict__["api"] = functions_api
    ontology_sdk = _dynamic_module("ontology_sdk")
    ontology_sdk.__dict__["FoundryClient"] = FoundryClient
    ontology = _dynamic_module("ontology_sdk.ontology")
    ontology_objects = _dynamic_module("ontology_sdk.ontology.objects", has_object_types=True)
    ontology_sets = _dynamic_module("ontology_sdk.ontology.object_sets", has_object_sets=True)
    ontology_sdk.__dict__["ontology"] = ontology
    ontology.__dict__["objects"] = ontology_objects
    ontology.__dict__["object_sets"] = ontology_sets
    sys.modules.update(
        {
            "functions": functions,
            "functions.api": functions_api,
            "ontology_sdk": ontology_sdk,
            "ontology_sdk.ontology": ontology,
            "ontology_sdk.ontology.objects": ontology_objects,
            "ontology_sdk.ontology.object_sets": ontology_sets,
        }
    )


def _dynamic_module(
    name: str,
    *,
    has_object_types: bool = False,
    has_object_sets: bool = False,
) -> types.ModuleType:
    module = types.ModuleType(name)

    def resolve(attribute: str) -> object:
        if has_object_types:
            return ObjectTypeMeta(attribute, (OntologyObjectType,), {})
        if has_object_sets:
            return ObjectSet
        raise AttributeError(attribute)

    module.__dict__["__getattr__"] = resolve
    return module


def _wrapped_value(value: object) -> object:
    if isinstance(value, Mapping) and isinstance(value.get("$foundryObjectSet"), Mapping):
        descriptor = value["$foundryObjectSet"]
        return ObjectSet(
            str(descriptor.get("objectType") or ""),
            descriptor.get("filter") if isinstance(descriptor.get("filter"), Mapping) else None,
            tuple(item for item in descriptor.get("orderBy", []) if isinstance(item, Mapping)),
        )
    if isinstance(value, Mapping) and isinstance(value.get("objectId"), str):
        return OntologyObject(value)
    return value


def _required_bridge() -> QueryBridge:
    if _bridge is None:
        raise RuntimeError("Ontology ObjectSet query bridge is unavailable")
    return _bridge


def _where_filter(
    filter_ast: Mapping[str, object] | None,
    filters: Mapping[str, object],
) -> Mapping[str, object] | None:
    if filter_ast is not None and filters:
        return _and_filter(filter_ast, _property_filters(filters))
    if filter_ast is not None:
        return _normalize_filter(filter_ast)
    return _property_filters(filters)


def _property_filters(filters: Mapping[str, object]) -> Mapping[str, object] | None:
    compiled = [_property_filter(name, "$eq", value) for name, value in filters.items()]
    if not compiled:
        return None
    return compiled[0] if len(compiled) == 1 else {"and": compiled}


def _property_filter(name: str, operator: str, value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping) and len(value) == 1:
        operator, value = next(iter(value.items()))
    operators = {
        "$eq": "eq",
        "$in": "in",
        "$gt": "gt",
        "$gte": "gte",
        "$lt": "lt",
        "$lte": "lte",
        "$contains": "contains",
    }
    if operator not in operators:
        raise ValueError(f"unsupported ObjectSet filter operator {operator}")
    return {"property": name, "op": operators[operator], "value": value}


def _normalize_filter(value: Mapping[str, object]) -> Mapping[str, object]:
    if "and" in value or "or" in value or "property" in value:
        return dict(value)
    compiled = [_property_filter(name, "$eq", operand) for name, operand in value.items()]
    return compiled[0] if len(compiled) == 1 else {"and": compiled}


def _and_filter(
    left: Mapping[str, object] | None,
    right: Mapping[str, object] | None,
) -> Mapping[str, object] | None:
    if left is None:
        return right
    if right is None:
        return left
    return {"and": [dict(left), dict(right)]}
