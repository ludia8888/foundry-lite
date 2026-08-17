"""Code Functions keep ObjectSet inputs lazy and permission-scoped."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from foundry_lite.application.ports.code_execution import (
    FunctionExecutionPlan,
    FunctionExecutionResult,
    FunctionQueryExecutor,
)
from foundry_lite.application.services.python_function_runtime import (
    PythonFunctionRuntimeService,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

_CTX = RequestContext(roles=("analyst",), actor_user_id="u-1", tenant_id="tenant-a")


class _RecordingAdapter:
    profile_name = "recording"

    def __init__(
        self,
        output: object = 6,
        runtime_evidence: Mapping[str, object] | None = None,
    ) -> None:
        self.plans: list[FunctionExecutionPlan] = []
        self._output = output
        self._runtime_evidence = runtime_evidence
        self.query_executor: FunctionQueryExecutor | None = None

    def execute_function(
        self,
        plan: FunctionExecutionPlan,
        *,
        query_executor: FunctionQueryExecutor | None = None,
    ) -> FunctionExecutionResult:
        self.plans.append(plan)
        self.query_executor = query_executor
        return FunctionExecutionResult(
            output=self._output,
            stderr_byte_count=0,
            duration_ms=1,
            runtime_evidence=self._runtime_evidence,
        )


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
        order_by: Any = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Mapping[str, object]:
        self.calls.append(
            {
                "kind": "query",
                "objectType": object_type_api_name,
                "filter": filter_ast,
                "orderBy": order_by,
                "limit": limit,
                "cursor": cursor,
                "ctx": ctx,
            }
        )
        return {"items": self.rows, "nextCursor": None}

    def aggregate_objects(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        group_by: object = None,
        select: object = None,
    ) -> Mapping[str, object]:
        self.calls.append(
            {
                "kind": "aggregate",
                "objectType": object_type_api_name,
                "filter": filter_ast,
                "groupBy": group_by,
                "select": select,
                "ctx": ctx,
            }
        )
        return {"groups": [{"key": {}, "metrics": {"count": 1}}], "totalGroups": 1}


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


def test_an_object_set_argument_stays_a_deferred_descriptor_until_the_function_reads_it() -> None:
    service, adapter, query = _service()

    service.run(
        _CTX,
        definition=_definition({"apiName": "tables", "type": "objectSet", "objectType": "DiningTable"}),
        inputs={"tables": {"filter": {"eq": {"status": "FREE"}}}},
    )

    assert query.calls == []
    assert adapter.plans[0].inputs_json == {
        "tables": {
            "$foundryObjectSet": {
                "objectType": "DiningTable",
                "filter": {"eq": {"status": "FREE"}},
                "orderBy": [],
            }
        }
    }


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


def test_the_sandbox_result_preserves_safe_runtime_evidence_for_the_caller() -> None:
    evidence = {"executionMode": "kubernetes-job", "imageDigest": "sha256:" + "a" * 64}
    service, _, _ = _service(adapter=_RecordingAdapter(runtime_evidence=evidence))

    result = service.run(
        _CTX,
        definition=_definition({"apiName": "threshold", "type": "integer"}),
        inputs={"threshold": 4},
    )

    assert result.output == 6
    assert result.runtime_evidence == evidence


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


def test_the_lazy_query_callback_preserves_context_filter_order_and_page_bounds() -> None:
    service, adapter, query = _service()
    service.run(
        _CTX,
        definition=_definition({"apiName": "tables", "type": "objectSet", "objectType": "DiningTable"}),
        inputs={"tables": None},
    )

    assert callable(adapter.query_executor)
    adapter.query_executor(
        {
            "operation": "fetchPage",
            "objectType": "DiningTable",
            "filter": {"property": "status", "op": "eq", "value": "FREE"},
            "orderBy": [{"property": "seats", "direction": "desc"}],
            "pageSize": 900,
            "pageToken": "next-1",
        }
    )

    assert query.calls == [
        {
            "kind": "query",
            "objectType": "DiningTable",
            "filter": {"property": "status", "op": "eq", "value": "FREE"},
            "orderBy": [{"property": "seats", "direction": "desc"}],
            "limit": 500,
            "cursor": "next-1",
            "ctx": _CTX,
        }
    ]


def test_the_lazy_aggregate_callback_uses_the_same_governed_service() -> None:
    service, adapter, query = _service()
    service.run(
        _CTX,
        definition=_definition({"apiName": "tables", "type": "objectSet", "objectType": "DiningTable"}),
        inputs={"tables": None},
    )
    assert callable(adapter.query_executor)
    result = adapter.query_executor(
        {
            "operation": "aggregate",
            "objectType": "DiningTable",
            "filter": None,
            "groupBy": ["status"],
            "select": [{"name": "count", "function": "count", "property": None}],
        }
    )

    assert result["totalGroups"] == 1
    assert query.calls[0]["kind"] == "aggregate"
    assert query.calls[0]["ctx"] is _CTX


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


# --- single-object inputs and the timeout budget --------------------------------------


def test_an_object_input_is_fetched_by_id_through_the_governed_service() -> None:
    """A single object is a get, not a one-row query, and the caller supplies only its id."""
    service, adapter, query = _service()

    service.run(
        _CTX,
        definition=_definition({"apiName": "table", "type": "object", "objectType": "DiningTable"}),
        inputs={"table": "T-7"},
    )

    assert query.calls[0] == {"kind": "get", "objectType": "DiningTable", "objectId": "T-7", "ctx": _CTX}
    assert adapter.plans[0].inputs_json == {"table": {"objectId": "T-7", "seats": 4}}


def test_an_object_input_also_accepts_the_object_id_wrapped_in_an_object() -> None:
    """OSDK callers pass {objectId}; requiring a bare string would make them unwrap it first."""
    service, _, query = _service()

    service.run(
        _CTX,
        definition=_definition({"apiName": "table", "type": "object", "objectType": "DiningTable"}),
        inputs={"table": {"objectId": "T-9"}},
    )

    assert query.calls[0]["objectId"] == "T-9"


def test_an_object_input_that_is_neither_an_id_nor_a_reference_is_refused() -> None:
    service, _, _ = _service()

    with pytest.raises(ValidationFailed, match="must be an object id"):
        service.run(
            _CTX,
            definition=_definition({"apiName": "table", "type": "object", "objectType": "DiningTable"}),
            inputs={"table": 7},
        )


def test_a_malformed_lazy_page_is_returned_to_the_runner_for_contract_validation() -> None:
    """The host does not load or reinterpret rows; the runner owns page-shape validation."""

    class _Broken(_RecordingObjectQuery):
        def query_objects(self, object_type_api_name: str, **_kwargs: Any) -> Mapping[str, object]:
            return {"items": "not-a-list"}

    service, adapter, _ = _service(query=_Broken())
    service.run(
        _CTX,
        definition=_definition({"apiName": "tables", "type": "objectSet", "objectType": "DiningTable"}),
        inputs={"tables": None},
    )
    assert callable(adapter.query_executor)

    assert adapter.query_executor({"operation": "fetchPage", "objectType": "DiningTable", "pageSize": 10}) == {
        "items": "not-a-list"
    }


def test_a_definition_with_no_declared_inputs_sends_nothing() -> None:
    service, adapter, _ = _service()
    definition = _definition({"apiName": "unused", "type": "integer"})
    definition["inputs"] = []

    service.run(_CTX, definition=definition, inputs={"stray": 1})

    assert adapter.plans[0].inputs_json == {}
    assert adapter.plans[0].argument_order == ()


def test_a_definition_without_an_output_type_is_refused() -> None:
    service, adapter, _ = _service()
    definition = _definition({"apiName": "threshold", "type": "integer"})
    definition["output"] = {}

    with pytest.raises(ValidationFailed, match="missing its output type"):
        service.run(_CTX, definition=definition, inputs={})

    assert adapter.plans == []


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        (None, 60),
        (True, 60),
        ("120", 60),
        (120, 120),
        (0, 1),
        (-5, 1),
        (10_000, 280),
    ],
)
def test_the_plan_timeout_is_clamped_to_the_permitted_range(declared: object, expected: int) -> None:
    """A definition persisted before per-version timeouts carries none, and a stored value that
    drifted outside the range is clamped rather than trusted: the sandbox budget is an operator
    bound, and a definition is authored content."""
    service, adapter, _ = _service()
    definition = _definition({"apiName": "threshold", "type": "integer"})
    if declared is not None:
        definition["timeoutSeconds"] = declared

    service.run(_CTX, definition=definition, inputs={})

    assert adapter.plans[0].timeout_seconds == expected
