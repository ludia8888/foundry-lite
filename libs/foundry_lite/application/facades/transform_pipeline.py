"""Thin facade entrypoints for transform pipeline workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from foundry_lite.application.ports import (
    TransformCheck,
    TransformRecordDlqRetryResult,
    TransformRetryResult,
    TransformRow,
)
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.transform_service import TransformService
from foundry_lite.domain.context import RequestContext
from foundry_lite.observability.tracing import trace_public_methods


@trace_public_methods
class TransformPipeline:
    """Transform bounded context: register, run, and retry SQL transforms."""

    def __init__(self, transform: TransformService) -> None:
        self._transform = transform

    def register(
        self,
        api_name: str,
        *,
        entrypoint: str | Path,
        inputs: Mapping[str, str],
        output_dataset_ref: str,
        checks: Sequence[TransformCheck] | None = None,
        ctx: RequestContext | None = None,
        mode: str = "snapshot",
        language: str = "sql",
    ) -> TransformRow:
        return self._transform.register_transform(
            api_name,
            entrypoint=entrypoint,
            inputs=inputs,
            output_dataset_ref=output_dataset_ref,
            checks=checks,
            ctx=ctx,
            mode=mode,
            language=language,
        )

    def register_sql(
        self,
        api_name: str,
        *,
        sql: str,
        inputs: Mapping[str, str],
        output_dataset_ref: str,
        checks: Sequence[TransformCheck] | None = None,
        ctx: RequestContext | None = None,
        mode: str = "snapshot",
    ) -> TransformRow:
        return self._transform.register_sql_transform(
            api_name,
            sql=sql,
            inputs=inputs,
            output_dataset_ref=output_dataset_ref,
            checks=checks,
            ctx=ctx,
            mode=mode,
        )

    def run(self, api_name: str, *, ctx: RequestContext | None = None) -> CommitResult:
        return self._transform.run_transform(api_name, ctx=ctx)

    def run_graph(
        self,
        api_name: str,
        *,
        ctx: RequestContext | None = None,
        max_depth: int = 7,
        max_runs: int = 50,
    ) -> dict[str, object]:
        return self._transform.run_transform_graph(api_name, ctx=ctx, max_depth=max_depth, max_runs=max_runs)

    def preview_due(
        self,
        *,
        ctx: RequestContext | None = None,
        max_runs: int = 50,
    ) -> dict[str, object]:
        return self._transform.preview_due_transform_runs(ctx=ctx, max_runs=max_runs)

    def run_due(
        self,
        *,
        ctx: RequestContext | None = None,
        max_runs: int = 50,
    ) -> dict[str, object]:
        return self._transform.run_due_transform_runs(ctx=ctx, max_runs=max_runs)

    def retry_run(self, transform_run_id: str, *, ctx: RequestContext | None = None) -> TransformRetryResult:
        return self._transform.retry_transform_run(transform_run_id, ctx=ctx)

    def retry_dead_letter_record(
        self,
        record_id: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> TransformRecordDlqRetryResult:
        return self._transform.retry_transform_dead_letter_record(
            record_id,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )
