"""Validated request, persistence, and response values for Pipeline previews."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelinePreviewRunRecord,
    PipelinePreviewRunRow,
)
from foundry_lite.application.primitives import _json_hash, _new_id
from foundry_lite.application.services.pipeline_graph_model import (
    pipeline_graph_fingerprint,
    validate_pipeline_graph,
)
from foundry_lite.application.services.pipeline_preview_recovery import (
    pipeline_preview_execution_context,
    pipeline_preview_utc_now,
)
from foundry_lite.application.state_transitions import (
    PIPELINE_PREVIEW_CANCELLED,
    PIPELINE_PREVIEW_FAILED,
    StatusTransition,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed


def _preview_record(
    ctx: RequestContext,
    *,
    pipeline_id: str,
    branch_id: str,
    graph: Mapping[str, object],
    target_node_id: str | None,
    limits: Mapping[str, object],
    idempotency_key: str,
    request_fingerprint: str,
) -> PipelinePreviewRunRecord:
    return PipelinePreviewRunRecord(
        preview_run_id=_new_id("pprev"),
        tenant_id=ctx.tenant_id,
        pipeline_id=pipeline_id,
        branch_id=branch_id,
        graph=dict(graph),
        graph_fingerprint=pipeline_graph_fingerprint(graph),
        target_node_id=target_node_id,
        limits=dict(limits),
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        execution_context=pipeline_preview_execution_context(ctx),
        created_by=ctx.actor_user_id,
        created_at=pipeline_preview_utc_now(),
    )


def _preview_request_fingerprint(
    graph: Mapping[str, object],
    target_node_id: str | None,
    limits: Mapping[str, object],
) -> str:
    return _json_hash(
        {
            "graphFingerprint": pipeline_graph_fingerprint(graph),
            "targetNodeId": target_node_id,
            "limits": dict(limits),
        }
    )


def _idempotent_preview_payload(
    row: PipelinePreviewRunRow,
    request_fingerprint: str,
) -> dict[str, object]:
    if row["request_fingerprint"] == request_fingerprint:
        return _preview_payload(row)
    raise ConflictDetected(
        "pipeline preview idempotency key was reused with a different request",
        details={"preview_run_id": row["id"]},
    )


def _preview_payload(row: PipelinePreviewRunRow) -> dict[str, object]:
    return {
        "id": row["id"],
        "pipelineId": row["pipeline_id"],
        "branchId": row["branch_id"],
        "status": row["status"],
        "graphFingerprint": row["graph_fingerprint"],
        "targetNodeId": row["target_node_id"],
        "limits": row["limits"],
        "outputs": row["outputs"],
        "artifacts": row["artifacts"],
        "commitForbidden": row["is_commit_forbidden"],
        "orchestration": {
            "workflowRunId": row.get("workflow_run_id"),
            "dispatchStatus": row.get("dispatch_status", "pending"),
            "dispatchAttemptCount": row.get("dispatch_attempt_count", 0),
            "dispatchError": row.get("dispatch_error"),
        },
        "servingVersionCreated": False,
        "notice": "미리보기 전용 · 출력 버전이 생성되지 않음",
        "cancelRequestedAt": row["cancel_requested_at"],
        "error": row["error"],
        "createdBy": row["created_by"],
        "createdAt": row["created_at"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
    }


def _require_valid_preview_graph(graph: Mapping[str, object]) -> None:
    validation = validate_pipeline_graph(graph)
    if not validation["valid"]:
        raise ValidationFailed("pipeline preview graph is invalid", details={"validation": validation})


__all__ = [
    "PIPELINE_PREVIEW_CANCELLED",
    "PIPELINE_PREVIEW_FAILED",
    "StatusTransition",
    "_idempotent_preview_payload",
    "_preview_payload",
    "_preview_record",
    "_preview_request_fingerprint",
    "_require_valid_preview_graph",
]
