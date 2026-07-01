"""Thin facade entrypoints for materialization runner workflows."""

from __future__ import annotations

from foundry_lite.application.ports import MaterializationReplayResult
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.materialization_service import MaterializationService
from foundry_lite.domain.context import RequestContext
from foundry_lite.observability.tracing import trace_public_methods


@trace_public_methods
class MaterializationRunner:
    """Materialization bounded context: run materializations and replay their rows."""

    def __init__(self, materialization: MaterializationService) -> None:
        self._materialization = materialization

    def run(self, api_name: str, *, ctx: RequestContext | None = None) -> CommitResult:
        return self._materialization.materialize(api_name, ctx=ctx)

    def replay_rows(
        self, materialization_run_id: str, *, ctx: RequestContext | None = None
    ) -> MaterializationReplayResult:
        return self._materialization.replay_rows_for_run(materialization_run_id, ctx=ctx)
