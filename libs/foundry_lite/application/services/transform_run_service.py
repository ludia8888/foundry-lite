"""Transform run and retry use cases."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast

from foundry_lite.application.ports import (
    ComputeAdapter,
    DatasetTransactionRepository,
    DatasetTransactionRow,
    PythonTransformPlan,
    SqlTransformPlan,
    TransactionContext,
    TransformExecutionResult,
    TransformRetryResult,
)
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.transform_protocols import (
    TransformDatasetRegistry,
    TransformDatasetTransactions,
    TransformDatasetVersions,
    TransformRuntimeBoundary,
    require_transform_write_open,
)
from foundry_lite.application.services.transform_record_dlq import (
    persist_transform_dead_letters,
    transform_dead_letter_summary,
)
from foundry_lite.application.services.transform_runs import (
    TransformRunPlan,
    infer_sql_partition_filters,
    mark_transform_run_succeeded,
    start_failed_transform_retry,
    start_transform_run,
    transform_input_paths_by_ref,
    validate_sql_transform_uses_declared_inputs,
)
from foundry_lite.application.services.transform_watermarks import transform_output_platform_watermark
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    DatasetCommitBlocked,
    InvariantViolation,
    NotFound,
    ValidationFailed,
)

TransformCommitHook = Callable[[TransactionContext, CommitResult], None]


class TransformRunService(CoreService):
    """Open, execute, finalize, and retry transform runs."""

    required_dependencies = ("engine", "compute_adapter", "dataset_transaction_repository", "transform_repository")
    required_collaborators = (
        "dataset_registry_service",
        "dataset_transaction_service",
        "dataset_version_service",
        "runtime_service",
    )
    compute_adapter: ComputeAdapter
    dataset_registry_service: TransformDatasetRegistry
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_transaction_service: TransformDatasetTransactions
    dataset_version_service: TransformDatasetVersions
    runtime_service: TransformRuntimeBoundary

    def run_transform(self, api_name: str, *, ctx: RequestContext | None = None) -> CommitResult:
        ctx = ctx or RequestContext()
        plan = self._prepare_pipeline_transform_run(ctx, api_name)
        result = self._execute_pipeline_transform_run(ctx, plan)
        return result

    def run_transform_internal(self, ctx: RequestContext, api_name: str) -> tuple[CommitResult, TransformRunPlan]:
        plan = self._prepare_pipeline_transform_run(ctx, api_name)
        result = self._execute_pipeline_transform_run(ctx, plan)
        return result, plan

    def _prepare_pipeline_transform_run(self, ctx: RequestContext, api_name: str) -> TransformRunPlan:
        self.runtime_service._require_or_audit(ctx, "transform:run", "transform", api_name)
        require_transform_write_open(self.runtime_service, ctx, "run_transform", "transform", api_name)
        return self._start_transform_run(ctx, api_name)

    def _execute_pipeline_transform_run(
        self,
        ctx: RequestContext,
        plan: TransformRunPlan,
        *,
        after_transform_commit: TransformCommitHook | None = None,
    ) -> CommitResult:
        staged = self.dataset_transaction_service._staging_file(
            plan.output_dataset, plan.transaction_id, "part-00000.parquet"
        )
        return self.execute_and_finalize_plan(
            ctx,
            plan,
            staged,
            after_transform_commit=after_transform_commit,
        )

    def _abort_pipeline_transform_run(
        self,
        ctx: RequestContext,
        plan: TransformRunPlan,
        exc: Exception,
    ) -> None:
        self.abort_transform_run(ctx, plan, exc)

    def retry_transform_run(
        self,
        transform_run_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> TransformRetryResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "operations:retry", "transform_run", transform_run_id)
        require_transform_write_open(
            self.runtime_service,
            ctx,
            "retry_transform_run",
            "transform_run",
            transform_run_id,
        )
        plan = self._start_failed_transform_retry(ctx, transform_run_id)
        staged = self.dataset_transaction_service._staging_file(
            plan.output_dataset, plan.transaction_id, "part-00000.parquet"
        )
        result = self.execute_and_finalize_plan(ctx, plan, staged, should_audit_retry=True)
        return _transform_retry_response(plan, result)

    def execute_and_finalize_plan(
        self,
        ctx: RequestContext,
        plan: TransformRunPlan,
        staged: Path,
        *,
        should_audit_retry: bool = False,
        after_transform_commit: TransformCommitHook | None = None,
    ) -> CommitResult:
        try:
            execution = self.execute_transform_plan(plan, staged)
            dead_letter_ids = self.persist_transform_dead_letters(ctx, plan, execution)
            return self.finalize_executed_transform(
                ctx,
                plan,
                staged,
                execution,
                dead_letter_ids,
                should_audit_retry=should_audit_retry,
                after_transform_commit=after_transform_commit,
            )
        except Exception as exc:
            self.abort_transform_run(ctx, plan, exc)
            if isinstance(exc, ValidationFailed | NotFound | ConflictDetected | InvariantViolation):
                raise
            raise ValidationFailed("transform failed", details={"error": str(exc)}) from exc

    def execute_transform_plan(self, plan: TransformRunPlan, staged: Path) -> TransformExecutionResult:
        if plan.language == "python":
            return self._execute_python_transform(plan, staged)
        return self._execute_sql_transform(plan, staged)

    def persist_transform_dead_letters(
        self,
        ctx: RequestContext,
        plan: TransformRunPlan,
        execution: TransformExecutionResult,
    ) -> tuple[str, ...]:
        if not execution.dead_letters:
            return ()
        with self.engine.begin() as conn:
            return persist_transform_dead_letters(
                repository=self.dataset_transaction_repository,
                runtime_service=self.runtime_service,
                conn=conn,
                ctx=ctx,
                plan=plan,
                execution=execution,
            )

    def finalize_executed_transform(
        self,
        ctx: RequestContext,
        plan: TransformRunPlan,
        staged: Path,
        execution: TransformExecutionResult,
        dead_letter_ids: Sequence[str],
        *,
        should_audit_retry: bool = False,
        after_transform_commit: TransformCommitHook | None = None,
    ) -> CommitResult:
        blocked: DatasetCommitBlocked | None = None
        with self.engine.begin() as conn:
            try:
                return self._finalize_transform_run(
                    conn,
                    ctx,
                    plan,
                    staged,
                    execution,
                    dead_letter_ids,
                    should_audit_retry=should_audit_retry,
                    after_transform_commit=after_transform_commit,
                )
            except DatasetCommitBlocked as exc:
                blocked = exc
        if blocked is not None:
            raise blocked
        raise InvariantViolation("transform finalization did not return a commit result")

    def abort_transform_run(
        self,
        ctx: RequestContext,
        plan: TransformRunPlan,
        exc: Exception,
    ) -> None:
        self.dataset_transaction_service._abort_transaction_after_error(
            ctx,
            plan.transaction_id,
            plan.run_id,
            exc,
            "transform",
            adapter="compute_adapter.execute_transform",
        )

    def _start_transform_run(self, ctx: RequestContext, api_name: str) -> TransformRunPlan:
        with self.engine.begin() as conn:
            return start_transform_run(
                conn=conn,
                ctx=ctx,
                api_name=api_name,
                dataset_registry_service=self.dataset_registry_service,
                dataset_transaction_service=self.dataset_transaction_service,
                dataset_version_service=self.dataset_version_service,
                transform_repository=self.transform_repository,
            )

    def _start_failed_transform_retry(self, ctx: RequestContext, transform_run_id: str) -> TransformRunPlan:
        with self.engine.begin() as conn:
            return start_failed_transform_retry(
                conn=conn,
                ctx=ctx,
                transform_run_id=transform_run_id,
                dataset_registry_service=self.dataset_registry_service,
                dataset_transaction_service=self.dataset_transaction_service,
                transform_repository=self.transform_repository,
            )

    def _execute_sql_transform(self, plan: TransformRunPlan, staged: Path) -> TransformExecutionResult:
        validate_sql_transform_uses_declared_inputs(plan.sql_template)
        input_paths_by_ref = transform_input_paths_by_ref(
            plan,
            dataset_transaction_service=self.dataset_transaction_service,
            dataset_version_service=self.dataset_version_service,
            partition_filters=infer_sql_partition_filters(plan.sql_template),
        )
        execution = self.compute_adapter.execute_transform(
            SqlTransformPlan(
                sql_template=plan.sql_template,
                input_paths_by_ref=input_paths_by_ref,
                target_path=staged,
            )
        )
        return _execution_result(execution)

    def _execute_python_transform(self, plan: TransformRunPlan, staged: Path) -> TransformExecutionResult:
        if plan.python_source is None:
            raise ValidationFailed("Python transform source is missing", details={"entrypoint": plan.entrypoint})
        input_paths_by_ref = transform_input_paths_by_ref(
            plan,
            dataset_transaction_service=self.dataset_transaction_service,
            dataset_version_service=self.dataset_version_service,
            partition_filters={},
        )
        execution = self.compute_adapter.execute_transform(
            PythonTransformPlan(
                entrypoint=plan.entrypoint,
                source_code=plan.python_source,
                function_name=plan.python_function,
                input_refs_by_alias=_snapshot_inputs(plan),
                input_paths_by_ref=input_paths_by_ref,
                output_dataset_ref=str(plan.definition_snapshot["output_dataset_ref"]),
                target_path=staged,
            )
        )
        return _execution_result(execution)

    def _finalize_transform_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: TransformRunPlan,
        staged: Path,
        execution: TransformExecutionResult,
        dead_letter_ids: Sequence[str],
        *,
        should_audit_retry: bool = False,
        after_transform_commit: TransformCommitHook | None = None,
    ) -> CommitResult:
        return self.dataset_transaction_service._finalize_open_transaction(
            conn,
            ctx,
            dataset=plan.output_dataset,
            transaction_id=plan.transaction_id,
            staged_parquet=staged,
            run_id=plan.run_id,
            audit_action="transform_output_commit",
            outbox_event_type="dataset.version.committed",
            extra_checks=plan.checks,
            transaction_metadata=self._transform_transaction_metadata(conn, ctx, plan, execution, dead_letter_ids),
            after_persist=self._after_persist_hook(ctx, plan, should_audit_retry, after_transform_commit),
        )

    def _after_persist_hook(
        self,
        ctx: RequestContext,
        plan: TransformRunPlan,
        should_audit_retry: bool,
        after_transform_commit: TransformCommitHook | None,
    ) -> TransformCommitHook:
        def after_persist(conn: TransactionContext, result: CommitResult) -> None:
            self._record_transform_lineage(conn, ctx, plan, result)
            mark_transform_run_succeeded(self.transform_repository, conn, ctx, plan.run_id, result)
            if should_audit_retry:
                self._audit_transform_retry(conn, ctx, plan, result)
            if after_transform_commit is not None:
                after_transform_commit(conn, result)

        return after_persist

    def _transform_transaction_metadata(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: TransformRunPlan,
        execution: TransformExecutionResult,
        dead_letter_ids: Sequence[str],
    ) -> Mapping[str, object] | None:
        metadata: dict[str, object] = {}
        if execution.dead_letters:
            metadata.update(transform_dead_letter_summary(execution, dead_letter_ids))
        watermark = transform_output_platform_watermark(
            output_dataset=plan.output_dataset,
            input_versions=plan.input_versions,
            input_transactions=self._input_transactions_by_ref(conn, ctx, plan),
        )
        if watermark is not None:
            metadata["platformWatermark"] = watermark
        return metadata or None

    def _input_transactions_by_ref(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: TransformRunPlan,
    ) -> Mapping[str, DatasetTransactionRow | None]:
        return {
            dataset_ref: self.dataset_transaction_repository.committed_transaction_by_version(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                committed_version_id=version_id,
            )
            for dataset_ref, version_id in plan.input_versions.items()
        }

    def _record_transform_lineage(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: TransformRunPlan,
        result: CommitResult,
    ) -> None:
        for version_id in plan.input_versions.values():
            self.runtime_service._lineage(
                conn,
                ctx,
                "dataset_version",
                version_id,
                "dataset_version",
                result.version_id,
                "input_to",
                plan.run_id,
            )

    def _audit_transform_retry(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        plan: TransformRunPlan,
        result: CommitResult,
    ) -> None:
        if plan.retry_of_run_id is None:
            return
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="transform.run.retry_requested",
            resource_type="transform_run",
            resource_id=plan.run_id,
            action="operations:retry",
            before_ref={"transform_run_id": plan.retry_of_run_id},
            after_ref={"transform_run_id": plan.run_id, "output_version_id": result.version_id},
            correlation_id=plan.run_id,
        )
        self.runtime_service._run_relation(
            conn,
            ctx,
            source_run_type="transform",
            source_run_id=plan.retry_of_run_id,
            target_run_type="transform",
            target_run_id=plan.run_id,
            relation="retry_of",
            resource_type="transform",
            resource_id=plan.transform_id,
            metadata={"outputVersionId": result.version_id},
        )


def _snapshot_inputs(plan: TransformRunPlan) -> dict[str, str]:
    return dict(cast(dict[str, str], plan.definition_snapshot["inputs"]))


def _execution_result(result: TransformExecutionResult | None) -> TransformExecutionResult:
    if result is None:
        return TransformExecutionResult()
    return result


def _transform_retry_response(plan: TransformRunPlan, result: CommitResult) -> TransformRetryResult:
    return {
        "original_run_id": plan.retry_of_run_id or "",
        "transform_run_id": plan.run_id,
        "dataset_id": result.dataset_id,
        "dataset_ref": result.dataset_ref,
        "transaction_id": result.transaction_id,
        "version_id": result.version_id,
        "version_number": result.version_number,
        "row_count": result.row_count,
        "manifest_uri": result.manifest_uri,
        "schema_hash": result.schema_hash,
    }
