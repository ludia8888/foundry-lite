"""Application service helpers for transform graph workflows."""

from __future__ import annotations

from foundry_lite.application.ports import TransactionContext, TransactionManager, TransformRepository, TransformRow
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.transform_protocols import TransformRuntimeBoundary
from foundry_lite.application.services.transform_protocols import (
    require_transform_write_open as require_write_open,
)
from foundry_lite.application.services.transform_runs import TransformRunPlan
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


class TransformGraphMixin:
    """Bounded downstream transform graph trigger behavior."""

    engine: TransactionManager
    transform_repository: TransformRepository
    runtime_service: TransformRuntimeBoundary

    def _run_transform_internal(self, ctx: RequestContext, api_name: str) -> tuple[CommitResult, TransformRunPlan]:
        """Run one transform through the concrete transform service."""
        raise NotImplementedError

    def run_transform_graph(
        self,
        api_name: str,
        *,
        ctx: RequestContext | None = None,
        max_depth: int = 7,
        max_runs: int = 50,
    ) -> dict[str, object]:
        """Run one transform and its downstream graph within explicit bounds."""
        ctx = ctx or RequestContext()
        if max_depth < 1 or max_depth > 7 or max_runs < 1 or max_runs > 50:
            raise ValidationFailed("transform graph bounds must be depth 1-7 and runs 1-50")
        root_result, root_plan = self._run_transform_for_graph(ctx, api_name)
        downstream: list[dict[str, object]] = []
        self._run_downstream_graph(ctx, root_plan, root_result, max_depth, max_runs, downstream, {api_name})
        return {"root": _commit_payload(root_result, root_plan), "downstream": downstream}

    def _run_transform_for_graph(self, ctx: RequestContext, api_name: str) -> tuple[CommitResult, TransformRunPlan]:
        """Apply authorization and write guards before graph-owned execution."""
        self.runtime_service._require_or_audit(ctx, "transform:run", "transform", api_name)
        require_write_open(self.runtime_service, ctx, "run_transform_graph", "transform", api_name)
        return self._run_transform_internal(ctx, api_name)

    def _run_downstream_graph(
        self,
        ctx: RequestContext,
        parent_plan: TransformRunPlan,
        parent_result: CommitResult,
        depth_remaining: int,
        max_runs: int,
        downstream: list[dict[str, object]],
        visited: set[str],
    ) -> None:
        """Depth-first downstream traversal with a hard run-count cap."""
        if depth_remaining <= 1 or len(downstream) >= max_runs:
            return
        for transform in self._downstream_transforms(ctx, parent_result.dataset_ref):
            child = self._run_downstream_transform(
                ctx,
                transform,
                parent_plan,
                parent_result,
                max_runs,
                downstream,
                visited,
            )
            if child is None:
                continue
            child_result, child_plan = child
            downstream.append(_commit_payload(child_result, child_plan))
            self._run_downstream_graph(
                ctx,
                child_plan,
                child_result,
                depth_remaining - 1,
                max_runs,
                downstream,
                visited,
            )

    def _run_downstream_transform(
        self,
        ctx: RequestContext,
        transform: TransformRow,
        parent_plan: TransformRunPlan,
        parent_result: CommitResult,
        max_runs: int,
        downstream: list[dict[str, object]],
        visited: set[str],
    ) -> tuple[CommitResult, TransformRunPlan] | None:
        """Run a child transform once unless the graph already visited it."""
        api_name = str(transform["api_name"])
        if api_name in visited or len(downstream) >= max_runs:
            return None
        visited.add(api_name)
        child_result, child_plan = self._run_transform_for_graph(ctx, api_name)
        self._record_downstream_trigger(ctx, parent_plan, child_plan, parent_result, child_result)
        return child_result, child_plan

    def _downstream_transforms(self, ctx: RequestContext, dataset_ref: str) -> list[TransformRow]:
        """Find tenant-scoped transforms that consume the parent output dataset."""
        with self.engine.begin() as conn:
            transforms = self.transform_repository.list_transforms(transaction=conn, tenant_id=ctx.tenant_id)
        return [row for row in transforms if dataset_ref in row["inputs"].values()]

    def _record_downstream_trigger(
        self,
        ctx: RequestContext,
        parent_plan: TransformRunPlan,
        child_plan: TransformRunPlan,
        parent_result: CommitResult,
        child_result: CommitResult,
    ) -> None:
        """Persist audit and run-relation evidence for a downstream trigger."""
        with self.engine.begin() as conn:
            self._audit_downstream_trigger(conn, ctx, parent_plan, child_plan, parent_result, child_result)
            self.runtime_service._run_relation(
                conn,
                ctx,
                source_run_type="transform",
                source_run_id=parent_plan.run_id,
                target_run_type="transform",
                target_run_id=child_plan.run_id,
                relation="triggered_downstream",
                resource_type="dataset_version",
                resource_id=parent_result.version_id,
                metadata={"sourceDatasetRef": parent_result.dataset_ref, "targetDatasetRef": child_result.dataset_ref},
            )

    def _audit_downstream_trigger(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        parent_plan: TransformRunPlan,
        child_plan: TransformRunPlan,
        parent_result: CommitResult,
        child_result: CommitResult,
    ) -> None:
        """Write the downstream-trigger audit event."""
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="transform.run.downstream_triggered",
            resource_type="transform",
            resource_id=child_plan.transform_id,
            action="transform:run",
            before_ref={"source_transform_run_id": parent_plan.run_id, "source_version_id": parent_result.version_id},
            after_ref={"target_transform_run_id": child_plan.run_id, "target_version_id": child_result.version_id},
            correlation_id=parent_plan.run_id,
        )


def _commit_payload(result: CommitResult, plan: TransformRunPlan) -> dict[str, object]:
    """Build the JSON-ready graph execution payload for one transform run."""
    return {
        "apiName": str(plan.definition_snapshot["api_name"]),
        "transformRunId": plan.run_id,
        "datasetRef": result.dataset_ref,
        "versionId": result.version_id,
        "transactionId": result.transaction_id,
        "rowCount": result.row_count,
    }
