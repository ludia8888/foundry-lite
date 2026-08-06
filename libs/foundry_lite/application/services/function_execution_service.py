"""Ontology function execution use-case service (Functions on Objects).

Executes one registered function from the ACTIVE ontology version: role check
from the persisted ``permissions.allowedRoles`` (admin implicit; missing
declaration fails closed to admin-only), OSDK ``osdk:function:<api>:execute``
scope, declared-input validation, then the persisted Logic DAG runs through
the existing builder runtime (visual-builder validation, AI-run ledger
seeding, ToolBroker-mediated reads, write intent blocked). Functions are
READ/compute-only, so no idempotency key is required.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import TypedDict

from foundry_lite.application.ports import FunctionTypeRow, OntologyJsonObject
from foundry_lite.application.primitives import _new_id
from foundry_lite.application.services.action_helpers import SupportsAudit
from foundry_lite.application.services.action_protocols import ActionOsdkScopeBoundary
from foundry_lite.application.services.aip.builder_runtime import (
    BuilderRuntimeRequest,
    BuilderRuntimeResult,
    BuilderRuntimeService,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.ontology_function_validation import (
    function_allowed_roles,
    function_draft_request,
    function_tool_ids,
    validate_function_inputs,
)
from foundry_lite.application.services.ontology_lookup_service import OntologyLookupService
from foundry_lite.application.services.python_function_runtime import PythonFunctionRuntimeService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed
from foundry_lite.domain.ontology.function_types import FUNCTION_RUNTIME_PYTHON
from foundry_lite.domain.ontology.function_versions import is_range, satisfies


class FunctionExecutionResult(TypedDict):
    """Public result returned after executing one ontology function."""

    functionApiName: str
    logicRunId: str
    aiRunId: str | None
    status: str
    output: OntologyJsonObject
    resultHash: str


class FunctionExecutionService(CoreService):
    """Execute registered ontology functions through the bounded Logic runtime."""

    required_dependencies = ("engine",)
    required_collaborators = (
        "builder_runtime_service",
        "ontology_lookup_service",
        "osdk_application_service",
        "python_function_runtime_service",
        "runtime_service",
    )
    builder_runtime_service: BuilderRuntimeService
    ontology_lookup_service: OntologyLookupService
    python_function_runtime_service: PythonFunctionRuntimeService
    osdk_application_service: ActionOsdkScopeBoundary
    runtime_service: SupportsAudit

    def execute_function(
        self,
        function_api_name: str,
        *,
        inputs: Mapping[str, object],
        ctx: RequestContext | None = None,
    ) -> FunctionExecutionResult:
        ctx = ctx or RequestContext()
        with self.engine.begin() as conn:
            row = self.ontology_lookup_service._active_function_type(conn, ctx, function_api_name)
        return self._execute_row(ctx, row, inputs)

    def describe_function(self, function_api_name: str, *, ctx: RequestContext | None = None) -> FunctionTypeRow:
        ctx = ctx or RequestContext()
        with self.engine.begin() as conn:
            row = self.ontology_lookup_service._active_function_type(conn, ctx, function_api_name)
        self._require_function_roles(ctx, row)
        self.osdk_application_service.require_resource_scope(
            ctx,
            resource_type="function",
            resource_api_name=row["api_name"],
            operation="execute",
        )
        return row

    def execute_pinned_function(
        self,
        function_api_name: str,
        *,
        function_version: str,
        ontology_version_id: str,
        inputs: Mapping[str, object],
        ctx: RequestContext,
        execution_id: str | None = None,
    ) -> FunctionExecutionResult:
        """Execute the function an Action run depends on, pinned exactly or by range.

        `function_version` is a requirement, not always a literal. An exact version is what a
        caller pins when it cannot tolerate any change; a caret or tilde range is what lets a
        patched function be published underneath a running Action without redeploying it, which
        is the whole point of range dependencies. Either way the definition is read from the
        Action's own ontology version, so the code cannot change out from under a run in flight.
        """
        with self.engine.begin() as conn:
            row = self.ontology_lookup_service._function_type_for_version(
                conn, ctx, ontology_version_id, function_api_name
            )
        actual_version = str(row["definition"].get("version") or "v1")
        if not satisfies(actual_version, function_version):
            raise ValidationFailed(
                "deployed function version does not satisfy the dependency",
                details={
                    "requirement": function_version,
                    "actualVersion": actual_version,
                    "isRange": is_range(function_version),
                },
            )
        return self._execute_row(ctx, row, inputs, execution_id=execution_id)

    def _execute_row(
        self,
        ctx: RequestContext,
        row: FunctionTypeRow,
        inputs: Mapping[str, object],
        execution_id: str | None = None,
    ) -> FunctionExecutionResult:
        self._require_function_roles(ctx, row)
        self.osdk_application_service.require_resource_scope(
            ctx,
            resource_type="function",
            resource_api_name=row["api_name"],
            operation="execute",
        )
        validate_function_inputs(row["definition"], inputs)
        if row["definition"].get("runtime") == FUNCTION_RUNTIME_PYTHON:
            return self._execute_python(ctx, row, inputs, execution_id)
        request = _builder_request(ctx, row, inputs, logic_run_id=execution_id)
        return _execution_result(row, request, self.builder_runtime_service.run(ctx, request))

    def _execute_python(
        self,
        ctx: RequestContext,
        row: FunctionTypeRow,
        inputs: Mapping[str, object],
        execution_id: str | None,
    ) -> FunctionExecutionResult:
        """A code function runs in the sandbox, not the Logic DAG, so it seeds no AI run.

        The Logic DAG path exists to mediate model-driven tool calls through the broker and
        records an AI run for that reason. Python source calls nothing: its reads were resolved
        before the sandbox started, so there is no tool traffic to attribute and ``aiRunId`` is
        absent rather than fabricated.
        """
        run_id = execution_id or _new_id("fnrun")
        output = self.python_function_runtime_service.run(ctx, definition=row["definition"], inputs=inputs)
        return {
            "functionApiName": row["api_name"],
            "logicRunId": run_id,
            "aiRunId": None,
            "status": "SUCCEEDED",
            "output": {"value": output},
            "resultHash": _result_hash(row, run_id, output),
        }

    def _require_function_roles(self, ctx: RequestContext, row: FunctionTypeRow) -> None:
        """Enforce persisted allowedRoles; admin implicit, missing roles admin-only."""
        declared = function_allowed_roles(row["definition"])
        allowed = {"admin"} if declared is None else set(declared) | {"admin"}
        if any(role in allowed for role in ctx.roles):
            return
        with self.engine.begin() as conn:
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="permission.denied",
                resource_type="function_type",
                resource_id=row["api_name"],
                action="execute",
                decision="deny",
                after_ref={"allowedRoles": sorted(allowed)},
            )
        raise PermissionDenied(
            "permission denied for function execution",
            details={"functionType": row["api_name"], "allowedRoles": sorted(allowed)},
        )


def _builder_request(
    ctx: RequestContext,
    row: FunctionTypeRow,
    inputs: Mapping[str, object],
    logic_run_id: str | None = None,
) -> BuilderRuntimeRequest:
    definition = row["definition"]
    return BuilderRuntimeRequest(
        logic_run_id=logic_run_id or _new_id("fnrun"),
        draft=function_draft_request(ctx, definition),
        input_json=dict(inputs),
        user_message=f"Execute ontology function {row['api_name']}",
        agent_allowed_tools=function_tool_ids(definition),
    )


def _execution_result(
    row: FunctionTypeRow,
    request: BuilderRuntimeRequest,
    result: BuilderRuntimeResult,
) -> FunctionExecutionResult:
    if result.logic_result is None:
        raise ValidationFailed(
            "function execution failed",
            details={
                "functionType": row["api_name"],
                "runStatus": result.run_status,
                "error": dict(result.error) if result.error is not None else {},
                "issues": [issue.to_payload() for issue in result.validation.blocking_issues],
            },
        )
    return {
        "functionApiName": row["api_name"],
        "logicRunId": request.logic_run_id,
        "aiRunId": result.ai_run_id,
        "status": result.logic_result.status,
        "output": dict(result.logic_result.output_json),
        "resultHash": result.logic_result.result_hash,
    }


def _result_hash(row: FunctionTypeRow, run_id: str, output: object) -> str:
    """Same shape as the Logic DAG's hash: identity of the code, the run, and what it produced."""
    payload = json.dumps(
        {
            "functionApiName": row["api_name"],
            "functionVersion": row["definition"].get("version"),
            "logicRunId": run_id,
            "output": output,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
