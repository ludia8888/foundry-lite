"""Durable Dataset node-run, attempt, and artifact evidence for Pipeline builds."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import (
    DatasetRepository,
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
from foundry_lite.application.primitives import CommitResult, _new_id, _now
from foundry_lite.application.services.pipeline_node_evidence_dataset import (
    required_dataset_for_version,
    required_dataset_version,
)
from foundry_lite.application.services.pipeline_node_evidence_plan import PipelineEvidencePlan
from foundry_lite.application.services.pipeline_node_evidence_records import (
    artifact_idempotency_key,
    inherited_security_envelope,
    input_artifact_ref,
    node_artifact_ref,
    output_artifact_record,
    source_artifact_record,
    source_security_envelope,
    unstarted_attempt_record,
)
from foundry_lite.application.services.pipeline_node_execution_evidence_types import (
    PipelineNodeAttemptContext,
)
from foundry_lite.application.services.transform_runs import TransformRunPlan
from foundry_lite.application.state_transitions import (
    PIPELINE_NODE_ATTEMPT_FAILED,
    PIPELINE_NODE_ATTEMPT_SUCCEEDED,
    PIPELINE_NODE_RUN_FAILED,
    PIPELINE_NODE_RUN_SKIPPED,
    PIPELINE_NODE_RUN_SUCCEEDED,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation

JsonObject = dict[str, object]
type PipelineNodeEvidenceRepository = PipelineExecutionRepository


class PipelineNodeExecutionEvidence:
    """Write synchronous Dataset execution evidence without owning business commits."""

    def __init__(
        self,
        *,
        transaction_manager: TransactionManager,
        repository: PipelineExecutionRepository,
        dataset_repository: DatasetRepository,
        dataset_version_repository: DatasetVersionRepository,
        ctx: RequestContext,
        run_id: str,
        execution_plan: Mapping[str, object],
    ) -> None:
        self._transaction_manager = transaction_manager
        self._pipeline_execution_repository = repository
        self._dataset_repository = dataset_repository
        self._dataset_version_repository = dataset_version_repository
        self._ctx = ctx
        self._run_id = run_id
        self._plan = PipelineEvidencePlan(execution_plan)

    def start_attempt(
        self,
        item: Mapping[str, object],
        transform_plan: TransformRunPlan,
    ) -> PipelineNodeAttemptContext:
        node_id = str(item["nodeId"])
        now = _now()
        with self._transaction_manager.begin() as transaction:
            inputs = self._resolved_input_artifacts(transaction, node_id, transform_plan)
            node_run = self._ensure_node_run(transaction, node_id, inputs, now)
            claimed = self._pipeline_execution_repository.claim_node_run(
                transaction=transaction,
                tenant_id=self._ctx.tenant_id,
                node_run_id=str(node_run["id"]),
                attempt_number=1,
                input_artifacts=inputs,
                started_at=now,
                updated_at=now,
            )
            if claimed is None:
                raise ConflictDetected("pipeline node run could not be claimed", details={"node_id": node_id})
            attempt = self._ensure_attempt(transaction, claimed, transform_plan, inputs, now)
        return PipelineNodeAttemptContext(
            node_id=node_id,
            node_run_id=str(claimed["id"]),
            attempt_id=str(attempt["id"]),
            attempt_number=int(attempt["attempt_number"]),
            input_artifacts=tuple(inputs),
            transform_plan=transform_plan,
        )

    def succeed(
        self,
        transaction: TransactionContext,
        attempt: PipelineNodeAttemptContext,
        result: CommitResult,
    ) -> None:
        artifact = self._committed_output_artifact(transaction, attempt, result)
        output_ref = node_artifact_ref(artifact)
        completed_at = _now()
        completed_attempt = self._pipeline_execution_repository.update_node_attempt_terminal(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            attempt_id=attempt.attempt_id,
            transition=PIPELINE_NODE_ATTEMPT_SUCCEEDED,
            output_manifest={"artifacts": [output_ref]},
            error=None,
            completed_at=completed_at,
        )
        completed_node = self._pipeline_execution_repository.update_node_run_terminal(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            node_run_id=attempt.node_run_id,
            transition=PIPELINE_NODE_RUN_SUCCEEDED,
            output_artifacts=[output_ref],
            error=None,
            completed_at=completed_at,
            updated_at=completed_at,
        )
        if completed_attempt is None or completed_node is None:
            raise ConflictDetected(
                "pipeline node success evidence changed concurrently",
                details={"node_id": attempt.node_id},
            )

    def fail_and_skip(
        self,
        transaction: TransactionContext,
        *,
        failed_attempt: PipelineNodeAttemptContext | None,
        failed_item: Mapping[str, object] | None,
        skipped_items: tuple[JsonObject, ...],
        error: Mapping[str, object],
    ) -> None:
        if failed_attempt is not None:
            self._fail_attempt(transaction, failed_attempt, error)
        elif failed_item is not None:
            self._record_unstarted_failure(transaction, failed_item, error)
        failed_node_id = str(failed_item["nodeId"]) if failed_item is not None else None
        for item in skipped_items:
            self._skip_node(transaction, item, failed_node_id, error)

    def _resolved_input_artifacts(
        self,
        transaction: TransactionContext,
        node_id: str,
        transform_plan: TransformRunPlan,
    ) -> list[JsonObject]:
        artifacts: list[JsonObject] = []
        for dataset_ref, version_id in sorted(transform_plan.input_versions.items()):
            artifact = self._resolved_input_artifact(transaction, node_id, dataset_ref, version_id)
            edge = self._plan.incoming_edge(node_id, artifact["node_id"], artifact["port_id"])
            artifacts.append(input_artifact_ref(artifact, edge))
        return artifacts

    def _resolved_input_artifact(
        self,
        transaction: TransactionContext,
        node_id: str,
        dataset_ref: str,
        version_id: str,
    ) -> PipelineRunArtifactRow:
        spec = self._plan.input_artifact_spec(node_id, dataset_ref)
        key = artifact_idempotency_key(self._run_id, spec, version_id)
        existing = self._pipeline_execution_repository.artifact_by_idempotency_key(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            idempotency_key=key,
        )
        if existing is not None:
            return existing
        producer = self._plan.node(str(spec["producerNodeId"]))
        if producer.get("descriptorId") != "source.dataset":
            raise InvariantViolation(
                "pipeline upstream Dataset artifact evidence is missing",
                details={"node_id": node_id, "dataset_ref": dataset_ref, "version_id": version_id},
            )
        return self._commit_source_artifact(transaction, producer, spec, dataset_ref, version_id, key)

    def _commit_source_artifact(
        self,
        transaction: TransactionContext,
        producer: Mapping[str, object],
        spec: Mapping[str, object],
        dataset_ref: str,
        version_id: str,
        idempotency_key: str,
    ) -> PipelineRunArtifactRow:
        version, dataset = required_dataset_for_version(
            self._dataset_repository,
            self._dataset_version_repository,
            transaction,
            self._ctx.tenant_id,
            version_id,
        )
        now = _now()
        node_run = self._ensure_node_run(transaction, str(producer["nodeId"]), [], now)
        if node_run["status"] == "PENDING":
            node_run = self._claim_source_node(transaction, node_run, version_id, now)
        artifact = self._pipeline_execution_repository.insert_artifact(
            transaction=transaction,
            record=source_artifact_record(
                ctx=self._ctx,
                run_id=self._run_id,
                node_run_id=str(node_run["id"]),
                spec=spec,
                dataset_ref=dataset_ref,
                version=version,
                security_envelope=source_security_envelope(dataset),
                idempotency_key=idempotency_key,
                now=now,
            ),
        )
        if node_run["status"] == "RUNNING":
            self._complete_source_node(transaction, node_run, artifact, now)
        return artifact

    def _claim_source_node(
        self,
        transaction: TransactionContext,
        node_run: PipelineNodeRunRow,
        version_id: str,
        now: str,
    ) -> PipelineNodeRunRow:
        claimed = self._pipeline_execution_repository.claim_node_run(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            node_run_id=str(node_run["id"]),
            attempt_number=1,
            input_artifacts=[],
            started_at=now,
            updated_at=now,
        )
        if claimed is None:
            raise ConflictDetected(
                "pipeline source node could not be claimed", details={"node_id": node_run["node_id"]}
            )
        self._pipeline_execution_repository.insert_node_attempt(
            transaction=transaction,
            record=PipelineNodeAttemptRecord(
                attempt_id=_new_id("pattempt"),
                tenant_id=self._ctx.tenant_id,
                node_run_id=str(claimed["id"]),
                attempt_number=1,
                executor_profile="dataset-source-resolver",
                input_manifest={"resolvedVersionId": version_id},
                started_at=now,
            ),
        )
        return claimed

    def _complete_source_node(
        self,
        transaction: TransactionContext,
        node_run: PipelineNodeRunRow,
        artifact: PipelineRunArtifactRow,
        now: str,
    ) -> None:
        attempt = self._pipeline_execution_repository.attempt_by_number(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            node_run_id=str(node_run["id"]),
            attempt_number=1,
        )
        if attempt is None:
            raise InvariantViolation("pipeline source attempt evidence is missing")
        output_ref = node_artifact_ref(artifact)
        completed_attempt = self._pipeline_execution_repository.update_node_attempt_terminal(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            attempt_id=str(attempt["id"]),
            transition=PIPELINE_NODE_ATTEMPT_SUCCEEDED,
            output_manifest={"artifacts": [output_ref]},
            error=None,
            completed_at=now,
        )
        completed_node = self._pipeline_execution_repository.update_node_run_terminal(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            node_run_id=str(node_run["id"]),
            transition=PIPELINE_NODE_RUN_SUCCEEDED,
            output_artifacts=[output_ref],
            error=None,
            completed_at=now,
            updated_at=now,
        )
        if completed_attempt is None or completed_node is None:
            raise ConflictDetected("pipeline source evidence changed concurrently")

    def _ensure_node_run(
        self,
        transaction: TransactionContext,
        node_id: str,
        input_artifacts: list[JsonObject],
        now: str,
    ) -> PipelineNodeRunRow:
        node = self._plan.node(node_id)
        return self._pipeline_execution_repository.insert_node_run(
            transaction=transaction,
            record=PipelineNodeRunRecord(
                node_run_id=_new_id("pnode"),
                tenant_id=self._ctx.tenant_id,
                run_id=self._run_id,
                node_id=node_id,
                descriptor_id=str(node["descriptorId"]),
                spec_version=str(node["specVersion"]),
                input_artifacts=input_artifacts,
                created_at=now,
            ),
        )

    def _ensure_attempt(
        self,
        transaction: TransactionContext,
        node_run: PipelineNodeRunRow,
        transform_plan: TransformRunPlan,
        input_artifacts: list[JsonObject],
        now: str,
    ) -> PipelineNodeAttemptRow:
        return self._pipeline_execution_repository.insert_node_attempt(
            transaction=transaction,
            record=PipelineNodeAttemptRecord(
                attempt_id=_new_id("pattempt"),
                tenant_id=self._ctx.tenant_id,
                node_run_id=str(node_run["id"]),
                attempt_number=1,
                executor_profile=self._plan.executor_profile(str(node_run["node_id"])),
                input_manifest={
                    "artifacts": input_artifacts,
                    "transformRunId": transform_plan.run_id,
                },
                started_at=now,
            ),
        )

    def _committed_output_artifact(
        self,
        transaction: TransactionContext,
        attempt: PipelineNodeAttemptContext,
        result: CommitResult,
    ) -> PipelineRunArtifactRow:
        spec = self._plan.output_artifact_spec(attempt.node_id)
        version = required_dataset_version(
            self._dataset_version_repository,
            transaction,
            self._ctx.tenant_id,
            result.version_id,
        )
        security = inherited_security_envelope(
            attempt.transform_plan.output_dataset["classification"],
            attempt.input_artifacts,
        )
        now = _now()
        return self._pipeline_execution_repository.insert_artifact(
            transaction=transaction,
            record=output_artifact_record(
                ctx=self._ctx,
                run_id=self._run_id,
                attempt=attempt,
                spec=spec,
                result=result,
                version=version,
                pins=self._plan.node_pins(attempt.node_id),
                security_envelope=security,
                now=now,
            ),
        )

    def _fail_attempt(
        self,
        transaction: TransactionContext,
        attempt: PipelineNodeAttemptContext,
        error: Mapping[str, object],
    ) -> None:
        self._fail_attempt_rows(
            transaction,
            node_id=attempt.node_id,
            node_run_id=attempt.node_run_id,
            attempt_id=attempt.attempt_id,
            error=error,
        )

    def _fail_attempt_rows(
        self,
        transaction: TransactionContext,
        *,
        node_id: str,
        node_run_id: str,
        attempt_id: str,
        error: Mapping[str, object],
    ) -> None:
        completed_at = _now()
        completed_attempt = self._pipeline_execution_repository.update_node_attempt_terminal(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            attempt_id=attempt_id,
            transition=PIPELINE_NODE_ATTEMPT_FAILED,
            output_manifest={},
            error=dict(error),
            completed_at=completed_at,
        )
        completed_node = self._pipeline_execution_repository.update_node_run_terminal(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            node_run_id=node_run_id,
            transition=PIPELINE_NODE_RUN_FAILED,
            output_artifacts=[],
            error=dict(error),
            completed_at=completed_at,
            updated_at=completed_at,
        )
        if completed_attempt is None or completed_node is None:
            raise ConflictDetected(
                "pipeline node failure evidence changed concurrently",
                details={"node_id": node_id},
            )

    def _record_unstarted_failure(
        self,
        transaction: TransactionContext,
        item: Mapping[str, object],
        error: Mapping[str, object],
    ) -> None:
        now = _now()
        node_run = self._ensure_node_run(transaction, str(item["nodeId"]), [], now)
        claimed = self._pipeline_execution_repository.claim_node_run(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            node_run_id=str(node_run["id"]),
            attempt_number=1,
            input_artifacts=[],
            started_at=now,
            updated_at=now,
        )
        if claimed is None:
            raise ConflictDetected("pipeline failed node could not be claimed")
        attempt = self._pipeline_execution_repository.insert_node_attempt(
            transaction=transaction,
            record=unstarted_attempt_record(
                tenant_id=self._ctx.tenant_id,
                node_run_id=str(claimed["id"]),
                executor_profile=self._plan.executor_profile(str(claimed["node_id"])),
                started_at=now,
            ),
        )
        self._fail_attempt_rows(
            transaction,
            node_id=str(item["nodeId"]),
            node_run_id=str(claimed["id"]),
            attempt_id=str(attempt["id"]),
            error=error,
        )

    def _skip_node(
        self,
        transaction: TransactionContext,
        item: Mapping[str, object],
        failed_node_id: str | None,
        error: Mapping[str, object],
    ) -> None:
        now = _now()
        node_run = self._ensure_node_run(transaction, str(item["nodeId"]), [], now)
        skipped_error: JsonObject = {
            "code": "PIPELINE_NODE_SKIPPED",
            "message": "node was not attempted because an earlier node failed",
            "failedNodeId": failed_node_id,
            "cause": dict(error),
        }
        completed = self._pipeline_execution_repository.update_node_run_terminal(
            transaction=transaction,
            tenant_id=self._ctx.tenant_id,
            node_run_id=str(node_run["id"]),
            transition=PIPELINE_NODE_RUN_SKIPPED,
            output_artifacts=[],
            error=skipped_error,
            completed_at=now,
            updated_at=now,
        )
        if completed is None:
            raise ConflictDetected("pipeline skipped node evidence changed concurrently")
