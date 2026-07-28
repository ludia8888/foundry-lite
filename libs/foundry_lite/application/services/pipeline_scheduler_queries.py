"""Tenant-scoped Pipeline scheduler lookup requirements."""

from __future__ import annotations

from foundry_lite.application.ports import TransactionContext, TransactionManager
from foundry_lite.application.ports.pipeline_repository import (
    PipelineRepository,
    PipelineScheduleRow,
    PipelineVersionRow,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound


def list_due_schedule_rows(
    transaction_manager: TransactionManager,
    repository: PipelineRepository,
    *,
    tenant_id: str,
    due_at: str,
    limit: int,
) -> list[PipelineScheduleRow]:
    with transaction_manager.begin() as transaction:
        return repository.list_due_schedules(
            transaction=transaction,
            tenant_id=tenant_id,
            due_at=due_at,
            limit=limit,
        )


def require_schedule_version(
    repository: PipelineRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    version_id: str,
) -> PipelineVersionRow:
    row = repository.version_by_id(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        version_id=version_id,
    )
    if row is None:
        raise NotFound("pipeline version not found", details={"version_id": version_id})
    return row


def require_pipeline_schedule(
    repository: PipelineRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    pipeline_id: str,
) -> PipelineScheduleRow:
    row = repository.schedule_by_pipeline(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        pipeline_id=pipeline_id,
    )
    if row is None:
        raise NotFound("pipeline schedule not found", details={"pipeline_id": pipeline_id})
    return row
