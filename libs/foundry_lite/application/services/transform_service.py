"""Compatibility entrypoint for the Transform bounded context."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from foundry_lite.application.ports import (
    TransactionContext,
    TransformCheck,
    TransformRecordDlqRetryResult,
    TransformRetryResult,
    TransformRow,
)
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.transform_definition_service import TransformDefinitionService
from foundry_lite.application.services.transform_dlq_replay_service import TransformDlqReplayService
from foundry_lite.application.services.transform_graph import TransformGraphService
from foundry_lite.application.services.transform_run_service import TransformRunService
from foundry_lite.application.services.transform_runs import TransformRunPlan
from foundry_lite.application.services.transform_scheduler_service import TransformSchedulerService
from foundry_lite.domain.context import RequestContext


class TransformService(CoreService):
    """Stable public service entrypoint that delegates to focused use cases."""

    required_dependencies = ()
    required_collaborators = (
        "transform_definition_service",
        "transform_dlq_replay_service",
        "transform_graph_service",
        "transform_run_service",
        "transform_scheduler_service",
    )
    transform_definition_service: TransformDefinitionService
    transform_dlq_replay_service: TransformDlqReplayService
    transform_graph_service: TransformGraphService
    transform_run_service: TransformRunService
    transform_scheduler_service: TransformSchedulerService

    def register_transform(
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
        return self.transform_definition_service.register_transform(
            api_name,
            entrypoint=entrypoint,
            inputs=inputs,
            output_dataset_ref=output_dataset_ref,
            checks=checks,
            ctx=ctx,
            mode=mode,
            language=language,
        )

    def register_sql_transform(
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
        return self.transform_definition_service.register_sql_transform(
            api_name,
            sql=sql,
            inputs=inputs,
            output_dataset_ref=output_dataset_ref,
            checks=checks,
            ctx=ctx,
            mode=mode,
        )

    def register_python_transform(
        self,
        api_name: str,
        *,
        source_code: str,
        function_name: str | None,
        inputs: Mapping[str, str],
        output_dataset_ref: str,
        checks: Sequence[TransformCheck] | None = None,
        ctx: RequestContext | None = None,
        mode: str = "snapshot",
    ) -> TransformRow:
        return self.transform_definition_service.register_python_transform(
            api_name,
            source_code=source_code,
            function_name=function_name,
            inputs=inputs,
            output_dataset_ref=output_dataset_ref,
            checks=checks,
            ctx=ctx,
            mode=mode,
        )

    def run_transform(self, api_name: str, *, ctx: RequestContext | None = None) -> CommitResult:
        return self.transform_run_service.run_transform(api_name, ctx=ctx)

    def _prepare_pipeline_transform_run(
        self,
        ctx: RequestContext,
        api_name: str,
    ) -> TransformRunPlan:
        return self.transform_run_service._prepare_pipeline_transform_run(ctx, api_name)

    def _execute_pipeline_transform_run(
        self,
        ctx: RequestContext,
        plan: TransformRunPlan,
        *,
        after_transform_commit: Callable[[TransactionContext, CommitResult], None] | None = None,
    ) -> CommitResult:
        return self.transform_run_service._execute_pipeline_transform_run(
            ctx,
            plan,
            after_transform_commit=after_transform_commit,
        )

    def _abort_pipeline_transform_run(
        self,
        ctx: RequestContext,
        plan: TransformRunPlan,
        exc: Exception,
    ) -> None:
        self.transform_run_service._abort_pipeline_transform_run(ctx, plan, exc)

    def run_transform_graph(
        self,
        api_name: str,
        *,
        ctx: RequestContext | None = None,
        max_depth: int = 7,
        max_runs: int = 50,
    ) -> dict[str, object]:
        return self.transform_graph_service.run_transform_graph(
            api_name,
            ctx=ctx,
            max_depth=max_depth,
            max_runs=max_runs,
        )

    def preview_due_transform_runs(
        self,
        *,
        ctx: RequestContext | None = None,
        max_runs: int = 50,
    ) -> dict[str, object]:
        return self.transform_scheduler_service.preview_due_transform_runs(ctx=ctx, max_runs=max_runs)

    def run_due_transform_runs(
        self,
        *,
        ctx: RequestContext | None = None,
        max_runs: int = 50,
    ) -> dict[str, object]:
        return self.transform_scheduler_service.run_due_transform_runs(ctx=ctx, max_runs=max_runs)

    def retry_transform_run(
        self,
        transform_run_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> TransformRetryResult:
        return self.transform_run_service.retry_transform_run(transform_run_id, ctx=ctx)

    def retry_transform_dead_letter_record(
        self,
        record_id: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> TransformRecordDlqRetryResult:
        return self.transform_dlq_replay_service.retry_transform_dead_letter_record(
            record_id,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )
