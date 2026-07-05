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
    output_dataset_ref,
    validate_pipeline_graph,
)
from foundry_lite.application.services.pipeline_payloads import test_result_record
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed


class PipelineGraphValidationService(CoreService):
    """Read-only graph analysis used by the frontend canvas."""

    required_dependencies = ("engine", "policy", "pipeline_repository")
    required_collaborators = ("runtime_service",)
    pipeline_repository: PipelineRepository
    runtime_service: RuntimeEvidenceBoundary

    def validate_branch(self, branch_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        row = self._branch(branch_id, ctx)
        return validate_pipeline_graph(row["graph"])

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

    def _branch(self, branch_id: str, ctx: RequestContext) -> PipelineBranchRow:
        with self.engine.begin() as conn:
            return self._require_branch(conn, ctx, branch_id)

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
    validation = validate_pipeline_graph(row["graph"])
    tests = row["graph"].get("tests")
    if not isinstance(tests, list):
        tests = []
    failures = [] if validation["valid"] else [{"test": "graph_validation", "errors": validation["errors"]}]
    failures.extend(_output_contract_test_failures(row["graph"]))
    status = "passed" if not failures else "failed"
    return {"status": status, "testCount": len(tests) + 1, "failures": failures, "validation": validation}


def _output_contract_test_failures(graph: Mapping[str, object]) -> list[dict[str, object]]:
    output_ref = output_dataset_ref(graph)
    if output_ref is None:
        return [{"test": "output_dataset", "message": "output dataset reference is missing"}]
    columns = output_contract_columns(graph)
    if not columns:
        return [{"test": "output_contract", "message": "output contract has no columns"}]
    if any(not column.get("type") for column in columns):
        raise ValidationFailed("output contract column type is required", details={"outputDatasetRef": output_ref})
    return []
