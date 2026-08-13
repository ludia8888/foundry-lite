"""Bounded recovery helpers for interrupted ontology proposal activation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from foundry_lite.application.ports import TransactionContext, TransactionManager
from foundry_lite.application.ports.insight_review_repository import (
    InsightReviewRepository,
    InsightReviewRow,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.ontology_activation_receipts import (
    activation_fingerprint,
    replay_activation,
    required_activation_key,
)
from foundry_lite.application.services.ontology_proposal_payloads import (
    proposal_status,
    proposal_yaml_text,
)
from foundry_lite.application.services.ontology_protocols import OntologyRuntimeBoundary
from foundry_lite.application.services.ontology_yaml_loading import load_ontology_yaml_text
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected

_EXECUTION_RECOVERY_SECONDS = 20
StoreSuccess = Callable[
    [TransactionContext, RequestContext, InsightReviewRow, dict[str, object]],
    dict[str, object],
]
LoadProposal = Callable[[TransactionContext, RequestContext, str], InsightReviewRow]
RecordFailure = Callable[[RequestContext, str, Exception], None]


class OntologyProposalExecutionOutcomeUnknown(Exception):
    """Activation committed, but the proposal completion projection failed."""

    original: Exception

    def __init__(self, original: Exception) -> None:
        super().__init__("ontology activation committed before proposal completion failed")
        self.original = original


def finish_execution_after_activation(
    finish: Callable[[RequestContext, str, dict[str, object]], dict[str, object]],
    ctx: RequestContext,
    proposal_id: str,
    applied: dict[str, object],
) -> dict[str, object]:
    try:
        return finish(ctx, proposal_id, applied)
    except Exception as exc:
        raise OntologyProposalExecutionOutcomeUnknown(exc) from exc


def recover_apply_exception(
    engine: TransactionManager,
    ctx: RequestContext,
    proposal_id: str,
    runtime: OntologyRuntimeBoundary,
    load_proposal: LoadProposal,
    store_success: StoreSuccess,
    record_failure: RecordFailure,
    original: Exception,
) -> dict[str, object]:
    try:
        with engine.begin() as conn:
            row = load_proposal(conn, ctx, proposal_id)
            recovered = reconcile_executing_proposal(conn, ctx, row, runtime, store_success, _now())
    except Exception as exc:
        raise OntologyProposalExecutionOutcomeUnknown(exc) from exc
    if recovered is not None:
        return recovered
    record_failure(ctx, proposal_id, original)
    raise original


def reconcile_executing_proposal(
    conn: TransactionContext,
    ctx: RequestContext,
    row: InsightReviewRow,
    runtime: OntologyRuntimeBoundary,
    store_success: StoreSuccess,
    now: str,
) -> dict[str, object] | None:
    definition = load_ontology_yaml_text(proposal_yaml_text(row))
    result = replay_activation(
        runtime,
        conn,
        ctx,
        required_activation_key(f"{row['id']}:execute"),
        activation_fingerprint(definition),
    )
    if result is None:
        return None
    applied_info = {
        "ontologyVersionId": result["ontology_version_id"],
        "versionNumber": result["version_number"],
        "migrationPlan": result["migration_plan"],
        "at": now,
        "appliedByUserId": ctx.actor_user_id,
    }
    return store_success(conn, ctx, row, applied_info)


def reclaim_execution(
    repository: InsightReviewRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    row: InsightReviewRow,
    now: str,
) -> str:
    if not _is_stale(row["updated_at"], now):
        raise ConflictDetected("ontology proposal execution is still in progress")
    reclaimed = repository.reclaim_execution(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        review_id=row["id"],
        execution_idempotency_key=f"{row['id']}:execute",
        expected_updated_at=row["updated_at"],
        updated_at=now,
    )
    if reclaimed is None:
        raise ConflictDetected("ontology proposal execution recovery was claimed concurrently")
    return proposal_yaml_text(reclaimed)


def claim_new_execution(
    repository: InsightReviewRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    row: InsightReviewRow,
    now: str,
) -> str:
    if proposal_status(row) != "approved":
        raise ConflictDetected(
            "only approved ontology proposals can be executed",
            details={"proposal_id": row["id"], "status": proposal_status(row)},
        )
    fingerprint = str(row["proposal_fingerprint"])
    claimed = repository.mark_execution_started(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        review_id=row["id"],
        execution_idempotency_key=f"{row['id']}:execute",
        execution_request_fingerprint=fingerprint,
        proposal_fingerprint=fingerprint,
        updated_at=now,
    )
    if claimed is None:
        raise ConflictDetected(
            "ontology proposal execution was already claimed",
            details={"proposal_id": row["id"], "executionStatus": row["execution_status"]},
        )
    return proposal_yaml_text(claimed)


def _is_stale(updated_at: str, now: str) -> bool:
    updated = _parse_time(updated_at)
    return updated + timedelta(seconds=_EXECUTION_RECOVERY_SECONDS) <= _parse_time(now)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


__all__ = [
    "OntologyProposalExecutionOutcomeUnknown",
    "claim_new_execution",
    "finish_execution_after_activation",
    "recover_apply_exception",
    "reclaim_execution",
    "reconcile_executing_proposal",
]
