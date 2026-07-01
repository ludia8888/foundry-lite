"""Application service helpers for transform execution mixin workflows."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from foundry_lite.application.ports import (
    ComputeAdapter,
    DatasetTransactionRepository,
    PythonTransformPlan,
    SqlTransformPlan,
    TransactionManager,
    TransformExecutionResult,
)
from foundry_lite.application.services.transform_protocols import (
    TransformDatasetTransactions,
    TransformDatasetVersions,
    TransformRuntimeBoundary,
)
from foundry_lite.application.services.transform_record_dlq import persist_transform_dead_letters
from foundry_lite.application.services.transform_runs import (
    TransformRunPlan,
    infer_sql_partition_filters,
    transform_input_paths_by_ref,
    validate_sql_transform_uses_declared_inputs,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


class TransformExecutionMixin:
    compute_adapter: ComputeAdapter
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_transaction_service: TransformDatasetTransactions
    dataset_version_service: TransformDatasetVersions
    engine: TransactionManager
    runtime_service: TransformRuntimeBoundary

    def _execute_transform_plan(self, plan: TransformRunPlan, staged: Path) -> TransformExecutionResult:
        if plan.language == "python":
            return self._execute_python_transform(plan, staged)
        return self._execute_sql_transform(plan, staged)

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

    def _persist_transform_dead_letters(
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


def _snapshot_inputs(plan: TransformRunPlan) -> dict[str, str]:
    return dict(cast(dict[str, str], plan.definition_snapshot["inputs"]))


def _execution_result(result: TransformExecutionResult | None) -> TransformExecutionResult:
    if result is None:
        return TransformExecutionResult()
    return result
