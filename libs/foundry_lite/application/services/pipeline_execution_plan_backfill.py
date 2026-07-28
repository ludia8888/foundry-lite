"""Compatibility upgrade for deployed Pipeline versions created before pinned plans."""

from __future__ import annotations

from foundry_lite.application.ports import TransactionManager
from foundry_lite.application.ports.pipeline_repository import PipelineRepository, PipelineVersionRow
from foundry_lite.application.services.pipeline_execution_contracts import (
    PipelineExecutionPlan,
    pipeline_execution_plan_payload,
)
from foundry_lite.application.services.pipeline_plan_compiler import PipelinePlanCompiler
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound


def ensure_pipeline_execution_plan(
    transaction_manager: TransactionManager,
    repository: PipelineRepository,
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    version: PipelineVersionRow,
) -> PipelineVersionRow:
    if version["execution_plan"] is not None:
        return version
    plan = PipelinePlanCompiler().compile(version["graph"])
    payload = pipeline_execution_plan_payload(plan)
    return _persist_execution_plan(transaction_manager, repository, runtime_service, ctx, version, plan, payload)


def _persist_execution_plan(
    transaction_manager: TransactionManager,
    repository: PipelineRepository,
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    version: PipelineVersionRow,
    plan: PipelineExecutionPlan,
    payload: dict[str, object],
) -> PipelineVersionRow:
    with transaction_manager.begin() as transaction:
        current = repository.version_by_id(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            version_id=str(version["id"]),
        )
        if current is None:
            raise NotFound("pipeline version not found", details={"version_id": version["id"]})
        if current["execution_plan"] is not None:
            return current
        updated = repository.mark_version_deployed(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            version_id=str(version["id"]),
            execution_plan=payload,
            plan_fingerprint=plan.plan_fingerprint,
            compiler_version=plan.compiler_version,
            deployed_at=str(version["deployed_at"]),
        )
        if updated is None:
            raise NotFound("pipeline version not found", details={"version_id": version["id"]})
        runtime_service._audit(
            transaction,
            ctx,
            event_type="pipeline.execution_plan_backfilled",
            resource_type="pipeline_version",
            resource_id=str(version["id"]),
            action="execution_plan_backfilled",
            after_ref={"planFingerprint": plan.plan_fingerprint, "compilerVersion": plan.compiler_version},
        )
        return updated
