"""Generic durable node-run, attempt, and artifact evidence for Graph v2."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace

from foundry_lite.application.ports import (
    PipelineExecutionLeaseFence,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineExecutionRepository,
    PipelineNodeAttemptRow,
    PipelineNodeRunRow,
    PipelineRunArtifactRow,
)
from foundry_lite.application.primitives import _new_id, _utc_now
from foundry_lite.application.services.pipeline_graph_v2_evidence_contracts import (
    attempt_context,
    require_attempt_contract,
    require_node_contract,
    required_execution_rows,
    skip_or_replay_node,
)
from foundry_lite.application.services.pipeline_graph_v2_execution_evidence_records import (
    graph_v2_artifact_record,
    graph_v2_attempt_record,
    graph_v2_node_run_record,
    graph_v2_output_artifact_ref,
    require_artifact_record_matches,
)
from foundry_lite.application.services.pipeline_graph_v2_execution_evidence_types import (
    JsonObject,
    PipelineGraphV2ArtifactSpec,
    PipelineGraphV2AttemptContext,
    PipelineGraphV2InputArtifact,
    PipelineGraphV2WorkerLease,
    canonical_input_artifacts,
    input_artifact_payloads,
    require_node_coordinates,
    require_output_security,
    validated_error_payload,
)
from foundry_lite.application.services.pipeline_graph_v2_worker_evidence import (
    PipelineGraphV2WorkerEvidence,
)
from foundry_lite.application.state_transitions import (
    PIPELINE_NODE_ATTEMPT_FAILED,
    PIPELINE_NODE_ATTEMPT_SUCCEEDED,
    PIPELINE_NODE_RUN_FAILED,
    PIPELINE_NODE_RUN_SUCCEEDED,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation, ValidationFailed


class PipelineGraphV2ExecutionEvidenceWriter:
    """Persist runtime-neutral Graph v2 evidence without owning domain commits."""

    def __init__(
        self,
        *,
        transaction_manager: TransactionManager,
        repository: PipelineExecutionRepository,
        ctx: RequestContext,
        run_id: str,
        execution_lease_guard: PipelineExecutionLeaseFence,
        worker_lease: PipelineGraphV2WorkerLease | None = None,
        on_attempt_started: Callable[[PipelineGraphV2AttemptContext], None] | None = None,
    ) -> None:
        if not run_id.strip():
            raise ValidationFailed("pipeline run id is required")
        self._transaction_manager = transaction_manager
        self._repository = repository
        self._ctx = ctx
        self._run_id = run_id
        self._execution_lease_guard = execution_lease_guard
        self._worker_evidence = (
            PipelineGraphV2WorkerEvidence(repository, ctx, run_id, worker_lease) if worker_lease is not None else None
        )
        self._on_attempt_started = on_attempt_started

    def start(
        self,
        *,
        node_id: str,
        descriptor_id: str,
        spec_version: int,
        executor_profile: str,
        input_artifacts: tuple[PipelineGraphV2InputArtifact, ...],
    ) -> PipelineGraphV2AttemptContext:
        require_node_coordinates(
            node_id=node_id,
            descriptor_id=descriptor_id,
            spec_version=spec_version,
            executor_profile=executor_profile,
        )
        inputs = canonical_input_artifacts(input_artifacts)
        now = _utc_now()
        with self._transaction_manager.begin() as transaction:
            node_run, attempt = self._persist_started_attempt(
                transaction,
                node_id=node_id,
                descriptor_id=descriptor_id,
                spec_version=spec_version,
                executor_profile=executor_profile,
                inputs=inputs,
                now=now,
            )
        context = attempt_context(
            node_run,
            attempt,
            descriptor_id,
            spec_version,
            executor_profile,
            inputs,
        )
        if self._on_attempt_started is not None:
            self._on_attempt_started(context)
        return context

    def _persist_started_attempt(
        self,
        transaction: TransactionContext,
        *,
        node_id: str,
        descriptor_id: str,
        spec_version: int,
        executor_profile: str,
        inputs: tuple[PipelineGraphV2InputArtifact, ...],
        now: str,
    ) -> tuple[PipelineNodeRunRow, PipelineNodeAttemptRow]:
        self._execution_lease_guard.require_active(transaction)
        node_run = self._insert_node_run(
            transaction,
            node_id=node_id,
            descriptor_id=descriptor_id,
            spec_version=spec_version,
            input_artifacts=inputs,
            now=now,
        )
        node_run, attempt = self._start_attempt(
            transaction,
            node_run,
            inputs,
            executor_profile,
            descriptor_id,
            spec_version,
            now,
        )
        require_attempt_contract(attempt, descriptor_id, spec_version, executor_profile, inputs)
        return node_run, attempt

    def succeed(
        self,
        attempt: PipelineGraphV2AttemptContext,
        artifact: PipelineGraphV2ArtifactSpec,
    ) -> PipelineRunArtifactRow:
        require_output_security(attempt, artifact)
        now = _utc_now()
        with self._transaction_manager.begin() as transaction:
            self._execution_lease_guard.require_active(transaction)
            node_run, attempt_row = self._required_execution_rows(transaction, attempt)
            existing = self._repository.artifact_by_idempotency_key(
                transaction=transaction,
                tenant_id=self._ctx.tenant_id,
                idempotency_key=artifact.idempotency_key,
            )
            artifact_id = str(existing["id"]) if existing is not None else _new_id("partifact")
            record = graph_v2_artifact_record(
                ctx=self._ctx,
                run_id=self._run_id,
                attempt=attempt,
                spec=artifact,
                artifact_id=artifact_id,
                now=now,
            )
            record = replace(
                record,
                attempt_id=attempt.attempt_id,
                fencing_token=attempt.fencing_token,
            )
            persisted = (
                self._repository.insert_artifact(transaction=transaction, record=record)
                if self._worker_evidence is None
                else self._worker_evidence.insert_artifact(transaction, record)
            )
            require_artifact_record_matches(persisted, record)
            output_ref = graph_v2_output_artifact_ref(persisted, attempt)
            self._complete_attempt_success(transaction, attempt_row, output_ref, now, attempt)
            if attempt.worker_id is None:
                self._complete_node_success(transaction, node_run, output_ref, now)
        return persisted

    def fail(
        self,
        attempt: PipelineGraphV2AttemptContext,
        error: Mapping[str, object],
    ) -> None:
        error_payload = validated_error_payload(error)
        now = _utc_now()
        with self._transaction_manager.begin() as transaction:
            self._execution_lease_guard.require_active(transaction)
            node_run, attempt_row = self._required_execution_rows(transaction, attempt)
            self._complete_attempt_failure(transaction, attempt_row, error_payload, now, attempt)
            if attempt.worker_id is None:
                self._complete_node_failure(transaction, node_run, error_payload, now)

    def skip(
        self,
        *,
        node_id: str,
        descriptor_id: str,
        spec_version: int,
        input_artifacts: tuple[PipelineGraphV2InputArtifact, ...],
        error: Mapping[str, object],
    ) -> PipelineNodeRunRow:
        require_node_coordinates(
            node_id=node_id,
            descriptor_id=descriptor_id,
            spec_version=spec_version,
        )
        inputs = canonical_input_artifacts(input_artifacts)
        error_payload = validated_error_payload(error)
        now = _utc_now()
        with self._transaction_manager.begin() as transaction:
            self._execution_lease_guard.require_active(transaction)
            node_run = self._insert_node_run(
                transaction,
                node_id=node_id,
                descriptor_id=descriptor_id,
                spec_version=spec_version,
                input_artifacts=inputs,
                now=now,
            )
            return skip_or_replay_node(
                self._repository,
                transaction,
                self._ctx.tenant_id,
                node_run,
                error_payload,
                now,
            )

    def _insert_node_run(
        self,
        transaction: TransactionContext,
        *,
        node_id: str,
        descriptor_id: str,
        spec_version: int,
        input_artifacts: tuple[PipelineGraphV2InputArtifact, ...],
        now: str,
    ) -> PipelineNodeRunRow:
        record = graph_v2_node_run_record(
            ctx=self._ctx,
            run_id=self._run_id,
            node_id=node_id,
            descriptor_id=descriptor_id,
            spec_version=spec_version,
            input_artifacts=input_artifacts,
            now=now,
        )
        row = self._repository.insert_node_run(transaction=transaction, record=record)
        require_node_contract(
            self._run_id,
            row,
            node_id,
            descriptor_id,
            spec_version,
            input_artifacts,
        )
        return row

    def _claim_or_replay_node(
        self,
        transaction: TransactionContext,
        node_run: PipelineNodeRunRow,
        inputs: tuple[PipelineGraphV2InputArtifact, ...],
        now: str,
    ) -> PipelineNodeRunRow:
        status = str(node_run["status"])
        if status in {"RUNNING", "SUCCEEDED"}:
            return node_run
        if status != "PENDING":
            raise ConflictDetected(
                "pipeline node cannot be started from its current state",
                details={"nodeId": node_run["node_id"], "status": status},
            )
        claimed = self._repository.claim_node_run(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            node_run_id=str(node_run["id"]),
            attempt_number=1,
            input_artifacts=input_artifact_payloads(inputs),
            started_at=now,
            updated_at=now,
        )
        if claimed is None:
            raise ConflictDetected("pipeline node run could not be claimed")
        return claimed

    def _start_attempt(
        self,
        transaction: TransactionContext,
        node_run: PipelineNodeRunRow,
        inputs: tuple[PipelineGraphV2InputArtifact, ...],
        executor_profile: str,
        descriptor_id: str,
        spec_version: int,
        now: str,
    ) -> tuple[PipelineNodeRunRow, PipelineNodeAttemptRow]:
        if self._worker_evidence is None:
            claimed = self._claim_or_replay_node(transaction, node_run, inputs, now)
            attempt = self._ensure_graph_v2_attempt(transaction, claimed, inputs, executor_profile, now)
            return claimed, attempt
        return self._worker_evidence.claim_attempt(
            transaction,
            node_run,
            inputs,
            executor_profile,
            descriptor_id,
            spec_version,
        )

    def _ensure_graph_v2_attempt(
        self,
        transaction: TransactionContext,
        node_run: PipelineNodeRunRow,
        inputs: tuple[PipelineGraphV2InputArtifact, ...],
        executor_profile: str,
        now: str,
    ) -> PipelineNodeAttemptRow:
        existing = self._repository.attempt_by_number(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            node_run_id=str(node_run["id"]),
            attempt_number=1,
        )
        if existing is not None:
            return existing
        if node_run["status"] != "RUNNING":
            raise InvariantViolation("pipeline node attempt evidence is missing")
        return self._repository.insert_node_attempt(
            transaction=transaction,
            record=graph_v2_attempt_record(
                ctx=self._ctx,
                node_run_id=str(node_run["id"]),
                attempt_number=1,
                descriptor_id=str(node_run["descriptor_id"]),
                spec_version=int(node_run["spec_version"]),
                executor_profile=executor_profile,
                input_artifacts=inputs,
                now=now,
            ),
        )

    def _required_execution_rows(
        self,
        transaction: TransactionContext,
        attempt: PipelineGraphV2AttemptContext,
    ) -> tuple[PipelineNodeRunRow, PipelineNodeAttemptRow]:
        return required_execution_rows(
            self._repository,
            transaction,
            self._ctx.tenant_id,
            self._run_id,
            attempt,
        )

    def _complete_attempt_success(
        self,
        transaction: TransactionContext,
        row: PipelineNodeAttemptRow,
        output_ref: JsonObject,
        now: str,
        attempt: PipelineGraphV2AttemptContext,
    ) -> None:
        expected: JsonObject = {"artifacts": [output_ref]}
        if row["status"] == "SUCCEEDED":
            if row["output_manifest"] != expected:
                raise ConflictDetected("pipeline attempt success replay has different artifact evidence")
            return
        if row["status"] != "RUNNING":
            raise ConflictDetected("pipeline attempt cannot succeed from its current state")
        if self._worker_evidence is not None:
            self._worker_evidence.complete_attempt(
                transaction,
                attempt,
                "SUCCEEDED",
                expected,
                None,
                now,
            )
            return
        completed = self._repository.update_node_attempt_terminal(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            attempt_id=str(row["id"]),
            transition=PIPELINE_NODE_ATTEMPT_SUCCEEDED,
            output_manifest=expected,
            error=None,
            completed_at=now,
        )
        if completed is None:
            raise ConflictDetected("pipeline attempt success evidence changed concurrently")

    def _complete_node_success(
        self,
        transaction: TransactionContext,
        row: PipelineNodeRunRow,
        output_ref: JsonObject,
        now: str,
    ) -> None:
        expected = [output_ref]
        if row["status"] == "SUCCEEDED":
            if row["output_artifacts"] != expected:
                raise ConflictDetected("pipeline node success replay has different artifact evidence")
            return
        if row["status"] != "RUNNING":
            raise ConflictDetected("pipeline node cannot succeed from its current state")
        completed = self._repository.update_node_run_terminal(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            node_run_id=str(row["id"]),
            transition=PIPELINE_NODE_RUN_SUCCEEDED,
            output_artifacts=expected,
            error=None,
            completed_at=now,
            updated_at=now,
        )
        if completed is None:
            raise ConflictDetected("pipeline node success evidence changed concurrently")

    def _complete_attempt_failure(
        self,
        transaction: TransactionContext,
        row: PipelineNodeAttemptRow,
        error: JsonObject,
        now: str,
        attempt: PipelineGraphV2AttemptContext,
    ) -> None:
        if self._worker_evidence is not None:
            self._worker_evidence.complete_attempt(
                transaction,
                attempt,
                "FAILED",
                {},
                error,
                now,
            )
            return
        if row["status"] == "FAILED":
            if row["error"] != error:
                raise ConflictDetected("pipeline attempt failure replay has different error evidence")
            return
        if row["status"] != "RUNNING":
            raise ConflictDetected("pipeline attempt cannot fail from its current state")
        completed = self._repository.update_node_attempt_terminal(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            attempt_id=str(row["id"]),
            transition=PIPELINE_NODE_ATTEMPT_FAILED,
            output_manifest={},
            error=error,
            completed_at=now,
        )
        if completed is None:
            raise ConflictDetected("pipeline attempt failure evidence changed concurrently")

    def _complete_node_failure(
        self,
        transaction: TransactionContext,
        row: PipelineNodeRunRow,
        error: JsonObject,
        now: str,
    ) -> None:
        if row["status"] == "FAILED":
            if row["error"] != error:
                raise ConflictDetected("pipeline node failure replay has different error evidence")
            return
        if row["status"] != "RUNNING":
            raise ConflictDetected("pipeline node cannot fail from its current state")
        completed = self._repository.update_node_run_terminal(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            node_run_id=str(row["id"]),
            transition=PIPELINE_NODE_RUN_FAILED,
            output_artifacts=[],
            error=error,
            completed_at=now,
            updated_at=now,
        )
        if completed is None:
            raise ConflictDetected("pipeline node failure evidence changed concurrently")
