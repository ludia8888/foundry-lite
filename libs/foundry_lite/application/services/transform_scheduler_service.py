"""Transform scheduler use cases."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from foundry_lite.application.ports import TransactionContext, TransactionManager, TransformRepository, TransformRow
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.transform_protocols import (
    TransformDatasetRegistry,
    TransformDatasetVersions,
    TransformRuntimeBoundary,
    require_transform_write_open,
)
from foundry_lite.application.services.transform_runs import TransformRunPlan
from foundry_lite.application.services.transform_scheduler import (
    TransformScheduleDecision,
    scheduler_now,
    transform_schedule_decision,
    transform_scheduler_run_payload,
    transform_scheduler_view,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.transform import validate_transform_scheduler_max_runs
from foundry_lite.security.policy import PolicyService


class _TransformRunner(Protocol):
    def run_transform_internal(self, ctx: RequestContext, api_name: str) -> tuple[CommitResult, TransformRunPlan]:
        """Run one transform without public entrypoint authorization."""
        ...


class TransformSchedulerService(CoreService):
    """Preview and run due snapshot transforms."""

    required_dependencies = ("engine", "policy", "transform_repository")
    required_collaborators = (
        "dataset_registry_service",
        "dataset_version_service",
        "runtime_service",
        "transform_run_service",
    )
    dataset_registry_service: TransformDatasetRegistry
    dataset_version_service: TransformDatasetVersions
    engine: TransactionManager
    policy: PolicyService
    runtime_service: TransformRuntimeBoundary
    transform_repository: TransformRepository
    transform_run_service: _TransformRunner

    def preview_due_transform_runs(
        self,
        *,
        ctx: RequestContext | None = None,
        max_runs: int = 50,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "transform:run")
        max_runs = validate_transform_scheduler_max_runs(max_runs)
        decisions = self._transform_schedule_decisions(ctx)
        return transform_scheduler_view(decisions, [], evaluated_at=scheduler_now(), max_runs=max_runs)

    def run_due_transform_runs(
        self,
        *,
        ctx: RequestContext | None = None,
        max_runs: int = 50,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "transform:run", "transform_scheduler", "tick")
        require_transform_write_open(self.runtime_service, ctx, "run_due_transform_runs", "transform_scheduler", "tick")
        max_runs = validate_transform_scheduler_max_runs(max_runs)
        decisions = self._transform_schedule_decisions(ctx)
        started = self._run_due_transform_decisions(ctx, decisions, max_runs=max_runs)
        return transform_scheduler_view(decisions, started, evaluated_at=scheduler_now(), max_runs=max_runs)

    def _transform_schedule_decisions(self, ctx: RequestContext) -> list[TransformScheduleDecision]:
        with self.engine.begin() as conn:
            transforms = self.transform_repository.list_transforms(transaction=conn, tenant_id=ctx.tenant_id)
            return [self._transform_schedule_decision(conn, ctx, transform) for transform in transforms]

    def _transform_schedule_decision(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        transform: TransformRow,
    ) -> TransformScheduleDecision:
        latest_input_versions, missing_input_refs = self._latest_transform_input_versions(conn, ctx, transform)
        return transform_schedule_decision(
            transform,
            latest_input_versions=latest_input_versions,
            missing_input_refs=missing_input_refs,
            last_successful_run=self.transform_repository.latest_successful_transform_run(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                transform_id=str(transform["id"]),
            ),
            active_run=self.transform_repository.running_transform_run(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                transform_id=str(transform["id"]),
            ),
        )

    def _latest_transform_input_versions(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        transform: TransformRow,
    ) -> tuple[dict[str, str], tuple[str, ...]]:
        versions: dict[str, str] = {}
        missing: list[str] = []
        for dataset_ref in sorted(set(transform["inputs"].values())):
            dataset = self.dataset_registry_service.get_dataset(dataset_ref, ctx=ctx)
            version = self.dataset_version_service._latest_version_by_dataset_id(
                conn,
                dataset["id"],
                allow_missing=True,
            )
            if version is None:
                missing.append(dataset_ref)
                continue
            versions[dataset_ref] = str(version["id"])
        return versions, tuple(missing)

    def _run_due_transform_decisions(
        self,
        ctx: RequestContext,
        decisions: Sequence[TransformScheduleDecision],
        *,
        max_runs: int,
    ) -> list[dict[str, object]]:
        started: list[dict[str, object]] = []
        for decision in decisions:
            if len(started) >= max_runs or not decision.is_due:
                continue
            started.append(self._run_scheduled_transform(ctx, decision))
        return started

    def _run_scheduled_transform(
        self,
        ctx: RequestContext,
        decision: TransformScheduleDecision,
    ) -> dict[str, object]:
        result, plan = self.transform_run_service.run_transform_internal(ctx, decision.api_name)
        self._record_scheduler_trigger(ctx, decision, result, plan)
        return transform_scheduler_run_payload(decision, result, plan)

    def _record_scheduler_trigger(
        self,
        ctx: RequestContext,
        decision: TransformScheduleDecision,
        result: CommitResult,
        plan: TransformRunPlan,
    ) -> None:
        with self.engine.begin() as conn:
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="transform.scheduler.triggered",
                resource_type="transform",
                resource_id=plan.transform_id,
                action="transform:run",
                before_ref=decision.as_dict(),
                after_ref={"transform_run_id": plan.run_id, "output_version_id": result.version_id},
                correlation_id=plan.run_id,
            )
            self.runtime_service._run_relation(
                conn,
                ctx,
                source_run_type="transform",
                source_run_id=decision.last_successful_run_id or "scheduler",
                target_run_type="transform",
                target_run_id=plan.run_id,
                relation="scheduled_rebuild_of",
                resource_type="transform",
                resource_id=plan.transform_id,
                metadata={"outputVersionId": result.version_id, "reason": decision.reason},
            )
