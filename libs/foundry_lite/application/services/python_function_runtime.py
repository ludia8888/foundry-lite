"""Execute Python or TypeScript ontology functions with a governed lazy ObjectSet boundary.

Individual object inputs are resolved under the caller's policy before execution. ObjectSet
inputs stay as query plans: the isolated runner can refine them without loading rows, and asks
this service for a page or aggregation only when the function actually needs data. The runner
never receives a database handle, bearer token, or network route.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from foundry_lite.application.ports.code_execution import (
    CodeExecutionAdapter,
    FunctionExecutionPlan,
    FunctionQueryExecutor,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.ontology.function_types import (
    FUNCTION_DEFAULT_TIMEOUT_SECONDS,
    FUNCTION_MAX_TIMEOUT_SECONDS,
)

MAX_INPUT_BYTES = 4 * 1024 * 1024
FUNCTION_OBJECT_SET_PAGE_SIZE = 500

_OBJECT_INPUT_TYPES = frozenset({"object", "objectSet"})


class ObjectMaterializationBoundary(Protocol):
    """The governed read surface this service is allowed to resolve inputs through."""

    def get_object(
        self, object_type_api_name: str, object_id: str, *, ctx: RequestContext | None = None
    ) -> Mapping[str, object]: ...

    def query_objects(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        order_by: Sequence[Mapping[str, str]] | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Mapping[str, object]: ...

    def aggregate_objects(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        group_by: Sequence[str] | None = None,
        select: Sequence[Mapping[str, object]] | None = None,
    ) -> Mapping[str, object]: ...


class PythonFunctionRuntimeService(CoreService):
    """Resolve concrete inputs and run one code function with lazy ObjectSet queries."""

    required_dependencies = ("code_execution_adapter",)
    required_collaborators = ("object_query_service",)
    code_execution_adapter: CodeExecutionAdapter
    object_query_service: ObjectMaterializationBoundary

    def run(
        self,
        ctx: RequestContext,
        *,
        definition: Mapping[str, object],
        inputs: Mapping[str, object],
    ) -> object:
        source = _definition_source(definition)
        plan = FunctionExecutionPlan(
            function_api_name=str(definition.get("apiName") or ""),
            function_version=str(definition.get("version") or "v1"),
            runtime=str(definition.get("runtime") or ""),
            entrypoint=str(source["entrypoint"]),
            source=str(source["source"]),
            inputs_json=self._resolved_inputs(ctx, definition, inputs),
            argument_order=tuple(str(item["apiName"]) for item in _declared_inputs(definition)),
            output_type=_output_type(definition),
            timeout_seconds=_timeout_seconds(definition),
            input_byte_limit=MAX_INPUT_BYTES,
        )
        return self.code_execution_adapter.execute_function(
            plan,
            query_executor=self._query_executor(ctx),
        ).output

    def _resolved_inputs(
        self,
        ctx: RequestContext,
        definition: Mapping[str, object],
        inputs: Mapping[str, object],
    ) -> dict[str, object]:
        resolved: dict[str, object] = {}
        for declared in _declared_inputs(definition):
            api_name = str(declared["apiName"])
            if api_name not in inputs:
                continue
            resolved[api_name] = self._resolved_value(ctx, declared, inputs[api_name])
        return resolved

    def _resolved_value(self, ctx: RequestContext, declared: Mapping[str, object], value: object) -> object:
        declared_type = str(declared.get("type"))
        if declared_type not in _OBJECT_INPUT_TYPES:
            return value
        object_type = _required_object_type(declared)
        if declared_type == "object":
            return dict(self.object_query_service.get_object(object_type, _object_id(declared, value), ctx=ctx))
        return _object_set_descriptor(declared, object_type, value)

    def _query_executor(self, ctx: RequestContext) -> FunctionQueryExecutor:
        def execute(request: Mapping[str, object]) -> Mapping[str, object]:
            return self._execute_object_set_request(ctx, request)

        return execute

    def _execute_object_set_request(
        self,
        ctx: RequestContext,
        request: Mapping[str, object],
    ) -> Mapping[str, object]:
        operation = _request_text(request, "operation")
        if operation == "fetchPage":
            return self._fetch_object_set_page(ctx, request)
        if operation == "aggregate":
            return self._aggregate_object_set(ctx, request)
        raise ValidationFailed("unsupported function ObjectSet operation", details={"operation": operation})

    def _fetch_object_set_page(
        self,
        ctx: RequestContext,
        request: Mapping[str, object],
    ) -> Mapping[str, object]:
        page_size = request.get("pageSize", FUNCTION_OBJECT_SET_PAGE_SIZE)
        if not isinstance(page_size, int) or isinstance(page_size, bool):
            raise ValidationFailed("function ObjectSet pageSize must be an integer")
        return self.object_query_service.query_objects(
            _request_text(request, "objectType"),
            ctx=ctx,
            filter_ast=_request_mapping(request, "filter"),
            order_by=_request_order_sequence(request, "orderBy"),
            limit=min(max(page_size, 1), FUNCTION_OBJECT_SET_PAGE_SIZE),
            cursor=_request_optional_text(request, "pageToken"),
        )

    def _aggregate_object_set(
        self,
        ctx: RequestContext,
        request: Mapping[str, object],
    ) -> Mapping[str, object]:
        return self.object_query_service.aggregate_objects(
            _request_text(request, "objectType"),
            ctx=ctx,
            filter_ast=_request_mapping(request, "filter"),
            group_by=_request_text_sequence(request, "groupBy"),
            select=_request_mapping_sequence(request, "select"),
        )


def _definition_source(definition: Mapping[str, object]) -> Mapping[str, object]:
    source = definition.get("definition")
    if not isinstance(source, Mapping) or not isinstance(source.get("source"), str):
        raise ValidationFailed(
            "python function definition is missing its source",
            details={"function": str(definition.get("apiName") or "")},
        )
    return source


def _declared_inputs(definition: Mapping[str, object]) -> list[Mapping[str, object]]:
    declared = definition.get("inputs")
    if not isinstance(declared, Sequence):
        return []
    return [item for item in declared if isinstance(item, Mapping)]


def _output_type(definition: Mapping[str, object]) -> str:
    output = definition.get("output")
    if not isinstance(output, Mapping) or not isinstance(output.get("type"), str):
        raise ValidationFailed(
            "python function definition is missing its output type",
            details={"function": str(definition.get("apiName") or "")},
        )
    return str(output["type"])


def _required_object_type(declared: Mapping[str, object]) -> str:
    object_type = declared.get("objectType")
    if not isinstance(object_type, str) or not object_type:
        raise ValidationFailed(
            "object input must declare an objectType",
            details={"input": str(declared.get("apiName") or "")},
        )
    return object_type


def _object_id(declared: Mapping[str, object], value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping) and isinstance(value.get("objectId"), str):
        return str(value["objectId"])
    raise ValidationFailed(
        "object input must be an object id or {objectId}",
        details={"input": str(declared.get("apiName") or "")},
    )


def _filter_ast(declared: Mapping[str, object], value: object) -> Mapping[str, object] | None:
    """An object-set argument is a filter, not rows: the caller never supplies object contents."""
    if value is None:
        return None
    if isinstance(value, Mapping):
        candidate = value.get("filter", value)
        if isinstance(candidate, Mapping):
            return dict(candidate)
    raise ValidationFailed(
        "objectSet input must be a filter object",
        details={"input": str(declared.get("apiName") or "")},
    )


def _object_set_descriptor(
    declared: Mapping[str, object],
    object_type: str,
    value: object,
) -> dict[str, object]:
    return {
        "$foundryObjectSet": {
            "objectType": object_type,
            "filter": _filter_ast(declared, value),
            "orderBy": [],
        }
    }


def _request_text(request: Mapping[str, object], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationFailed("function ObjectSet request field must be text", details={"field": key})
    return value


def _request_optional_text(request: Mapping[str, object], key: str) -> str | None:
    value = request.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationFailed("function ObjectSet request field must be text", details={"field": key})
    return value


def _request_mapping(request: Mapping[str, object], key: str) -> Mapping[str, object] | None:
    value = request.get(key)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValidationFailed("function ObjectSet request field must be an object", details={"field": key})
    return dict(value)


def _request_mapping_sequence(
    request: Mapping[str, object],
    key: str,
) -> Sequence[Mapping[str, object]] | None:
    value = request.get(key)
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValidationFailed("function ObjectSet request field must be a list", details={"field": key})
    if not all(isinstance(item, Mapping) for item in value):
        raise ValidationFailed("function ObjectSet request items must be objects", details={"field": key})
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _request_order_sequence(
    request: Mapping[str, object],
    key: str,
) -> Sequence[Mapping[str, str]] | None:
    items = _request_mapping_sequence(request, key)
    if items is None:
        return None
    if not all(all(isinstance(value, str) for value in item.values()) for item in items):
        raise ValidationFailed("function ObjectSet order items must contain text", details={"field": key})
    return [{str(name): str(value) for name, value in item.items()} for item in items]


def _request_text_sequence(request: Mapping[str, object], key: str) -> Sequence[str] | None:
    value = request.get(key)
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValidationFailed("function ObjectSet request field must be a list", details={"field": key})
    if not all(isinstance(item, str) for item in value):
        raise ValidationFailed("function ObjectSet request items must be text", details={"field": key})
    return [str(item) for item in value]


def _timeout_seconds(definition: Mapping[str, object]) -> int:
    """A definition persisted before per-version timeouts existed carries none; it gets the default."""
    declared = definition.get("timeoutSeconds")
    if not isinstance(declared, int) or isinstance(declared, bool):
        return FUNCTION_DEFAULT_TIMEOUT_SECONDS
    return min(max(declared, 1), FUNCTION_MAX_TIMEOUT_SECONDS)
