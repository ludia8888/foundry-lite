"""Generic durable node-run, attempt, and artifact evidence for Graph v2."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from foundry_lite.application.ports import TransactionContext, TransactionManager
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineExecutionRepository,
    PipelineNodeAttemptRow,
    PipelineNodeRunRow,
    PipelineRunArtifactRow,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.pipeline_execution_contracts import thaw_json_value
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
    canonical_input_artifacts,
    input_artifact_payloads,
    require_node_coordinates,
    require_output_security,
)
from foundry_lite.application.state_transitions import (
    PIPELINE_NODE_ATTEMPT_FAILED,
    PIPELINE_NODE_ATTEMPT_SUCCEEDED,
    PIPELINE_NODE_RUN_FAILED,
    PIPELINE_NODE_RUN_SKIPPED,
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
    ) -> None:
        if not run_id.strip():
            raise ValidationFailed("pipeline run id is required")
        self._transaction_manager = transaction_manager
        self._repository = repository
        self._ctx = ctx
        self._run_id = run_id

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
        now = _now()
        with self._transaction_manager.begin() as transaction:
            node_run = self._insert_node_run(
                transaction,
                node_id=node_id,
                descriptor_id=descriptor_id,
                spec_version=spec_version,
                input_artifacts=inputs,
                now=now,
            )
            node_run = self._claim_or_replay_node(transaction, node_run, inputs, now)
            attempt = self._ensure_graph_v2_attempt(transaction, node_run, inputs, executor_profile, now)
            self._require_attempt_contract(attempt, descriptor_id, spec_version, executor_profile, inputs)
        return self._attempt_context(node_run, attempt, descriptor_id, spec_version, executor_profile, inputs)

    def succeed(
        self,
        attempt: PipelineGraphV2AttemptContext,
        artifact: PipelineGraphV2ArtifactSpec,
    ) -> PipelineRunArtifactRow:
        require_output_security(attempt, artifact)
        now = _now()
        with self._transaction_manager.begin() as transaction:
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
            persisted = self._repository.insert_artifact(transaction=transaction, record=record)
            require_artifact_record_matches(persisted, record)
            output_ref = graph_v2_output_artifact_ref(persisted, attempt)
            self._complete_attempt_success(transaction, attempt_row, output_ref, now)
            self._complete_node_success(transaction, node_run, output_ref, now)
        return persisted

    def fail(
        self,
        attempt: PipelineGraphV2AttemptContext,
        error: Mapping[str, object],
    ) -> None:
        error_payload = _error_payload(error)
        now = _now()
        with self._transaction_manager.begin() as transaction:
            node_run, attempt_row = self._required_execution_rows(transaction, attempt)
            self._complete_attempt_failure(transaction, attempt_row, error_payload, now)
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
        error_payload = _error_payload(error)
        now = _now()
        with self._transaction_manager.begin() as transaction:
            node_run = self._insert_node_run(
                transaction,
                node_id=node_id,
                descriptor_id=descriptor_id,
                spec_version=spec_version,
                input_artifacts=inputs,
                now=now,
            )
            return self._skip_or_replay_node(transaction, node_run, error_payload, now)

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
        self._require_node_contract(row, node_id, descriptor_id, spec_version, input_artifacts)
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
        node_run = self._repository.node_run_by_run_node(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            run_id=self._run_id,
            node_id=attempt.node_id,
        )
        if node_run is None or node_run["id"] != attempt.node_run_id:
            raise ConflictDetected("pipeline node attempt does not belong to this run")
        attempt_row = self._repository.attempt_by_number(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            node_run_id=attempt.node_run_id,
            attempt_number=attempt.attempt_number,
        )
        if attempt_row is None or attempt_row["id"] != attempt.attempt_id:
            raise ConflictDetected("pipeline node attempt evidence is missing or stale")
        self._require_context_contract(node_run, attempt_row, attempt)
        return node_run, attempt_row

    def _require_context_contract(
        self,
        node_run: PipelineNodeRunRow,
        attempt_row: PipelineNodeAttemptRow,
        attempt: PipelineGraphV2AttemptContext,
    ) -> None:
        self._require_node_contract(
            node_run,
            attempt.node_id,
            attempt.descriptor_id,
            attempt.spec_version,
            attempt.input_artifacts,
        )
        self._require_attempt_contract(
            attempt_row,
            attempt.descriptor_id,
            attempt.spec_version,
            attempt.executor_profile,
            attempt.input_artifacts,
        )

    def _require_node_contract(
        self,
        row: PipelineNodeRunRow,
        node_id: str,
        descriptor_id: str,
        spec_version: int,
        inputs: tuple[PipelineGraphV2InputArtifact, ...],
    ) -> None:
        expected = (
            self._run_id,
            node_id,
            descriptor_id,
            str(spec_version),
            input_artifact_payloads(inputs),
        )
        actual = (
            row["run_id"],
            row["node_id"],
            row["descriptor_id"],
            row["spec_version"],
            row["input_artifacts"],
        )
        if actual != expected:
            raise ConflictDetected("pipeline node run coordinates changed under the same run and node id")

    def _require_attempt_contract(
        self,
        row: PipelineNodeAttemptRow,
        descriptor_id: str,
        spec_version: int,
        executor_profile: str,
        inputs: tuple[PipelineGraphV2InputArtifact, ...],
    ) -> None:
        manifest = row["input_manifest"]
        expected = (
            executor_profile,
            descriptor_id,
            spec_version,
            input_artifact_payloads(inputs),
        )
        actual = (
            row["executor_profile"],
            manifest.get("descriptorId"),
            manifest.get("specVersion"),
            manifest.get("artifacts"),
        )
        if actual != expected:
            raise ConflictDetected("pipeline node attempt coordinates changed under the same attempt number")

    def _attempt_context(
        self,
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
        )

    def _complete_attempt_success(
        self,
        transaction: TransactionContext,
        row: PipelineNodeAttemptRow,
        output_ref: JsonObject,
        now: str,
    ) -> None:
        expected: JsonObject = {"artifacts": [output_ref]}
        if row["status"] == "SUCCEEDED":
            if row["output_manifest"] != expected:
                raise ConflictDetected("pipeline attempt success replay has different artifact evidence")
            return
        if row["status"] != "RUNNING":
            raise ConflictDetected("pipeline attempt cannot succeed from its current state")
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
    ) -> None:
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

    def _skip_or_replay_node(
        self,
        transaction: TransactionContext,
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
        completed = self._repository.update_node_run_terminal(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
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


def _error_payload(error: Mapping[str, object]) -> JsonObject:
    payload = cast(JsonObject, thaw_json_value(error))
    if not payload:
        raise ValidationFailed("pipeline node error evidence cannot be empty")
    return payload
