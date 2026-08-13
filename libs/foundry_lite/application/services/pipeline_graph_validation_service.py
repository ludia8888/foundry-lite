"""Pipeline Builder graph validation, cast hints, preview metadata, and tests."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.pipeline_repository import PipelineBranchRow, PipelineRepository
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.pipeline_graph_model import (
    bounded_preview_limit,
    node_by_id,
    node_data,
    output_contract_columns,
    validate_pipeline_graph,
)
from foundry_lite.application.services.pipeline_payloads import test_result_payload, test_result_record
from foundry_lite.application.services.pipeline_source_contract_resolver import (
    PipelineSourceContractResolutionFailed,
    PipelineSourceContractResolver,
)
from foundry_lite.application.services.pipeline_source_contracts import PipelineSourceResolution
from foundry_lite.application.services.pipeline_source_validation import (
    validate_pipeline_graph_with_sources,
    validation_with_source_failure,
)
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed


class PipelineGraphValidationService(CoreService):
    """Read-only graph analysis used by the frontend canvas."""

    required_dependencies = (
        "engine",
        "policy",
        "pipeline_repository",
        "dataset_repository",
        "dataset_version_repository",
        "dataset_quality_repository",
        "media_repository",
        "source_management_repository",
    )
    required_collaborators = ("runtime_service",)
    pipeline_repository: PipelineRepository
    runtime_service: RuntimeEvidenceBoundary

    def validate_branch(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        row = self._branch(branch_id, ctx)
        if _has_dataset_source(row["graph"]):
            self.policy.require(ctx, "dataset:read")
        try:
            resolution = self._resolve_sources(row["graph"], ctx)
        except PipelineSourceContractResolutionFailed as exc:
            return validation_with_source_failure(row["graph"], exc)
        return validate_pipeline_graph_with_sources(row["graph"], resolution)

    def suggest_casts(
        self,
        branch_id: str,
        node_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        row = self._branch(branch_id, ctx)
        node = node_by_id(row["graph"], node_id)
        columns = _columns_from_node_or_contract(node, row["graph"])
        return {"branchId": branch_id, "nodeId": node_id, "suggestions": _cast_suggestions(columns)}

    def preview_node(
        self,
        branch_id: str,
        node_id: str,
        *,
        options: Mapping[str, object] | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        row = self._branch(branch_id, ctx)
        node = node_by_id(row["graph"], node_id)
        limit = bounded_preview_limit((options or {}).get("limit"))
        return {
            "branchId": branch_id,
            "nodeId": node_id,
            "limit": limit,
            "schema": _columns_from_node_or_contract(node, row["graph"]),
            "rows": [],
            "noCommit": True,
        }

    def node_stats(
        self,
        branch_id: str,
        node_id: str,
        *,
        options: Mapping[str, object] | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        row = self._branch(branch_id, ctx)
        node = node_by_id(row["graph"], node_id)
        columns = _columns_from_node_or_contract(node, row["graph"])
        return {
            "branchId": branch_id,
            "nodeId": node_id,
            "columnCount": len(columns),
            "rowCount": None,
            "bounded": True,
            "options": dict(options or {}),
        }

    def run_tests(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "pipeline:write", "pipeline_branch", branch_id)
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="run_pipeline_tests",
            resource_type="pipeline_branch",
            resource_id=branch_id,
        )
        row = self._branch(branch_id, ctx)
        result = _test_result(row)
        with self.engine.begin() as conn:
            stored = self.pipeline_repository.insert_test_result(
                transaction=conn,
                record=test_result_record(
                    ctx,
                    pipeline_id=str(row["pipeline_id"]),
                    branch_id=branch_id,
                    status=str(result["status"]),
                    result=result,
                    now=_now(),
                ),
            )
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="pipeline.branch.tests_run",
                resource_type="pipeline_branch",
                resource_id=branch_id,
                action="tests_run",
                after_ref={
                    "pipeline_id": row["pipeline_id"],
                    "test_result_id": stored["id"],
                    "status": stored["status"],
                },
            )
        return {"id": stored["id"], **result}

    def latest_test_result(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object] | None:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        with self.engine.begin() as conn:
            row = self.pipeline_repository.latest_test_result(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                branch_id=branch_id,
            )
        return test_result_payload(row) if row is not None else None

    def _branch(self, branch_id: str, ctx: RequestContext) -> PipelineBranchRow:
        with self.engine.begin() as conn:
            return self._require_branch(conn, ctx, branch_id)

    def _resolve_sources(
        self,
        graph: Mapping[str, object],
        ctx: RequestContext,
    ) -> PipelineSourceResolution:
        resolver = PipelineSourceContractResolver(
            dataset_repository=self.dataset_repository,
            dataset_version_repository=self.dataset_version_repository,
            dataset_quality_repository=self.dataset_quality_repository,
            media_repository=self.media_repository,
            source_management_repository=self.source_management_repository,
        )
        with self.engine.begin() as conn:
            return resolver.resolve(transaction=conn, graph=graph, ctx=ctx)

    def _require_branch(self, conn: TransactionContext, ctx: RequestContext, branch_id: str) -> PipelineBranchRow:
        row = self.pipeline_repository.branch_by_id(transaction=conn, tenant_id=ctx.tenant_id, branch_id=branch_id)
        if row is None:
            raise NotFound("pipeline branch not found", details={"branch_id": branch_id})
        return row


def _columns_from_node_or_contract(node: Mapping[str, object], graph: Mapping[str, object]) -> list[dict[str, object]]:
    data = node_data(node)
    schema = data.get("schema")
    if isinstance(schema, list):
        return [dict(column) for column in schema if isinstance(column, dict)]
    return output_contract_columns(graph)


def _has_dataset_source(graph: Mapping[str, object]) -> bool:
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return False
    return any(
        isinstance(node, Mapping)
        and (
            node.get("descriptorId") in {"source.dataset", "source.stream", "source.geospatial"}
            or node.get("type") == "dataset"
        )
        for node in nodes
    )


def _cast_suggestions(columns: list[dict[str, object]]) -> list[dict[str, object]]:
    suggestions: list[dict[str, object]] = []
    for column in columns:
        declared = str(column.get("type") or "").lower()
        name = str(column.get("name") or "")
        suggestion = _suggested_type(name, declared)
        if suggestion is not None:
            suggestions.append({"column": name, "from": declared, "to": suggestion, "confidence": "medium"})
    return suggestions


def _suggested_type(name: str, declared: str) -> str | None:
    lowered = name.lower()
    if declared in {"", "string"} and lowered.endswith(("_at", "_date", "_time")):
        return "timestamp"
    if declared in {"", "string"} and lowered.endswith(("_id", "_count", "_qty")):
        return "integer"
    if declared in {"", "string"} and lowered.endswith(("_amount", "_price", "_rate")):
        return "float"
    return None


def _test_result(row: PipelineBranchRow) -> dict[str, object]:
    graph = dict(row["graph"])
    tests = graph.get("tests")
    if not isinstance(tests, list):
        tests = []
        graph["tests"] = tests
    validation = validate_pipeline_graph(graph)
    failures = [] if validation["valid"] else [{"test": "graph_validation", "errors": validation["errors"]}]
    failures.extend(_output_contract_test_failures(graph))
    status = "passed" if not failures else "failed"
    return {
        "status": status,
        "graphFingerprint": row["graph_fingerprint"],
        "testCount": len(tests) + 1,
        "declaredTestCount": len(tests),
        "proofKind": "static_graph_output_contract",
        "proofVersion": "pipeline-static-review-v1",
        "isDataExecution": False,
        "evaluatedChecks": ["graph_validation", "output_descriptor_contracts"],
        "failures": failures,
        "validation": validation,
    }


def _output_contract_test_failures(graph: Mapping[str, object]) -> list[dict[str, object]]:
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return []
    dataset_outputs = [
        node
        for node in nodes
        if isinstance(node, Mapping)
        and (node.get("type") == "output_dataset" or node.get("descriptorId") == "output.dataset")
    ]
    failures: list[dict[str, object]] = [
        {"test": "output_dataset", "message": "output dataset reference is missing"}
        for node in dataset_outputs
        if not _output_dataset_ref(node)
    ]
    columns = output_contract_columns(graph)
    if any(not column.get("type") for column in columns):
        raise ValidationFailed("output contract column type is required")
    return failures


def _output_dataset_ref(node: Mapping[str, object]) -> str | None:
    config = node_data(node)
    value = config.get("outputDatasetRef") or config.get("datasetRef")
    return value if isinstance(value, str) and value.strip() else None
