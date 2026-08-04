"""Single governed execution plane for all AI FDE mode tools."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.services.aip.fde_application_tools import FdeApplicationToolService
from foundry_lite.application.services.aip.fde_ontology_tools import FdeOntologyToolRequest, FdeOntologyToolService
from foundry_lite.application.services.aip.fde_tool_result import (
    FdePlatformToolError,
    FdePlatformToolRequest,
    platform_tool_result,
    require_tool_approval,
    required_text,
    scope_value,
)
from foundry_lite.application.services.aip.tool_broker import ToolBrokerResult, ToolSpec
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext


class FdePipelineCatalog(Protocol):
    """Read the governed trained-model catalog."""

    def trained_models(self, *, ctx: RequestContext | None = None) -> dict[str, object]: ...


class FdePipelineDefinition(Protocol):
    """Read and edit isolated Pipeline branches."""

    def get_branch(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]: ...

    def diff_branch(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]: ...

    def update_graph(
        self,
        branch_id: str,
        *,
        graph: Mapping[str, object],
        expected_fingerprint: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...


class FdePipelineGovernance(Protocol):
    """Submit Pipeline branch proposals for human review."""

    def propose_branch(
        self,
        branch_id: str,
        *,
        title: str,
        description: str | None,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...


class FdePipelineValidation(Protocol):
    """Validate and test a Pipeline branch without deployment."""

    def validate_branch(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]: ...

    def run_tests(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]: ...


class FdeSourceConnections(Protocol):
    """Inspect and test registered Source connectivity."""

    def list_source_connection_tests(
        self, source_name: str, *, limit: int, ctx: RequestContext | None = None
    ) -> list[dict[str, object]]: ...

    def list_source_egress_attempts(
        self, source_name: str, *, limit: int, ctx: RequestContext | None = None
    ) -> list[dict[str, object]]: ...

    def test_source_connection(
        self,
        source_name: str,
        *,
        expected_config_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...


class FdeSourceReader(Protocol):
    """Read a registered Source through its normal permission path."""

    def get_source(self, source_name: str, *, ctx: RequestContext | None = None) -> dict[str, object]: ...


class FdeFunctionExecutor(Protocol):
    """Execute a versioned function through the governed runtime."""

    def execute_function(
        self, function_name: str, *, inputs: Mapping[str, object], ctx: RequestContext | None = None
    ) -> Mapping[str, object]: ...


class FdePlatformToolService(CoreService):
    """Authorize and route server-owned tools across supported FDE modes."""

    required_dependencies = ("policy",)
    required_collaborators = (
        "fde_application_tool_service",
        "fde_ontology_tool_service",
        "function_execution_service",
        "pipeline_catalog_service",
        "pipeline_definition_service",
        "pipeline_governance_service",
        "pipeline_graph_validation_service",
        "source_connection_test_service",
        "source_onboarding_service",
    )
    fde_application_tool_service: FdeApplicationToolService
    fde_ontology_tool_service: FdeOntologyToolService
    function_execution_service: FdeFunctionExecutor
    pipeline_catalog_service: FdePipelineCatalog
    pipeline_definition_service: FdePipelineDefinition
    pipeline_governance_service: FdePipelineGovernance
    pipeline_graph_validation_service: FdePipelineValidation
    source_connection_test_service: FdeSourceConnections
    source_onboarding_service: FdeSourceReader

    def execute(self, ctx: RequestContext, request: FdePlatformToolRequest) -> ToolBrokerResult:
        self.policy.require(ctx, request.spec.required_permission)
        if request.spec.tool_id.startswith("ontology."):
            return self._ontology(ctx, request)
        require_tool_approval(request)
        output = self._dispatch(ctx, request)
        return platform_tool_result(ctx, request, output)

    def _dispatch(self, ctx: RequestContext, request: FdePlatformToolRequest) -> dict[str, object]:
        if request.spec.tool_id == "fde.tools.search":
            return _search_tools(request)
        if request.spec.tool_id == "fde.plan.present":
            return {"operationType": "plan", "status": "presented", **dict(request.arguments)}
        if request.spec.tool_id == "fde.clarification.request":
            return {"operationType": "clarification", "status": "awaiting_user", **dict(request.arguments)}
        if request.spec.tool_id.startswith("pipeline."):
            return self._pipeline(ctx, request)
        if request.spec.tool_id.startswith("source."):
            return self._source(ctx, request)
        if request.spec.tool_id == "function.execute":
            return self._function(ctx, request)
        if request.spec.tool_id == "ml.catalog.inspect":
            return self.pipeline_catalog_service.trained_models(ctx=ctx)
        return self.fde_application_tool_service.execute(ctx, request)

    def _ontology(self, ctx: RequestContext, request: FdePlatformToolRequest) -> ToolBrokerResult:
        branch_id = scope_value(request.scope_ref, "ontology-branch:")
        return self.fde_ontology_tool_service.execute(
            ctx,
            FdeOntologyToolRequest(
                tool_call_id=request.tool_call_id,
                ai_run_id=request.ai_run_id,
                sequence=request.sequence,
                branch_id=branch_id,
                spec=request.spec,
                arguments=request.arguments,
                approved_tool_ids=request.approved_tool_ids,
                max_output_bytes=request.max_output_bytes,
                occurred_at=request.occurred_at,
            ),
        )

    def _pipeline(self, ctx: RequestContext, request: FdePlatformToolRequest) -> dict[str, object]:
        branch_id = scope_value(request.scope_ref, "pipeline-branch:")
        tool_id = request.spec.tool_id
        if tool_id == "pipeline.branch.inspect":
            return {
                "branch": self.pipeline_definition_service.get_branch(branch_id, ctx=ctx),
                "diff": self.pipeline_definition_service.diff_branch(branch_id, ctx=ctx),
            }
        if tool_id == "pipeline.branch.validate":
            return self.pipeline_graph_validation_service.validate_branch(branch_id, ctx=ctx)
        if tool_id == "pipeline.branch.update_graph":
            return self.pipeline_definition_service.update_graph(
                branch_id,
                graph=_mapping(request.arguments, "graph"),
                expected_fingerprint=required_text(request.arguments, "expectedFingerprint"),
                ctx=ctx,
            )
        if tool_id == "pipeline.branch.run_tests":
            return self.pipeline_graph_validation_service.run_tests(branch_id, ctx=ctx)
        if tool_id == "pipeline.branch.propose":
            return self._propose_pipeline(ctx, request, branch_id)
        raise FdePlatformToolError("unknown_fde_tool", f"unsupported pipeline tool {tool_id}")

    def _propose_pipeline(
        self, ctx: RequestContext, request: FdePlatformToolRequest, branch_id: str
    ) -> dict[str, object]:
        return self.pipeline_governance_service.propose_branch(
            branch_id,
            title=required_text(request.arguments, "title"),
            description=_optional_text(request.arguments, "description"),
            idempotency_key=required_text(request.arguments, "idempotencyKey"),
            ctx=ctx,
        )

    def _source(self, ctx: RequestContext, request: FdePlatformToolRequest) -> dict[str, object]:
        source_name = scope_value(request.scope_ref, "source:")
        if request.spec.tool_id == "source.inspect":
            return self.source_onboarding_service.get_source(source_name, ctx=ctx)
        if request.spec.tool_id == "source.connection_history":
            return self._source_history(ctx, request, source_name)
        if request.spec.tool_id == "source.test_connection":
            return self.source_connection_test_service.test_source_connection(
                source_name,
                expected_config_fingerprint=required_text(request.arguments, "expectedConfigFingerprint"),
                idempotency_key=required_text(request.arguments, "idempotencyKey"),
                ctx=ctx,
            )
        raise FdePlatformToolError("unknown_fde_tool", f"unsupported Source tool {request.spec.tool_id}")

    def _source_history(
        self, ctx: RequestContext, request: FdePlatformToolRequest, source_name: str
    ) -> dict[str, object]:
        limit = _bounded_limit(request.arguments.get("limit"))
        return {
            "sourceName": source_name,
            "connectionTests": self.source_connection_test_service.list_source_connection_tests(
                source_name, limit=limit, ctx=ctx
            ),
            "egressAttempts": self.source_connection_test_service.list_source_egress_attempts(
                source_name, limit=limit, ctx=ctx
            ),
        }

    def _function(self, ctx: RequestContext, request: FdePlatformToolRequest) -> dict[str, object]:
        function_name = required_text(request.arguments, "functionApiName")
        if request.scope_ref.startswith("function:") and scope_value(request.scope_ref, "function:") != function_name:
            raise FdePlatformToolError("scope_mismatch", "function tool must match the selected function scope")
        return dict(
            self.function_execution_service.execute_function(
                function_name,
                inputs=_mapping(request.arguments, "inputs"),
                ctx=ctx,
            )
        )


def _search_tools(request: FdePlatformToolRequest) -> dict[str, object]:
    """Return bounded server-catalog matches for lazy tool activation."""
    query = _required_search_query(request.arguments.get("query"))
    max_results = _bounded_results(request.arguments.get("maxResults"))
    matches = _ranked_tool_matches(request.catalog, query, max_results)
    return {
        "query": query,
        "activatedToolIds": [tool.tool_id for tool in matches],
        "tools": [_tool_payload(tool) for tool in matches],
        "count": len(matches),
        "isLazyDiscovery": True,
    }


def _required_search_query(value: object) -> str:
    """Normalize a non-empty lazy-discovery query."""
    query = str(value or "").strip().lower()
    if not query:
        raise FdePlatformToolError("schema_invalid", "query is required")
    return query


def _ranked_tool_matches(catalog: tuple[ToolSpec, ...], query: str, limit: int) -> list[ToolSpec]:
    """Rank catalog tools deterministically and discard zero-score results."""
    terms = tuple(term for term in query.replace(".", " ").replace("_", " ").split() if term)
    candidates = ((_tool_score(tool, terms), tool) for tool in catalog if tool.tool_id != "fde.tools.search")
    ranked = sorted(candidates, key=lambda item: (-item[0], item[1].tool_id))
    return [tool for score, tool in ranked if score > 0][:limit]


def _tool_score(tool: ToolSpec, terms: tuple[str, ...]) -> int:
    """Score exact tool-id matches above description-only matches."""
    tool_id = tool.tool_id.lower()
    text = f"{tool_id} {tool.description.lower()}"
    return sum(3 if term in tool_id else 1 for term in terms if term in text)


def _tool_payload(tool: ToolSpec) -> dict[str, object]:
    """Expose the stable public subset of one server-owned tool."""
    return {
        "toolId": tool.tool_id,
        "version": tool.version,
        "description": tool.description,
        "inputSchema": dict(tool.input_schema),
        "effect": tool.effect,
        "confirmationPolicy": tool.confirmation_policy,
    }


def _bounded_results(value: object) -> int:
    """Validate the lazy-discovery result budget."""
    if value is None:
        return 8
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 20:
        raise FdePlatformToolError("schema_invalid", "maxResults must be between 1 and 20")
    return value


def _mapping(value: Mapping[str, object], key: str) -> dict[str, object]:
    """Read a required object argument with a typed error."""
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise FdePlatformToolError("schema_invalid", f"{key} must be an object")
    return {str(name): field for name, field in item.items()}


def _optional_text(value: Mapping[str, object], key: str) -> str | None:
    """Read and normalize an optional text argument."""
    item = value.get(key)
    return item.strip() if isinstance(item, str) and item.strip() else None


def _bounded_limit(value: object) -> int:
    """Validate Source history pagination bounds."""
    if value is None:
        return 20
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 50:
        raise FdePlatformToolError("schema_invalid", "limit must be between 1 and 50")
    return value
