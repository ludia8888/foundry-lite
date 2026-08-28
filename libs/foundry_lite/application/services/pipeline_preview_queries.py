"""Read-only query helpers for durable Pipeline preview workflows."""

from __future__ import annotations

from foundry_lite.application.ports.pipeline_repository import PipelineBranchRow, PipelineRepository
from foundry_lite.application.ports.transaction_context import TransactionManager
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound


def require_pipeline_preview_branch(
    engine: TransactionManager,
    repository: PipelineRepository,
    ctx: RequestContext,
    branch_id: str,
) -> PipelineBranchRow:
    """Load one tenant-owned branch or fail with the stable preview error."""

    with engine.begin() as conn:
        row = repository.branch_by_id(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            branch_id=branch_id,
        )
    if row is None:
        raise NotFound("pipeline branch not found", details={"branch_id": branch_id})
    return row
