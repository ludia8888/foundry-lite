"""Atomic stage, validate, and commit flow for governed Pipeline output candidates."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import (
    DatasetRepository,
    DatasetRow,
    DatasetVersionRepository,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineExecutionRepository,
    PipelineNodeAttemptRecord,
    PipelineNodeAttemptRow,
    PipelineNodeRunRecord,
    PipelineNodeRunRow,
    PipelineRunArtifactRow,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.pipeline_candidate_output_records import (
    PipelineCandidateCommitResult,
    PipelineCandidateStage,
    ValidatedPipelineCandidate,
    candidate_artifact_record,
    candidate_attempt_record,
    candidate_commit_result,
    candidate_event,
    candidate_input_resource_id,
    matching_candidate_input_artifacts,
    stage_pipeline_candidate,
    validate_pipeline_candidate,
)
from foundry_lite.application.services.pipeline_node_evidence_plan import PipelineEvidencePlan
from foundry_lite.application.services.pipeline_node_evidence_records import (
    artifact_idempotency_key,
    input_artifact_ref,
    node_artifact_ref,
    source_artifact_record,
    source_security_envelope,
)
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.application.state_transitions import (
    PIPELINE_NODE_ATTEMPT_SUCCEEDED,
    PIPELINE_NODE_RUN_SUCCEEDED,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation, NotFound, ValidationFailed

JsonObject = dict[str, object]


class GovernedPipelineCandidateCommitter:
    """Commit immutable candidates without claiming a serving index or active Ontology asset."""

    def __init__(
        self,
        *,
        transaction_manager: TransactionManager,
        repository: PipelineExecutionRepository,
        dataset_repository: DatasetRepository,
        dataset_version_repository: DatasetVersionRepository,
        runtime_service: RuntimeEvidenceBoundary,
        ctx: RequestContext,
        run_id: str,
        execution_plan: Mapping[str, object],
    ) -> None:
        self._transaction_manager = transaction_manager
        self._repository = repository
        self._dataset_repository = dataset_repository
        self._dataset_version_repository = dataset_version_repository
        self._runtime_service = runtime_service
        self._ctx = ctx
        self._run_id = run_id
        self._plan = PipelineEvidencePlan(execution_plan)

    def commit(self, item: Mapping[str, object]) -> PipelineCandidateCommitResult:
        stage = stage_pipeline_candidate(item)
        source_datasets = self._prefetch_source_datasets(stage.node_id)
        with self._transaction_manager.begin() as transaction:
            inputs = self._resolve_inputs(transaction, stage.node_id, source_datasets)
            candidate = self._validate_candidate(stage, inputs)
            node_run, attempt = self._start_attempt(transaction, candidate)
            artifact = self._commit_artifact(transaction, candidate, node_run)
            self._complete_attempt(transaction, node_run, attempt, artifact)
            self._record_governance(transaction, candidate, artifact)
        return candidate_commit_result(candidate, artifact)

    def _prefetch_source_datasets(self, node_id: str) -> dict[str, DatasetRow]:
        datasets: dict[str, DatasetRow] = {}
        for edge in self._plan.incoming_edges(node_id):
            source_id = str(edge["sourceNodeId"])
            source = self._plan.node(source_id)
            if source.get("descriptorId") != "source.dataset":
                continue
            spec = self._plan.artifact_spec(source_id, str(edge["sourcePortId"]))
            datasets[source_id] = self._required_dataset(str(spec.get("resourceRef") or ""))
        return datasets

    def _required_dataset(self, dataset_ref: str) -> DatasetRow:
        parts = dataset_ref.split(".", 1)
        if len(parts) != 2:
            raise ValidationFailed("pipeline candidate source Dataset ref is invalid")
        dataset = self._dataset_repository.find_active_dataset(
            tenant_id=self._ctx.tenant_id,
            namespace=parts[0],
            name=parts[1],
        )
        if dataset is None:
            raise NotFound("pipeline candidate source Dataset was not found", details={"datasetRef": dataset_ref})
        return dataset

    def _resolve_inputs(
        self,
        transaction: TransactionContext,
        node_id: str,
        source_datasets: Mapping[str, DatasetRow],
    ) -> tuple[JsonObject, ...]:
        edges = self._plan.incoming_edges(node_id)
        artifacts = self._repository.artifacts_for_run(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            run_id=self._run_id,
        )
        resolved = [
            input_artifact_ref(self._resolve_edge(transaction, edge, artifacts, source_datasets), edge)
            for edge in edges
        ]
        if len(resolved) != 1:
            raise ValidationFailed(
                "governed candidate output requires exactly one committed input artifact",
                details={"nodeId": node_id, "inputCount": len(resolved)},
            )
        return tuple(resolved)

    def _resolve_edge(
        self,
        transaction: TransactionContext,
        edge: Mapping[str, object],
        artifacts: list[PipelineRunArtifactRow],
        source_datasets: Mapping[str, DatasetRow],
    ) -> PipelineRunArtifactRow:
        matches = matching_candidate_input_artifacts(edge, artifacts)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise InvariantViolation("pipeline candidate input artifact is ambiguous")
        source_id = str(edge["sourceNodeId"])
        dataset = source_datasets.get(source_id)
        if dataset is None:
            raise InvariantViolation(
                "pipeline candidate input artifact is not committed",
                details={"sourceNodeId": source_id, "sourcePortId": edge["sourcePortId"]},
            )
        return self._commit_source_artifact(transaction, edge, dataset)

    def _commit_source_artifact(
        self,
        transaction: TransactionContext,
        edge: Mapping[str, object],
        dataset: DatasetRow,
    ) -> PipelineRunArtifactRow:
        version = self._dataset_version_repository.latest_version_by_dataset_id(
            transaction=transaction,
            dataset_id=dataset["id"],
        )
        if version is None:
            raise NotFound("pipeline candidate source Dataset has no committed version")
        spec = self._plan.artifact_spec(str(edge["sourceNodeId"]), str(edge["sourcePortId"]))
        key = artifact_idempotency_key(self._run_id, spec, str(version["id"]))
        existing = self._repository.artifact_by_idempotency_key(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            idempotency_key=key,
        )
        if existing is not None:
            return existing
        return self._insert_source_artifact(transaction, spec, dataset, version, key)

    def _insert_source_artifact(
        self,
        transaction: TransactionContext,
        spec: Mapping[str, object],
        dataset: DatasetRow,
        version: Mapping[str, object],
        idempotency_key: str,
    ) -> PipelineRunArtifactRow:
        now = _now()
        node_run = self._ensure_node_run(transaction, str(spec["producerNodeId"]), [], now)
        claimed, attempt = self._start_source_attempt(transaction, node_run, version, now)
        artifact = self._repository.insert_artifact(
            transaction=transaction,
            record=source_artifact_record(
                ctx=self._ctx,
                run_id=self._run_id,
                node_run_id=str(claimed["id"]),
                spec=spec,
                dataset_ref=f"{dataset['namespace']}.{dataset['name']}",
                version=version,
                security_envelope=source_security_envelope(dataset),
                idempotency_key=idempotency_key,
                now=now,
            ),
        )
        self._complete_attempt(transaction, claimed, attempt, artifact)
        return artifact

    def _start_source_attempt(
        self,
        transaction: TransactionContext,
        node_run: PipelineNodeRunRow,
        version: Mapping[str, object],
        now: str,
    ) -> tuple[PipelineNodeRunRow, PipelineNodeAttemptRow]:
        claimed = self._claim_node_run(transaction, node_run, [], now)
        attempt = self._repository.insert_node_attempt(
            transaction=transaction,
            record=PipelineNodeAttemptRecord(
                attempt_id=_new_id("pattempt"),
                tenant_id=self._ctx.tenant_id,
                node_run_id=str(claimed["id"]),
                attempt_number=1,
                executor_profile="dataset-source-resolver",
                input_manifest={"resolvedVersionId": version["id"]},
                started_at=now,
            ),
        )
        return claimed, attempt

    def _validate_candidate(
        self,
        stage: PipelineCandidateStage,
        inputs: tuple[JsonObject, ...],
    ) -> ValidatedPipelineCandidate:
        return validate_pipeline_candidate(stage, inputs, self._plan.node_pins(stage.node_id))

    def _start_attempt(
        self,
        transaction: TransactionContext,
        candidate: ValidatedPipelineCandidate,
    ) -> tuple[PipelineNodeRunRow, PipelineNodeAttemptRow]:
        now = _now()
        node_run = self._ensure_node_run(
            transaction,
            candidate.stage.node_id,
            list(candidate.input_artifacts),
            now,
        )
        claimed = self._claim_node_run(transaction, node_run, list(candidate.input_artifacts), now)
        attempt = self._repository.insert_node_attempt(
            transaction=transaction,
            record=candidate_attempt_record(self._ctx, claimed, candidate, now),
        )
        return claimed, attempt

    def _claim_node_run(
        self,
        transaction: TransactionContext,
        node_run: PipelineNodeRunRow,
        inputs: list[JsonObject],
        now: str,
    ) -> PipelineNodeRunRow:
        claimed = self._repository.claim_node_run(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            node_run_id=str(node_run["id"]),
            attempt_number=1,
            input_artifacts=inputs,
            started_at=now,
            updated_at=now,
        )
        if claimed is None:
            raise ConflictDetected("pipeline governed candidate node could not be claimed")
        return claimed

    def _ensure_node_run(
        self,
        transaction: TransactionContext,
        node_id: str,
        inputs: list[JsonObject],
        now: str,
    ) -> PipelineNodeRunRow:
        node = self._plan.node(node_id)
        return self._repository.insert_node_run(
            transaction=transaction,
            record=PipelineNodeRunRecord(
                node_run_id=_new_id("pnode"),
                tenant_id=self._ctx.tenant_id,
                run_id=self._run_id,
                node_id=node_id,
                descriptor_id=str(node["descriptorId"]),
                spec_version=str(node["specVersion"]),
                input_artifacts=inputs,
                created_at=now,
            ),
        )

    def _commit_artifact(
        self,
        transaction: TransactionContext,
        candidate: ValidatedPipelineCandidate,
        node_run: PipelineNodeRunRow,
    ) -> PipelineRunArtifactRow:
        now = _now()
        record = candidate_artifact_record(self._ctx, self._run_id, node_run, candidate, now)
        artifact = self._repository.insert_artifact(transaction=transaction, record=record)
        if artifact["content_fingerprint"] != candidate.content_fingerprint:
            raise ConflictDetected("pipeline candidate artifact idempotency collision")
        return artifact

    def _complete_attempt(
        self,
        transaction: TransactionContext,
        node_run: PipelineNodeRunRow,
        attempt: PipelineNodeAttemptRow,
        artifact: PipelineRunArtifactRow,
    ) -> None:
        now = _now()
        output = node_artifact_ref(artifact)
        completed_attempt = self._repository.update_node_attempt_terminal(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            attempt_id=str(attempt["id"]),
            transition=PIPELINE_NODE_ATTEMPT_SUCCEEDED,
            output_manifest={"artifacts": [output]},
            error=None,
            completed_at=now,
        )
        completed_node = self._repository.update_node_run_terminal(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            node_run_id=str(node_run["id"]),
            transition=PIPELINE_NODE_RUN_SUCCEEDED,
            output_artifacts=[output],
            error=None,
            completed_at=now,
            updated_at=now,
        )
        if completed_attempt is None or completed_node is None:
            raise ConflictDetected("pipeline candidate success evidence changed concurrently")

    def _record_governance(
        self,
        transaction: TransactionContext,
        candidate: ValidatedPipelineCandidate,
        artifact: PipelineRunArtifactRow,
    ) -> None:
        for input_artifact in candidate.input_artifacts:
            self._record_lineage(transaction, input_artifact, candidate)
        event = candidate_event(candidate, artifact)
        self._runtime_service._outbox(
            transaction,
            self._ctx,
            "pipeline.output_candidate.committed",
            candidate.stage.policy.candidate_type,
            candidate.candidate_id,
            event,
            idempotency_key=f"pipeline-candidate:{self._run_id}:{candidate.candidate_id}",
            correlation_id=self._ctx.request_id,
        )
        self._runtime_service._audit(
            transaction,
            self._ctx,
            event_type="pipeline.output_candidate.committed",
            resource_type=candidate.stage.policy.candidate_type,
            resource_id=candidate.candidate_id,
            action="commit",
            after_ref=event,
        )

    def _record_lineage(
        self,
        transaction: TransactionContext,
        input_artifact: Mapping[str, object],
        candidate: ValidatedPipelineCandidate,
    ) -> None:
        from_id = candidate_input_resource_id(input_artifact)
        self._runtime_service._lineage(
            transaction,
            self._ctx,
            str(input_artifact["artifactKind"]),
            from_id,
            candidate.stage.policy.artifact_kind.value,
            candidate.candidate_id,
            "pipeline_candidate_input_to",
            self._run_id,
        )
