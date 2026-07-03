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
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed


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
        "runtime_service",
    )
    builder_runtime_service: BuilderRuntimeService
    ontology_lookup_service: OntologyLookupService
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
        self._require_function_roles(ctx, row)
        self.osdk_application_service.require_resource_scope(
            ctx,
            resource_type="function",
            resource_api_name=function_api_name,
            operation="execute",
        )
        validate_function_inputs(row["definition"], inputs)
        request = _builder_request(ctx, row, inputs)
        return _execution_result(row, request, self.builder_runtime_service.run(ctx, request))

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
) -> BuilderRuntimeRequest:
    definition = row["definition"]
    return BuilderRuntimeRequest(
        logic_run_id=_new_id("fnrun"),
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
