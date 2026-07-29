"""Fencing-specific persistence for distributed Pipeline Graph v2 workers."""

from __future__ import annotations

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineExecutionRepository,
    PipelineNodeAttemptClaim,
    PipelineNodeAttemptRow,
    PipelineNodeRunRow,
    PipelineRunArtifactRecord,
    PipelineRunArtifactRow,
)
from foundry_lite.application.services.pipeline_graph_v2_execution_evidence_types import (
    JsonObject,
    PipelineGraphV2AttemptContext,
    PipelineGraphV2InputArtifact,
    PipelineGraphV2WorkerLease,
    input_artifact_payloads,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation


class PipelineGraphV2WorkerEvidence:
    """Apply worker ownership checks to attempt, artifact, and terminal writes."""

    def __init__(
        self,
        repository: PipelineExecutionRepository,
        ctx: RequestContext,
        run_id: str,
        lease: PipelineGraphV2WorkerLease,
    ) -> None:
        self._repository = repository
        self._ctx = ctx
        self._run_id = run_id
        self._lease = lease

    def claim_attempt(
        self,
        transaction: TransactionContext,
        node_run: PipelineNodeRunRow,
        inputs: tuple[PipelineGraphV2InputArtifact, ...],
        executor_profile: str,
        descriptor_id: str,
        spec_version: int,
    ) -> tuple[PipelineNodeRunRow, PipelineNodeAttemptRow]:
        attempt = self._repository.claim_node_attempt(
            transaction=transaction,
            claim=self._claim(node_run, inputs, executor_profile, descriptor_id, spec_version),
        )
        if attempt is None:
            raise ConflictDetected("pipeline node has an active attempt owned by another worker")
        claimed = self._required_node_run(transaction, str(node_run["node_id"]))
        return claimed, attempt

    def insert_artifact(
        self,
        transaction: TransactionContext,
        record: PipelineRunArtifactRecord,
    ) -> PipelineRunArtifactRow:
        persisted = self._repository.insert_fenced_artifact(
            transaction=transaction,
            record=record,
            worker_id=self._lease.worker_id,
            lease_token=self._lease.lease_token,
        )
        if persisted is None:
            raise ConflictDetected("stale pipeline worker cannot commit artifact evidence")
        return persisted

    def complete_attempt(
        self,
        transaction: TransactionContext,
        attempt: PipelineGraphV2AttemptContext,
        status: str,
        output_manifest: JsonObject,
        error: JsonObject | None,
        completed_at: str,
    ) -> None:
        fencing_token = self._required_fencing_token(attempt)
        completed = self._repository.update_fenced_node_attempt_terminal(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            attempt_id=attempt.attempt_id,
            worker_id=self._lease.worker_id,
            lease_token=self._lease.lease_token,
            fencing_token=fencing_token,
            status=status,
            output_manifest=output_manifest,
            error=error,
            error_kind=None if error is None else str(error.get("kind") or error.get("code") or "unknown"),
            completed_at=completed_at,
        )
        if completed is None:
            raise ConflictDetected("stale pipeline worker cannot write terminal attempt evidence")

    def _claim(
        self,
        node_run: PipelineNodeRunRow,
        inputs: tuple[PipelineGraphV2InputArtifact, ...],
        executor_profile: str,
        descriptor_id: str,
        spec_version: int,
    ) -> PipelineNodeAttemptClaim:
        return PipelineNodeAttemptClaim(
            tenant_id=self._ctx.tenant_id,
            node_run_id=str(node_run["id"]),
            worker_id=self._lease.worker_id,
            lease_token=self._lease.lease_token,
            lease_expires_at=self._lease.lease_expires_at,
            heartbeat_at=self._lease.heartbeat_at,
            executor_profile=executor_profile,
            input_manifest={
                "descriptorId": descriptor_id,
                "specVersion": spec_version,
                "executorProfile": executor_profile,
                "requestId": self._ctx.request_id,
                "artifacts": input_artifact_payloads(inputs),
            },
            external_execution_id=self._lease.external_execution_id,
        )

    def _required_node_run(
        self,
        transaction: TransactionContext,
        node_id: str,
    ) -> PipelineNodeRunRow:
        row = self._repository.node_run_by_run_node(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            run_id=self._run_id,
            node_id=node_id,
        )
        if row is None:
            raise InvariantViolation("pipeline node run disappeared after attempt claim")
        return row

    def _required_fencing_token(self, attempt: PipelineGraphV2AttemptContext) -> int:
        expected = (self._lease.worker_id, self._lease.lease_token)
        if attempt.fencing_token is None or (attempt.worker_id, attempt.lease_token) != expected:
            raise InvariantViolation("pipeline fenced attempt coordinates are incomplete")
        return attempt.fencing_token
