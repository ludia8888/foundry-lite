"""Payload helpers for Pipeline Builder services."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.pipeline_repository import (
    PipelineBranchRecord,
    PipelineBranchRow,
    PipelineProposalRecord,
    PipelineProposalRow,
    PipelineRunRecord,
    PipelineRunRow,
    PipelineScheduleRecord,
    PipelineScheduleRow,
    PipelineTestResultRecord,
    PipelineTestResultRow,
    PipelineVersionRecord,
    PipelineVersionRow,
)
from foundry_lite.application.primitives import _new_id
from foundry_lite.application.services.pipeline_deployment_admission import (
    locked_deployment_replay as locked_deployment_replay,
)
from foundry_lite.application.services.pipeline_deployment_admission import optional_text as optional_text
from foundry_lite.application.services.pipeline_deployment_admission import (
    require_expected_current_deployment as require_expected_current_deployment,
)
from foundry_lite.application.services.pipeline_deployment_admission import (
    require_matching_deployment as require_matching_deployment,
)
from foundry_lite.application.services.pipeline_graph_model import pipeline_graph_fingerprint
from foundry_lite.application.services.pipeline_graph_normalizer import (
    empty_pipeline_graph_v2,
    pipeline_graph_schema_version,
)
from foundry_lite.application.services.pipeline_proposal_review_evidence import public_proposal_description
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

JsonObject = dict[str, object]


def bounded_pipeline_limit(limit: int, *, cap: int = 100) -> int:
    if limit < 1:
        return 1
    return min(limit, cap)


def required_text(value: str | None, field: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValidationFailed(f"{field} is required", details={"field": field})
    return normalized


def branch_record(
    ctx: RequestContext,
    *,
    pipeline_id: str,
    name: str,
    now: str,
    latest: PipelineVersionRow | None = None,
) -> PipelineBranchRecord:
    graph = dict(latest["graph"]) if latest is not None else dict(empty_pipeline_graph_v2())
    return PipelineBranchRecord(
        branch_id=_new_id("pbr"),
        tenant_id=ctx.tenant_id,
        pipeline_id=pipeline_id,
        name=name,
        base_version_id=latest["id"] if latest is not None else None,
        base_graph=graph,
        graph=graph,
        graph_fingerprint=pipeline_graph_fingerprint(graph),
        created_by=ctx.actor_user_id,
        created_at=now,
        updated_at=now,
        graph_schema_version=pipeline_graph_schema_version(graph),
    )


def proposal_record(
    ctx: RequestContext,
    branch: PipelineBranchRow,
    *,
    title: str,
    description: str | None,
    now: str,
) -> PipelineProposalRecord:
    return PipelineProposalRecord(
        proposal_id=_new_id("ppr"),
        tenant_id=ctx.tenant_id,
        pipeline_id=str(branch["pipeline_id"]),
        branch_id=str(branch["id"]),
        title=title,
        description=description,
        graph=dict(branch["graph"]),
        graph_fingerprint=str(branch["graph_fingerprint"]),
        created_by=ctx.actor_user_id,
        created_at=now,
        graph_schema_version=int(branch["graph_schema_version"]),
    )


def version_record(
    ctx: RequestContext,
    proposal: PipelineProposalRow,
    *,
    version_number: int,
    now: str,
) -> PipelineVersionRecord:
    return PipelineVersionRecord(
        version_id=_new_id("pver"),
        tenant_id=ctx.tenant_id,
        pipeline_id=str(proposal["pipeline_id"]),
        version_number=version_number,
        graph=dict(proposal["graph"]),
        graph_fingerprint=str(proposal["graph_fingerprint"]),
        proposal_id=str(proposal["id"]),
        created_by=ctx.actor_user_id,
        created_at=now,
        graph_schema_version=int(proposal["graph_schema_version"]),
    )


def run_record(
    ctx: RequestContext,
    *,
    pipeline_id: str,
    version_id: str,
    idempotency_key: str | None = None,
    request_fingerprint: str | None = None,
    plan_fingerprint: str | None = None,
    parameters: Mapping[str, object] | None = None,
    target_node_ids: list[str] | None = None,
    now: str,
    initial_status: str = "running",
) -> PipelineRunRecord:
    return PipelineRunRecord(
        run_id=_new_id("prun"),
        tenant_id=ctx.tenant_id,
        pipeline_id=pipeline_id,
        version_id=version_id,
        status=initial_status,
        output_dataset_ref=None,
        output_version_id=None,
        timeline=[{"event": f"pipeline.run.{initial_status}", "at": now, "versionId": version_id}],
        error=None,
        created_by=ctx.actor_user_id,
        started_at=now,
        completed_at=None,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        plan_fingerprint=plan_fingerprint,
        parameters=dict(parameters or {}),
        target_node_ids=list(target_node_ids or []),
        schedule_id=_optional_parameter_text(parameters, "scheduleId"),
        schedule_slot_at=_optional_parameter_text(parameters, "slotStart"),
    )


def _optional_parameter_text(
    parameters: Mapping[str, object] | None,
    key: str,
) -> str | None:
    value = parameters.get(key) if parameters is not None else None
    return value if isinstance(value, str) and value.strip() else None


def schedule_record(
    ctx: RequestContext,
    *,
    pipeline_id: str,
    version_id: str,
    schedule: Mapping[str, object],
    enabled: bool,
    status: str,
    trigger_type: str,
    timezone: str,
    next_due_at: str | None,
    paused_reason: str | None,
    now: str,
) -> PipelineScheduleRecord:
    return PipelineScheduleRecord(
        schedule_id=_new_id("psch"),
        tenant_id=ctx.tenant_id,
        pipeline_id=pipeline_id,
        version_id=version_id,
        schedule=dict(schedule),
        enabled=enabled,
        status=status,
        trigger_type=trigger_type,
        timezone=timezone,
        next_due_at=next_due_at,
        updated_by=ctx.actor_user_id,
        updated_at=now,
        paused_reason=paused_reason,
    )


def test_result_record(
    ctx: RequestContext,
    *,
    pipeline_id: str,
    branch_id: str,
    status: str,
    result: Mapping[str, object],
    now: str,
) -> PipelineTestResultRecord:
    return PipelineTestResultRecord(
        result_id=_new_id("ptr"),
        tenant_id=ctx.tenant_id,
        pipeline_id=pipeline_id,
        branch_id=branch_id,
        status=status,
        result=dict(result),
        created_by=ctx.actor_user_id,
        created_at=now,
    )


def test_result_payload(row: PipelineTestResultRow) -> JsonObject:
    return {
        "id": row["id"],
        "pipelineId": row["pipeline_id"],
        "branchId": row["branch_id"],
        "status": row["status"],
        "result": row["result"],
        "createdBy": row["created_by"],
        "createdAt": row["created_at"],
    }


def branch_payload(
    row: PipelineBranchRow,
    *,
    is_separate_reviewer_required: bool = False,
) -> JsonObject:
    return {
        "id": row["id"],
        "pipelineId": row["pipeline_id"],
        "name": row["name"],
        "status": row["status"],
        "baseVersionId": row["base_version_id"],
        "graph": row["graph"],
        "graphFingerprint": row["graph_fingerprint"],
        "graphSchemaVersion": row["graph_schema_version"],
        "createdBy": row["created_by"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "proposalId": row["proposal_id"],
        "mergedVersionId": row["merged_version_id"],
        "protection": {
            "requiresProposal": True,
            "requiresSeparateReviewer": is_separate_reviewer_required,
            "blocksStaleProposal": True,
        },
    }


def proposal_payload(row: PipelineProposalRow) -> JsonObject:
    return {
        "id": row["id"],
        "pipelineId": row["pipeline_id"],
        "branchId": row["branch_id"],
        "title": row["title"],
        "description": public_proposal_description(row["description"]),
        "status": row["status"],
        "graph": row["graph"],
        "graphFingerprint": row["graph_fingerprint"],
        "graphSchemaVersion": row["graph_schema_version"],
        "assignedTo": row["assigned_to"],
        "decision": row["decision"],
        "decisionComment": row["decision_comment"],
        "decidedAt": row["decided_at"],
        "createdBy": row["created_by"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def version_payload(row: PipelineVersionRow) -> JsonObject:
    return {
        "id": row["id"],
        "pipelineId": row["pipeline_id"],
        "versionNumber": row["version_number"],
        "graph": row["graph"],
        "graphFingerprint": row["graph_fingerprint"],
        "graphSchemaVersion": row["graph_schema_version"],
        "executionPlan": row["execution_plan"],
        "planFingerprint": row["plan_fingerprint"],
        "compilerVersion": row["compiler_version"],
        "proposalId": row["proposal_id"],
        "createdBy": row["created_by"],
        "createdAt": row["created_at"],
        "deployedAt": row["deployed_at"],
    }


def run_payload(row: PipelineRunRow) -> JsonObject:
    return {
        "id": row["id"],
        "pipelineId": row["pipeline_id"],
        "versionId": row["version_id"],
        "status": row["status"],
        "idempotencyKey": row["idempotency_key"],
        "requestFingerprint": row["request_fingerprint"],
        "planFingerprint": row["plan_fingerprint"],
        "workflowRunId": row["workflow_run_id"],
        "orchestration": {
            "dispatchStatus": row["dispatch_status"],
            "dispatchAttemptCount": row["dispatch_attempt_count"],
            "dispatchError": row["dispatch_error"],
            "lastEventSequence": row["event_sequence"],
        },
        "executionLeaseExpiresAt": row["execution_lease_expires_at"],
        "executionHeartbeatAt": row["execution_heartbeat_at"],
        "cancelRequestedAt": row["cancel_requested_at"],
        "cancelReason": row["cancel_reason"],
        "scheduleId": row["schedule_id"],
        "scheduleSlotAt": row["schedule_slot_at"],
        "terminalObservedAt": row["terminal_observed_at"],
        "parameters": row["parameters"],
        "targetNodeIds": row["target_node_ids"],
        "outputs": row["outputs"],
        "outputDatasetRef": row["output_dataset_ref"],
        "outputVersionId": row["output_version_id"],
        "timeline": row["timeline"],
        "error": row["error"],
        "createdBy": row["created_by"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
    }


def schedule_payload(row: PipelineScheduleRow | None) -> JsonObject | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "pipelineId": row["pipeline_id"],
        "versionId": row["version_id"],
        "schedule": row["schedule"],
        "enabled": row["enabled"],
        "status": row["status"],
        "updatedBy": row["updated_by"],
        "updatedAt": row["updated_at"],
        "lastTickAt": row["last_tick_at"],
        "lastSlotAt": row["last_slot_at"],
        "triggerType": row["trigger_type"],
        "timezone": row["timezone"],
        "nextDueAt": row["next_due_at"],
        "leaseOwner": row["lease_owner"],
        "leaseExpiresAt": row["lease_expires_at"],
        "fencingToken": row["fencing_token"],
        "failureCount": row["failure_count"],
        "pausedReason": row["paused_reason"],
        "lastFailureAt": row["last_failure_at"],
        "lastError": row["last_error"],
        "lastTerminalRunId": row["last_terminal_run_id"],
        "lastTerminalSlotAt": row["last_terminal_slot_at"],
        "lastTerminalStatus": row["last_terminal_status"],
        "lastTerminalAt": row["last_terminal_at"],
    }


def require_open_branch(row: PipelineBranchRow) -> None:
    if row["status"] != "open":
        raise ConflictDetected("pipeline branch is not open", details={"branch_id": row["id"], "status": row["status"]})


def require_proposal_status(row: PipelineProposalRow, statuses: tuple[str, ...]) -> None:
    if row["status"] not in statuses:
        raise ConflictDetected(
            "pipeline proposal is not in the required state",
            details={"proposal_id": row["id"], "status": row["status"], "required": list(statuses)},
        )
