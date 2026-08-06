"""Which runtime executes, and what the caller is told about the run.

A function type declares its runtime and the service dispatches on it. The Logic DAG path exists
to mediate model-driven tool calls through the broker and records an AI run for that reason; a
code function calls nothing, because its reads were resolved before the sandbox started. These
tests pin that the dispatch goes where it should and that the result does not claim an AI run
that never happened.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import pytest
from foundry_lite.application.ports import FunctionTypeRow
from foundry_lite.application.services.function_execution_service import FunctionExecutionService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed

_CTX = RequestContext(roles=("admin",), actor_user_id="u-1", tenant_id="tenant-a")


class _RecordingScope:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def require_resource_scope(
        self, ctx: RequestContext, *, resource_type: str, resource_api_name: str, operation: str
    ) -> None:
        del ctx
        self.calls.append({"type": resource_type, "api": resource_api_name, "operation": operation})


class _RecordingPythonRuntime:
    def __init__(self, output: object = 6) -> None:
        self.calls: list[dict[str, Any]] = []
        self._output = output

    def run(self, ctx: RequestContext, *, definition: Mapping[str, object], inputs: Mapping[str, object]) -> object:
        self.calls.append({"ctx": ctx, "definition": definition, "inputs": inputs})
        return self._output


class _FakeConnection:
    pass


class _FakeEngine:
    def begin(self) -> Any:
        from contextlib import nullcontext

        return nullcontext(_FakeConnection())


class _RecordingAudit:
    """A denial that is not recorded is a denial nobody can review afterwards."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def _audit(self, conn: object, ctx: RequestContext, **fields: Any) -> None:
        del conn, ctx
        self.events.append(fields)


class _UnusedBuilderRuntime:
    def run(self, ctx: RequestContext, request: object) -> object:
        raise AssertionError("a code function must not reach the Logic DAG runtime")


def _row(runtime: str = "python", **definition: Any) -> FunctionTypeRow:
    return cast(
        FunctionTypeRow,
        {
            "api_name": "TotalSeats",
            "definition": {
                "apiName": "TotalSeats",
                "version": "2.1.0",
                "runtime": runtime,
                "inputs": [{"apiName": "threshold", "type": "integer", "required": False}],
                "output": {"type": "integer"},
                "definition": {"source": "def compute():\n    return 6\n", "entrypoint": "compute"},
                **definition,
            },
        },
    )


def _service(
    python_runtime: _RecordingPythonRuntime | None = None,
) -> tuple[FunctionExecutionService, _RecordingPythonRuntime, _RecordingScope, _RecordingAudit]:
    runtime = python_runtime or _RecordingPythonRuntime()
    scope = _RecordingScope()
    audit = _RecordingAudit()
    service = FunctionExecutionService.__new__(FunctionExecutionService)
    service.python_function_runtime_service = runtime  # type: ignore[assignment]
    service.osdk_application_service = scope  # type: ignore[assignment]
    service.builder_runtime_service = _UnusedBuilderRuntime()  # type: ignore[assignment]
    service.runtime_service = audit  # type: ignore[assignment]
    service.engine = _FakeEngine()  # type: ignore[assignment]
    return service, runtime, scope, audit


def test_a_python_function_runs_in_the_sandbox_and_never_touches_the_logic_dag() -> None:
    """_UnusedBuilderRuntime asserts on call, so a dispatch regression fails loudly here."""
    service, runtime, _, _ = _service()

    result = service._execute_row(_CTX, _row(), {"threshold": 4})

    assert result["status"] == "SUCCEEDED"
    assert result["output"] == {"value": 6}
    assert runtime.calls[0]["inputs"] == {"threshold": 4}


def test_a_code_function_reports_no_ai_run_rather_than_inventing_one() -> None:
    """Python source calls nothing through the tool broker, so there is no tool traffic to
    attribute. Reporting an id here would point an auditor at a ledger entry that does not exist."""
    service, _, _, _ = _service()

    assert service._execute_row(_CTX, _row(), {})["aiRunId"] is None


def test_the_osdk_execute_scope_is_required_before_anything_runs() -> None:
    service, _, scope, _ = _service()

    service._execute_row(_CTX, _row(), {})

    assert scope.calls == [{"type": "function", "api": "TotalSeats", "operation": "execute"}]


def test_the_result_hash_covers_the_code_identity_the_run_and_the_output() -> None:
    """A hash that ignored the version would collide across two functions that returned the same
    value, which is the case an auditor most needs to tell apart."""
    service, _, _, _ = _service()

    first = service._execute_row(_CTX, _row(), {}, execution_id="run-1")
    same = service._execute_row(_CTX, _row(), {}, execution_id="run-1")
    other_version = service._execute_row(_CTX, _row(version="9.9.9"), {}, execution_id="run-1")
    other_run = service._execute_row(_CTX, _row(), {}, execution_id="run-2")

    assert first["resultHash"] == same["resultHash"]
    assert first["resultHash"] != other_version["resultHash"]
    assert first["resultHash"] != other_run["resultHash"]


def test_a_supplied_execution_id_is_used_as_the_run_id() -> None:
    """An Action names the run so its ledger entry and the function run can be joined."""
    service, _, _, _ = _service()

    assert service._execute_row(_CTX, _row(), {}, execution_id="act-7:function")["logicRunId"] == "act-7:function"


def test_an_input_that_does_not_match_the_declaration_is_refused_before_dispatch() -> None:
    service, runtime, _, _ = _service()

    with pytest.raises(ValidationFailed):
        service._execute_row(_CTX, _row(), {"threshold": "four"})

    assert runtime.calls == []


def test_a_caller_outside_the_declared_roles_is_denied() -> None:
    """Roles are checked before the scope call, so a denial cannot be mistaken for a scope grant."""
    service, runtime, scope, audit = _service()
    row = _row(permissions={"allowedRoles": ["operator"]})

    with pytest.raises(PermissionDenied, match="permission denied for function execution"):
        service._execute_row(RequestContext(roles=("analyst",), actor_user_id="u-2"), row, {})

    assert runtime.calls == []
    assert scope.calls == []
    assert audit.events[0]["decision"] == "deny"
    assert audit.events[0]["resource_id"] == "TotalSeats"


# --- resolving a dependency against what is deployed ----------------------------------


class _FakeLookup:
    """Stands in for the ontology lookup, which owns reading a definition at a given version."""

    def __init__(self, row: FunctionTypeRow) -> None:
        self.row = row
        self.calls: list[tuple[str, str]] = []

    def _function_type_for_version(
        self, conn: object, ctx: RequestContext, ontology_version_id: str, function_api_name: str
    ) -> FunctionTypeRow:
        del conn, ctx
        self.calls.append((ontology_version_id, function_api_name))
        return self.row

    def _active_function_type(self, conn: object, ctx: RequestContext, function_api_name: str) -> FunctionTypeRow:
        del conn, ctx
        self.calls.append(("ACTIVE", function_api_name))
        return self.row


def _pinned_service(deployed_version: str) -> tuple[FunctionExecutionService, _FakeLookup]:
    service, _, _, _ = _service()
    lookup = _FakeLookup(_row(version=deployed_version))
    service.ontology_lookup_service = lookup  # type: ignore[assignment]
    return service, lookup


@pytest.mark.parametrize(
    ("deployed", "requirement"),
    [("2.1.0", "2.1.0"), ("2.1.4", "^2.1.0"), ("2.3.0", "^2.1.0"), ("2.1.9", "~2.1.0")],
)
def test_a_deployed_version_that_satisfies_the_dependency_runs(deployed: str, requirement: str) -> None:
    """A range is what lets a patched function be published under a running Action."""
    service, lookup = _pinned_service(deployed)

    result = service.execute_pinned_function(
        "TotalSeats", function_version=requirement, ontology_version_id="ov-1", inputs={}, ctx=_CTX
    )

    assert result["status"] == "SUCCEEDED"
    assert lookup.calls == [("ov-1", "TotalSeats")]


@pytest.mark.parametrize(
    ("deployed", "requirement"),
    [("2.1.1", "2.1.0"), ("3.0.0", "^2.1.0"), ("2.2.0", "~2.1.0"), ("2.0.9", "^2.1.0")],
)
def test_a_deployed_version_outside_the_dependency_is_refused(deployed: str, requirement: str) -> None:
    service, _ = _pinned_service(deployed)

    with pytest.raises(ValidationFailed, match="does not satisfy the dependency") as caught:
        service.execute_pinned_function(
            "TotalSeats", function_version=requirement, ontology_version_id="ov-1", inputs={}, ctx=_CTX
        )

    assert caught.value.details["actualVersion"] == deployed
    assert caught.value.details["requirement"] == requirement


def test_the_failure_says_whether_the_dependency_was_a_pin_or_a_range() -> None:
    """ "Your pin is stale" and "nothing in range is deployed" are different problems to fix."""
    service, _ = _pinned_service("3.0.0")

    for requirement, is_range in (("2.1.0", False), ("^2.1.0", True)):
        with pytest.raises(ValidationFailed) as caught:
            service.execute_pinned_function(
                "TotalSeats", function_version=requirement, ontology_version_id="ov-1", inputs={}, ctx=_CTX
            )
        assert caught.value.details["isRange"] is is_range


def test_the_definition_is_read_from_the_action_own_ontology_version() -> None:
    """Reading the ACTIVE version instead would let code change under a run already in flight."""
    service, lookup = _pinned_service("2.1.0")

    service.execute_pinned_function(
        "TotalSeats", function_version="2.1.0", ontology_version_id="ov-frozen", inputs={}, ctx=_CTX
    )

    assert lookup.calls == [("ov-frozen", "TotalSeats")]


def test_describe_function_enforces_the_same_permissions_as_execution() -> None:
    """A caller that cannot run a function must not be able to read its shape either."""
    service, _, scope, _ = _service()
    service.ontology_lookup_service = _FakeLookup(_row())  # type: ignore[assignment]

    described = service.describe_function("TotalSeats", ctx=_CTX)

    assert described["api_name"] == "TotalSeats"
    assert scope.calls == [{"type": "function", "api": "TotalSeats", "operation": "execute"}]


def test_describe_function_denies_a_caller_outside_the_declared_roles() -> None:
    service, _, scope, _ = _service()
    service.ontology_lookup_service = _FakeLookup(_row(permissions={"allowedRoles": ["operator"]}))  # type: ignore[assignment]

    with pytest.raises(PermissionDenied):
        service.describe_function("TotalSeats", ctx=RequestContext(roles=("analyst",), actor_user_id="u-2"))

    assert scope.calls == []
