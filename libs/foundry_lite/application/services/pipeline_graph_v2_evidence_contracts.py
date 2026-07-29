"""Replay-contract validation for Pipeline Graph v2 evidence."""

from __future__ import annotations

from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineExecutionRepository,
    PipelineNodeAttemptRow,
    PipelineNodeRunRow,
)
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.services.pipeline_graph_v2_execution_evidence_types import (
    JsonObject,
    PipelineGraphV2AttemptContext,
    PipelineGraphV2InputArtifact,
    input_artifact_payloads,
)
from foundry_lite.application.state_transitions import PIPELINE_NODE_RUN_SKIPPED
from foundry_lite.domain.errors import ConflictDetected


def require_node_contract(
    run_id: str,
    row: PipelineNodeRunRow,
    node_id: str,
    descriptor_id: str,
    spec_version: int,
    inputs: tuple[PipelineGraphV2InputArtifact, ...],
) -> None:
    expected_coordinates = (
        run_id,
        node_id,
        descriptor_id,
        str(spec_version),
    )
    actual_coordinates = (
        row["run_id"],
        row["node_id"],
        row["descriptor_id"],
        row["spec_version"],
    )
    if actual_coordinates != expected_coordinates:
        raise ConflictDetected("pipeline node run coordinates changed under the same run and node id")
    expected_inputs = input_artifact_payloads(inputs)
    if row["input_artifacts"] == expected_inputs:
        return
    if row["status"] == "PENDING" and row["input_artifacts"] == []:
        return
    raise ConflictDetected("pipeline node run coordinates changed under the same run and node id")


def require_attempt_contract(
    row: PipelineNodeAttemptRow,
    descriptor_id: str,
    spec_version: int,
    executor_profile: str,
    inputs: tuple[PipelineGraphV2InputArtifact, ...],
) -> None:
    manifest = row["input_manifest"]
    expected = (executor_profile, descriptor_id, spec_version, input_artifact_payloads(inputs))
    actual = (
        row["executor_profile"],
        manifest.get("descriptorId"),
        manifest.get("specVersion"),
        manifest.get("artifacts"),
    )
    if actual != expected:
        raise ConflictDetected("pipeline node attempt coordinates changed under the same attempt number")


def require_context_contract(
    run_id: str,
    node_run: PipelineNodeRunRow,
    attempt_row: PipelineNodeAttemptRow,
    attempt: PipelineGraphV2AttemptContext,
) -> None:
    require_node_contract(
        run_id,
        node_run,
        attempt.node_id,
        attempt.descriptor_id,
        attempt.spec_version,
        attempt.input_artifacts,
    )
    require_attempt_contract(
        attempt_row,
        attempt.descriptor_id,
        attempt.spec_version,
        attempt.executor_profile,
        attempt.input_artifacts,
    )


def required_execution_rows(
    repository: PipelineExecutionRepository,
    transaction: TransactionContext,
    tenant_id: str,
    run_id: str,
    attempt: PipelineGraphV2AttemptContext,
) -> tuple[PipelineNodeRunRow, PipelineNodeAttemptRow]:
    node_run = repository.node_run_by_run_node(
        transaction=transaction,
        tenant_id=tenant_id,
        run_id=run_id,
        node_id=attempt.node_id,
    )
    if node_run is None or node_run["id"] != attempt.node_run_id:
        raise ConflictDetected("pipeline node attempt does not belong to this run")
    attempt_row = repository.attempt_by_number(
        transaction=transaction,
        tenant_id=tenant_id,
        node_run_id=attempt.node_run_id,
        attempt_number=attempt.attempt_number,
    )
    if attempt_row is None or attempt_row["id"] != attempt.attempt_id:
        raise ConflictDetected("pipeline node attempt evidence is missing or stale")
    require_context_contract(run_id, node_run, attempt_row, attempt)
    return node_run, attempt_row


def attempt_context(
    node_run: PipelineNodeRunRow,
    attempt: PipelineNodeAttemptRow,
    descriptor_id: str,
    spec_version: int,
    executor_profile: str,
    inputs: tuple[PipelineGraphV2InputArtifact, ...],
) -> PipelineGraphV2AttemptContext:
    return PipelineGraphV2AttemptContext(
        node_id=str(node_run["node_id"]),
        descriptor_id=descriptor_id,
        spec_version=spec_version,
        executor_profile=executor_profile,
        node_run_id=str(node_run["id"]),
        attempt_id=str(attempt["id"]),
        attempt_number=int(attempt["attempt_number"]),
        input_artifacts=inputs,
        worker_id=attempt["lease_owner"],
        lease_token=attempt["lease_token"],
        fencing_token=attempt["fencing_token"],
    )


def skip_or_replay_node(
    repository: PipelineExecutionRepository,
    transaction: TransactionContext,
    tenant_id: str,
    row: PipelineNodeRunRow,
    error: JsonObject,
    now: str,
) -> PipelineNodeRunRow:
    if row["status"] == "SKIPPED":
        if row["error"] != error:
            raise ConflictDetected("pipeline skipped-node replay has different error evidence")
        return row
    if row["status"] != "PENDING":
        raise ConflictDetected("pipeline node cannot be skipped from its current state")
    completed = repository.update_node_run_terminal(
        transaction=transaction,
        tenant_id=tenant_id,
        node_run_id=str(row["id"]),
        transition=PIPELINE_NODE_RUN_SKIPPED,
        output_artifacts=[],
        error=error,
        completed_at=now,
        updated_at=now,
    )
    if completed is None:
        raise ConflictDetected("pipeline skipped-node evidence changed concurrently")
    return completed
