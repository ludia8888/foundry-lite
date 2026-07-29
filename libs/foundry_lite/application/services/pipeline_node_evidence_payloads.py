"""Stable camelCase public payloads for Pipeline node execution evidence."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineExecutionRepository,
    PipelineNodeAttemptRow,
    PipelineNodeRunRow,
    PipelineRunArtifactRow,
)
from foundry_lite.application.ports.pipeline_repository import PipelineRunRow
from foundry_lite.application.services.pipeline_node_evidence_plan import PipelineEvidencePlan
from foundry_lite.application.services.pipeline_payloads import run_payload

JsonObject = dict[str, object]


def run_with_evidence_payload(
    *,
    transaction: TransactionContext,
    repository: PipelineExecutionRepository,
    tenant_id: str,
    row: PipelineRunRow,
    execution_plan: Mapping[str, object],
) -> JsonObject:
    node_rows = repository.node_runs_for_run(
        transaction=transaction,
        tenant_id=tenant_id,
        run_id=str(row["id"]),
    )
    artifacts = repository.artifacts_for_run(
        transaction=transaction,
        tenant_id=tenant_id,
        run_id=str(row["id"]),
    )
    plan = PipelineEvidencePlan(execution_plan)
    payload = run_payload(row)
    events = repository.run_events(
        transaction=transaction,
        tenant_id=tenant_id,
        run_id=str(row["id"]),
        after_sequence=0,
        limit=500,
    )
    if events:
        payload["timeline"] = [
            {
                "sequence": event["sequence"],
                "event": event["event_type"],
                "at": event["created_at"],
                **event["payload"],
            }
            for event in events
        ]
    payload["nodeRuns"] = _node_payloads(transaction, repository, tenant_id, node_rows, plan)
    payload["artifacts"] = [_artifact_payload(item) for item in _ordered_artifacts(artifacts, plan)]
    return payload


def _node_payloads(
    transaction: TransactionContext,
    repository: PipelineExecutionRepository,
    tenant_id: str,
    rows: list[PipelineNodeRunRow],
    plan: PipelineEvidencePlan,
) -> list[JsonObject]:
    order = _order_index(plan.node_order())
    ordered = sorted(rows, key=lambda row: (order.get(row["node_id"], len(order)), row["node_id"]))
    return [
        _node_payload(
            row,
            repository.attempts_for_node_run(
                transaction=transaction,
                tenant_id=tenant_id,
                node_run_id=row["id"],
            ),
        )
        for row in ordered
    ]


def _node_payload(
    row: PipelineNodeRunRow,
    attempts: list[PipelineNodeAttemptRow],
) -> JsonObject:
    return {
        "id": row["id"],
        "runId": row["run_id"],
        "nodeId": row["node_id"],
        "descriptorId": row["descriptor_id"],
        "specVersion": _spec_version(row["spec_version"]),
        "status": row["status"].lower(),
        "attemptCount": row["attempt_count"],
        "inputArtifacts": row["input_artifacts"],
        "outputArtifacts": row["output_artifacts"],
        "error": row["error"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "attempts": [_attempt_payload(attempt) for attempt in attempts],
    }


def _attempt_payload(row: PipelineNodeAttemptRow) -> JsonObject:
    return {
        "id": row["id"],
        "nodeRunId": row["node_run_id"],
        "attemptNumber": row["attempt_number"],
        "status": row["status"].lower(),
        "executorProfile": row["executor_profile"],
        "workerId": row["lease_owner"],
        "leaseExpiresAt": row["lease_expires_at"],
        "fencingToken": row["fencing_token"],
        "heartbeatAt": row["heartbeat_at"],
        "retryAt": row["retry_at"],
        "errorKind": row["error_kind"],
        "externalExecutionId": row["external_execution_id"],
        "inputManifest": row["input_manifest"],
        "outputManifest": row["output_manifest"],
        "error": row["error"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
    }


def _ordered_artifacts(
    rows: list[PipelineRunArtifactRow],
    plan: PipelineEvidencePlan,
) -> list[PipelineRunArtifactRow]:
    order = _order_index(plan.node_order())
    return sorted(
        rows,
        key=lambda row: (order.get(row["node_id"], len(order)), row["port_id"], row["created_at"], row["id"]),
    )


def _artifact_payload(row: PipelineRunArtifactRow) -> JsonObject:
    return {
        "id": row["id"],
        "runId": row["run_id"],
        "nodeRunId": row["node_run_id"],
        "attemptId": row["attempt_id"],
        "fencingToken": row["fencing_token"],
        "nodeId": row["node_id"],
        "portId": row["port_id"],
        "artifactKind": row["artifact_kind"],
        "plane": row["plane"],
        "artifactRef": row["artifact_ref"],
        "manifest": row["manifest"],
        "contentFingerprint": row["content_fingerprint"],
        "securityEnvelope": row["security_envelope"],
        "status": row["status"],
        "isServing": row["is_serving"],
        "committedAt": row["committed_at"],
        "createdAt": row["created_at"],
    }


def _order_index(values: tuple[str, ...]) -> dict[str, int]:
    return {value: index for index, value in enumerate(values)}


def _spec_version(value: str) -> int | str:
    return int(value) if value.isdigit() else value
