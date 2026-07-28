"""Shared write guard and audit helpers for Pipeline run workflows."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext


def require_pipeline_write_open(
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    operation: str,
    resource_id: str,
) -> None:
    runtime_service._require_write_traffic_open(
        ctx,
        operation=operation,
        resource_type="pipeline",
        resource_id=resource_id,
    )


def audit_pipeline_run(
    runtime_service: RuntimeEvidenceBoundary,
    transaction: TransactionContext,
    ctx: RequestContext,
    event: str,
    resource_type: str,
    resource_id: str,
    after_ref: Mapping[str, object],
) -> None:
    runtime_service._audit(
        transaction,
        ctx,
        event_type=f"pipeline.{event}",
        resource_type=resource_type,
        resource_id=resource_id,
        action=event,
        after_ref=after_ref,
    )
