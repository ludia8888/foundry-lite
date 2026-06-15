from __future__ import annotations

from foundry_lite.application.core_retry import retry_materialization_name, retry_materialization_result
from foundry_lite.application.ports import (
    LineageEdgeRow,
    RuntimeRetryResult,
    RuntimeRunDetail,
    RuntimeRunQueryResult,
    RuntimeRunSnapshot,
)
from foundry_lite.application.services.materialization_service import MaterializationService
from foundry_lite.application.services.runtime_service import RuntimeService
from foundry_lite.domain.context import RequestContext
from foundry_lite.observability.tracing import trace_public_methods


@trace_public_methods
class OperationsConsole:
    """Operations bounded context: run inspection, lineage, retries, and DLQ reprocessing."""

    def __init__(self, runtime: RuntimeService, materialization: MaterializationService) -> None:
        self._runtime = runtime
        self._materialization = materialization

    def lineage(self, resource_id: str, *, ctx: RequestContext | None = None) -> list[LineageEdgeRow]:
        return self._runtime.lineage_for_resource(resource_id, ctx=ctx)

    def list_runs(self, *, ctx: RequestContext | None = None) -> RuntimeRunSnapshot:
        return self._runtime.list_runs(ctx=ctx)

    def query_runs(
        self,
        *,
        ctx: RequestContext | None = None,
        run_type: str | None = None,
        status: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> RuntimeRunQueryResult:
        return self._runtime.query_runs(
            ctx=ctx,
            run_type=run_type,
            status=status,
            since=since,
            until=until,
            limit=limit,
            cursor=cursor,
        )

    def run_detail(self, run_type: str, run_id: str, *, ctx: RequestContext | None = None) -> RuntimeRunDetail:
        return self._runtime.run_detail(run_type, run_id, ctx=ctx)

    def retry_dead_letter_event(self, event_id: str, *, ctx: RequestContext | None = None) -> RuntimeRetryResult:
        ctx = ctx or RequestContext()
        plan = self._runtime.dead_letter_event_retry_plan(event_id, ctx=ctx)
        materialization_name = retry_materialization_name(plan)
        materialization_result = None
        if materialization_name is not None:
            commit = self._materialization.materialize(materialization_name, ctx=ctx)
            materialization_result = retry_materialization_result(materialization_name, commit)
        result = self._runtime.retry_dead_letter_event(event_id, ctx=ctx)
        if materialization_result is not None:
            result["materializationResult"] = materialization_result
        return result
