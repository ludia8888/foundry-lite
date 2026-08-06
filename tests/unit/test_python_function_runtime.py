"""Python ontology functions: what the sandbox is handed, and what it is refused.

Palantir runs Functions on Objects with an OSDK client that reads the Ontology while the
function executes. Our sandbox has no network, so the reads happen first and the function
receives a JSON document. These tests pin the two consequences of that choice: the resolution
goes through the governed query service under the caller's own context, and an object set too
large to hand over fails by name instead of arriving silently truncated.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from foundry_lite.application.ports.code_execution import FunctionExecutionPlan, FunctionExecutionResult
from foundry_lite.application.services.python_function_runtime import (
    MAX_MATERIALIZED_OBJECTS,
    PythonFunctionRuntimeService,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

_CTX = RequestContext(roles=("analyst",), actor_user_id="u-1", tenant_id="tenant-a")


class _RecordingAdapter:
    profile_name = "recording"

    def __init__(self, output: object = 6) -> None:
        self.plans: list[FunctionExecutionPlan] = []
        self._output = output

    def execute_function(self, plan: FunctionExecutionPlan) -> FunctionExecutionResult:
        self.plans.append(plan)
        return FunctionExecutionResult(output=self._output, stderr_byte_count=0, duration_ms=1)


class _RecordingObjectQuery:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows if rows is not None else [{"objectId": "T-1", "seats": 4}]
        self.calls: list[dict[str, Any]] = []

    def get_object(
        self, object_type_api_name: str, object_id: str, *, ctx: RequestContext | None = None
    ) -> Mapping[str, object]:
        self.calls.append({"kind": "get", "objectType": object_type_api_name, "objectId": object_id, "ctx": ctx})
        return {"objectId": object_id, "seats": 4}

    def query_objects(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        limit: int = 50,
    ) -> Mapping[str, object]:
        self.calls.append(
            {"kind": "query", "objectType": object_type_api_name, "filter": filter_ast, "limit": limit, "ctx": ctx}
        )
        return {"objects": self.rows}


def _service(
    adapter: _RecordingAdapter | None = None,
    query: _RecordingObjectQuery | None = None,
) -> tuple[PythonFunctionRuntimeService, _RecordingAdapter, _RecordingObjectQuery]:
    adapter = adapter or _RecordingAdapter()
    query = query or _RecordingObjectQuery()
    service = PythonFunctionRuntimeService.__new__(PythonFunctionRuntimeService)
    service.code_execution_adapter = adapter  # type: ignore[assignment]
    service.object_query_service = query  # type: ignore[assignment]
    return service, adapter, query


def _definition(input_def: dict[str, Any], output_type: str = "integer", runtime: str = "python") -> dict[str, Any]:
    return {
        "apiName": "TotalSeats",
        "version": "v3",
        "runtime": runtime,
        "inputs": [input_def],
        "output": {"type": output_type},
        "definition": {"source": "def compute(tables):\n    return 6\n", "entrypoint": "compute"},
    }


# --- what reaches the sandbox --------------------------------------------------------


def test_an_object_set_argument_is_a_filter_resolved_host_side_not_rows_from_the_caller() -> None:
    """The caller names a filter; the platform decides which rows exist and may be seen.

    Accepting rows from the caller would let a function be handed objects the caller cannot
    read, which is the whole reason resolution happens on this side of the boundary.
    """
    service, adapter, query = _service()

    service.run(
        _CTX,
        definition=_definition({"apiName": "tables", "type": "objectSet", "objectType": "DiningTable"}),
        inputs={"tables": {"filter": {"eq": {"status": "FREE"}}}},
    )

    assert query.calls[0]["objectType"] == "DiningTable"
    assert query.calls[0]["filter"] == {"eq": {"status": "FREE"}}
    assert query.calls[0]["ctx"] is _CTX
    assert adapter.plans[0].inputs_json == {"tables": [{"objectId": "T-1", "seats": 4}]}


def test_the_plan_carries_the_pinned_version_and_the_declared_output_type() -> None:
    service, adapter, _ = _service()

    service.run(
        _CTX,
        definition=_definition({"apiName": "tables", "type": "objectSet", "objectType": "DiningTable"}),
        inputs={"tables": None},
    )

    plan = adapter.plans[0]
    assert (plan.function_api_name, plan.function_version, plan.output_type) == ("TotalSeats", "v3", "integer")
    assert plan.entrypoint == "compute"


def test_a_scalar_input_is_passed_through_without_a_query() -> None:
    service, adapter, query = _service()

    service.run(
        _CTX,
        definition=_definition({"apiName": "threshold", "type": "integer"}),
        inputs={"threshold": 4},
    )

    assert query.calls == []
    assert adapter.plans[0].inputs_json == {"threshold": 4}


def test_an_absent_optional_input_is_omitted_rather_than_sent_as_null() -> None:
    """A missing argument and an argument that is null are different things to user code."""
    service, adapter, _ = _service()

    service.run(_CTX, definition=_definition({"apiName": "threshold", "type": "integer"}), inputs={})

    assert adapter.plans[0].inputs_json == {}


# --- what is refused -----------------------------------------------------------------


def test_an_object_set_over_the_ceiling_fails_instead_of_arriving_truncated() -> None:
    """A function that computes over a silently shortened set returns a confidently wrong answer.

    This is the visible cost of materializing inputs rather than handing the function a lazy
    handle, so it surfaces as a validation failure naming the input and the limit.
    """
    oversized = [{"objectId": f"T-{index}"} for index in range(MAX_MATERIALIZED_OBJECTS + 1)]
    service, _, _ = _service(query=_RecordingObjectQuery(rows=oversized))

    with pytest.raises(ValidationFailed, match="exceeds what a Python function can be handed") as caught:
        service.run(
            _CTX,
            definition=_definition({"apiName": "tables", "type": "objectSet", "objectType": "DiningTable"}),
            inputs={"tables": None},
        )

    assert caught.value.details["input"] == "tables"
    assert caught.value.details["limit"] == MAX_MATERIALIZED_OBJECTS


def test_an_object_set_exactly_at_the_ceiling_is_served() -> None:
    """Off-by-one here would reject a legitimate set, so the boundary is pinned from both sides."""
    exact = [{"objectId": f"T-{index}"} for index in range(MAX_MATERIALIZED_OBJECTS)]
    service, adapter, _ = _service(query=_RecordingObjectQuery(rows=exact))

    service.run(
        _CTX,
        definition=_definition({"apiName": "tables", "type": "objectSet", "objectType": "DiningTable"}),
        inputs={"tables": None},
    )

    assert len(adapter.plans[0].inputs_json["tables"]) == MAX_MATERIALIZED_OBJECTS  # type: ignore[arg-type]


def test_an_object_input_without_an_object_type_is_refused() -> None:
    service, _, _ = _service()

    with pytest.raises(ValidationFailed, match="must declare an objectType"):
        service.run(
            _CTX,
            definition=_definition({"apiName": "table", "type": "object"}),
            inputs={"table": "T-1"},
        )


def test_an_object_set_argument_that_is_not_a_filter_is_refused() -> None:
    """Passing a list here is the natural mistake, and it is the one that must not silently work."""
    service, _, _ = _service()

    with pytest.raises(ValidationFailed, match="must be a filter object"):
        service.run(
            _CTX,
            definition=_definition({"apiName": "tables", "type": "objectSet", "objectType": "DiningTable"}),
            inputs={"tables": [{"objectId": "T-1"}]},
        )


def test_a_definition_without_source_is_refused_before_the_sandbox_starts() -> None:
    service, adapter, _ = _service()
    definition = _definition({"apiName": "threshold", "type": "integer"})
    definition["definition"] = {}

    with pytest.raises(ValidationFailed, match="missing its source"):
        service.run(_CTX, definition=definition, inputs={})

    assert adapter.plans == []


# --- the plan tells the sandbox which runtime it is serving ---------------------------


def test_the_plan_carries_the_runtime_so_the_adapter_can_pick_an_image() -> None:
    """Python and TypeScript run in different images; the plan is where that choice comes from."""
    service, adapter, _ = _service()

    service.run(
        _CTX,
        definition=_definition({"apiName": "threshold", "type": "integer"}, runtime="typescript"),
        inputs={"threshold": 4},
    )

    assert adapter.plans[0].runtime == "typescript"


def test_the_plan_carries_declaration_order_for_positional_languages() -> None:
    """TypeScript has no keyword arguments, so argument order is the host's to own, not the runner's."""
    service, adapter, _ = _service()
    definition = _definition({"apiName": "tables", "type": "objectSet", "objectType": "DiningTable"})
    definition["inputs"].append({"apiName": "partySize", "type": "integer"})

    service.run(_CTX, definition=definition, inputs={"tables": None, "partySize": 2})

    assert adapter.plans[0].argument_order == ("tables", "partySize")
