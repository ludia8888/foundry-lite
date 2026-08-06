"""Executes ontology functions written in Python inside the isolated code sandbox.

Palantir authors Functions on Objects in TypeScript or Python and hands them an OSDK client that
reads the Ontology as the function runs. Our sandbox has no network, so the reads happen first:
this service resolves every declared ``object`` and ``objectSet`` input through the governed
object query service under the caller's own context, serializes the result, and gives the
sandbox a JSON document. User code never holds a database handle, a credential, or a policy
decision -- only rows the platform already decided this caller may see.

The trade is that an object set is a materialized list rather than the lazy handle Palantir
passes, so a function cannot walk a collection larger than ``MAX_MATERIALIZED_OBJECTS``. That
ceiling is enforced here and reported as a validation failure naming the input, rather than
silently truncating the set the function is reasoning about.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from foundry_lite.application.ports.code_execution import (
    CodeExecutionAdapter,
    FunctionExecutionPlan,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.ontology.function_types import (
    FUNCTION_DEFAULT_TIMEOUT_SECONDS,
    FUNCTION_MAX_TIMEOUT_SECONDS,
)

# A function reads what it was given, so the ceiling bounds host memory and the sandbox payload
# rather than the query itself. Palantir's object sets stay lazy and have no equivalent cap;
# ours is the honest limit of the materialized design and is documented as such.
MAX_MATERIALIZED_OBJECTS = 1000
MAX_INPUT_BYTES = 4 * 1024 * 1024

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
        limit: int = 50,
    ) -> Mapping[str, object]: ...


class PythonFunctionRuntimeService(CoreService):
    """Resolve declared inputs, then run one Python function in the sandbox."""

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
            inputs_json=self._materialized_inputs(ctx, definition, inputs),
            argument_order=tuple(str(item["apiName"]) for item in _declared_inputs(definition)),
            output_type=_output_type(definition),
            timeout_seconds=_timeout_seconds(definition),
            input_byte_limit=MAX_INPUT_BYTES,
        )
        return self.code_execution_adapter.execute_function(plan).output

    def _materialized_inputs(
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
        return self._materialized_set(ctx, declared, object_type, value)

    def _materialized_set(
        self,
        ctx: RequestContext,
        declared: Mapping[str, object],
        object_type: str,
        value: object,
    ) -> list[dict[str, object]]:
        api_name = str(declared["apiName"])
        # One over the ceiling, so a set that is exactly at the limit is served and one past it
        # is refused by name rather than quietly delivered short.
        result = self.object_query_service.query_objects(
            object_type,
            ctx=ctx,
            filter_ast=_filter_ast(declared, value),
            limit=MAX_MATERIALIZED_OBJECTS + 1,
        )
        rows = result.get("objects")
        if not isinstance(rows, Sequence):
            raise ValidationFailed(
                "object query did not return a collection",
                details={"input": api_name, "objectType": object_type},
            )
        if len(rows) > MAX_MATERIALIZED_OBJECTS:
            raise ValidationFailed(
                "object set input exceeds what a Python function can be handed",
                details={"input": api_name, "objectType": object_type, "limit": MAX_MATERIALIZED_OBJECTS},
            )
        return [dict(row) for row in rows if isinstance(row, Mapping)]


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


def _timeout_seconds(definition: Mapping[str, object]) -> int:
    """A definition persisted before per-version timeouts existed carries none; it gets the default."""
    declared = definition.get("timeoutSeconds")
    if not isinstance(declared, int) or isinstance(declared, bool):
        return FUNCTION_DEFAULT_TIMEOUT_SECONDS
    return min(max(declared, 1), FUNCTION_MAX_TIMEOUT_SECONDS)
