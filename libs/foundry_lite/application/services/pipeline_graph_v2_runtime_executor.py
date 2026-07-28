"""Engine-neutral, named-port executor for production Pipeline Graph v2 plans."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineRunArtifactRow,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.pipeline_candidate_output_committer import (
    GovernedPipelineCandidateCommitter,
)
from foundry_lite.application.services.pipeline_graph_v2_execution_evidence import (
    PipelineGraphV2ExecutionEvidenceWriter,
)
from foundry_lite.application.services.pipeline_graph_v2_execution_evidence_types import (
    PipelineGraphV2AttemptContext,
    PipelineGraphV2InputArtifact,
)
from foundry_lite.application.services.pipeline_graph_v2_runtime_evidence_bridge import (
    available_runtime_input_evidence,
    committed_output_reconciliation_payload,
    runtime_artifact_spec,
    runtime_input_evidence,
    runtime_node_timeline,
    runtime_output_payload,
)
from foundry_lite.application.services.pipeline_output_candidate_contracts import (
    require_candidate_policy,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2CommittedOutputReconciliationRequired,
    PipelineV2RunResult,
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeEdge,
    PipelineV2RuntimeNode,
    RuntimeInputs,
    input_artifacts_for_node,
)
from foundry_lite.domain.errors import ValidationFailed

JsonObject = dict[str, object]
ErrorPayload = Callable[[Exception], Mapping[str, object]]


class PipelineGraphV2NodeDispatcher(Protocol):
    """Runtime-specific node execution behind the engine-neutral loop."""

    def execute(
        self,
        node: PipelineV2RuntimeNode,
        inputs: RuntimeInputs,
    ) -> PipelineV2RuntimeArtifact: ...


class PipelineGraphV2RuntimeExecutor:
    """Execute independent DAG branches and retain exact partial outcomes."""

    def __init__(
        self,
        *,
        run_id: str,
        nodes: Sequence[PipelineV2RuntimeNode],
        edges: Sequence[PipelineV2RuntimeEdge],
        dispatcher: PipelineGraphV2NodeDispatcher,
        evidence: PipelineGraphV2ExecutionEvidenceWriter,
        candidates: GovernedPipelineCandidateCommitter,
        error_payload: ErrorPayload,
    ) -> None:
        self._run_id = run_id
        self._nodes = tuple(nodes)
        self._edges = tuple(edges)
        self._dispatcher = dispatcher
        self._evidence = evidence
        self._candidates = candidates
        self._error_payload = error_payload

    def execute(self) -> PipelineV2RunResult:
        state = _ExecutionState()
        state.timeline.append({"event": "pipeline.graph_v2.started", "at": _now()})
        for node in self._nodes:
            self._execute_or_skip(node, state)
        state.timeline.append(_terminal_timeline(state))
        return state.result()

    def _execute_or_skip(
        self,
        node: PipelineV2RuntimeNode,
        state: _ExecutionState,
    ) -> None:
        missing = _missing_upstream(node, self._edges, state.artifacts)
        if missing:
            self._skip_node(node, state, missing)
            return
        if node.descriptor_id in {"output.semantic_index", "output.ontology"}:
            self._execute_candidate(node, state)
            return
        self._execute_artifact_node(node, state)

    def _execute_artifact_node(
        self,
        node: PipelineV2RuntimeNode,
        state: _ExecutionState,
    ) -> None:
        inputs = input_artifacts_for_node(node.node_id, self._edges, state.artifacts)
        input_evidence = runtime_input_evidence(node, self._edges, state.persisted)
        attempt = self._evidence.start(
            node_id=node.node_id,
            descriptor_id=node.descriptor_id,
            spec_version=node.spec_version,
            executor_profile=node.runtime_capability,
            input_artifacts=input_evidence,
        )
        try:
            artifact = self._dispatcher.execute(node, inputs)
        except PipelineV2CommittedOutputReconciliationRequired as exc:
            self._record_committed_post_commit_failure(node, attempt, state, exc)
            return
        except Exception as exc:
            self._evidence.fail(attempt, self._error_payload(exc))
            self._record_failure(node, state, exc)
            return
        try:
            row = self._evidence.succeed(
                attempt,
                runtime_artifact_spec(self._run_id, artifact),
            )
        except Exception as exc:
            if _is_reconcilable_committed_output(node, artifact):
                self._record_committed_evidence_failure(node, artifact, attempt, state, exc)
                return
            self._evidence.fail(attempt, self._error_payload(exc))
            self._record_failure(node, state, exc)
            return
        self._record_success(node, artifact, row, state)

    def _record_committed_evidence_failure(
        self,
        node: PipelineV2RuntimeNode,
        artifact: PipelineV2RuntimeArtifact,
        attempt: PipelineGraphV2AttemptContext,
        state: _ExecutionState,
        exc: Exception,
    ) -> None:
        error = _committed_output_evidence_error(node, self._error_payload(exc))
        self._record_committed_reconciliation(
            node,
            artifact,
            attempt,
            state,
            error,
            "pipeline.node.committed_evidence_reconciliation_required",
        )

    def _record_committed_post_commit_failure(
        self,
        node: PipelineV2RuntimeNode,
        attempt: PipelineGraphV2AttemptContext,
        state: _ExecutionState,
        exc: PipelineV2CommittedOutputReconciliationRequired,
    ) -> None:
        cause = exc.__cause__ if isinstance(exc.__cause__, Exception) else exc
        error = _committed_output_post_commit_error(node, self._error_payload(cause))
        self._record_committed_reconciliation(
            node,
            exc.committed_artifact,
            attempt,
            state,
            error,
            "pipeline.node.committed_output_reconciliation_required",
        )

    def _record_committed_reconciliation(
        self,
        node: PipelineV2RuntimeNode,
        artifact: PipelineV2RuntimeArtifact,
        attempt: PipelineGraphV2AttemptContext,
        state: _ExecutionState,
        error: JsonObject,
        event: str,
    ) -> None:
        secondary = self._mark_attempt_failed(attempt, error)
        if secondary is not None:
            details = error.get("details")
            if isinstance(details, dict):
                details["attemptEvidenceFailure"] = secondary
        if state.error is None:
            state.error = error
            state.failed_node_id = node.node_id
        state.outputs.append(committed_output_reconciliation_payload(artifact, error))
        state.timeline.append(_failed_timeline(node, error, event))

    def _mark_attempt_failed(
        self,
        attempt: PipelineGraphV2AttemptContext,
        error: Mapping[str, object],
    ) -> JsonObject | None:
        try:
            self._evidence.fail(attempt, error)
        except Exception as exc:
            return dict(self._error_payload(exc))
        return None

    def _execute_candidate(
        self,
        node: PipelineV2RuntimeNode,
        state: _ExecutionState,
    ) -> None:
        inputs = runtime_input_evidence(node, self._edges, state.persisted)
        try:
            result = self._candidates.commit(_candidate_item(node))
        except Exception as exc:
            self._persist_candidate_failure(node, inputs, exc)
            self._record_failure(node, state, exc)
            return
        state.outputs.append(result.output)
        state.timeline.append(result.timeline_event)

    def _persist_candidate_failure(
        self,
        node: PipelineV2RuntimeNode,
        inputs: tuple[PipelineGraphV2InputArtifact, ...],
        exc: Exception,
    ) -> None:
        attempt = self._evidence.start(
            node_id=node.node_id,
            descriptor_id=node.descriptor_id,
            spec_version=node.spec_version,
            executor_profile=node.runtime_capability,
            input_artifacts=inputs,
        )
        self._evidence.fail(attempt, self._error_payload(exc))

    def _skip_node(
        self,
        node: PipelineV2RuntimeNode,
        state: _ExecutionState,
        missing: tuple[str, ...],
    ) -> None:
        error = _upstream_error(node, missing)
        inputs = available_runtime_input_evidence(node, self._edges, state.persisted)
        self._evidence.skip(
            node_id=node.node_id,
            descriptor_id=node.descriptor_id,
            spec_version=node.spec_version,
            input_artifacts=inputs,
            error=error,
        )
        state.skipped.append(node.node_id)
        state.timeline.append(_failed_timeline(node, error, "pipeline.node.skipped"))
        if node.kind == "output":
            state.outputs.append(_failed_output_payload(node, error, "SKIPPED"))

    def _record_success(
        self,
        node: PipelineV2RuntimeNode,
        artifact: PipelineV2RuntimeArtifact,
        row: PipelineRunArtifactRow,
        state: _ExecutionState,
    ) -> None:
        state.artifacts[node.node_id] = artifact
        state.persisted[node.node_id] = row
        state.timeline.append(runtime_node_timeline(node, row))
        if node.kind == "output":
            state.outputs.append(runtime_output_payload(artifact, row))

    def _record_failure(
        self,
        node: PipelineV2RuntimeNode,
        state: _ExecutionState,
        exc: Exception,
    ) -> None:
        error = dict(self._error_payload(exc))
        if state.error is None:
            state.error = error
            state.failed_node_id = node.node_id
        state.timeline.append(_failed_timeline(node, error, "pipeline.node.failed"))
        if node.kind == "output":
            state.outputs.append(_failed_output_payload(node, error, "FAILED"))


@dataclass
class _ExecutionState:
    artifacts: dict[str, PipelineV2RuntimeArtifact] = field(default_factory=dict)
    persisted: dict[str, PipelineRunArtifactRow] = field(default_factory=dict)
    outputs: list[JsonObject] = field(default_factory=list)
    timeline: list[JsonObject] = field(default_factory=list)
    error: JsonObject | None = None
    failed_node_id: str | None = None
    skipped: list[str] = field(default_factory=list)

    def result(self) -> PipelineV2RunResult:
        return PipelineV2RunResult(
            outputs=tuple(self.outputs),
            timeline=tuple(self.timeline),
            error=self.error,
            failed_node_id=self.failed_node_id,
            skipped_node_ids=tuple(self.skipped),
        )


def _missing_upstream(
    node: PipelineV2RuntimeNode,
    edges: Sequence[PipelineV2RuntimeEdge],
    artifacts: Mapping[str, PipelineV2RuntimeArtifact],
) -> tuple[str, ...]:
    source_ids = {edge.source_node_id for edge in edges if edge.target_node_id == node.node_id}
    return tuple(sorted(source_id for source_id in source_ids if source_id not in artifacts))


def _candidate_item(node: PipelineV2RuntimeNode) -> JsonObject:
    policy = require_candidate_policy(node.descriptor_id)
    resource_ref = node.config.get(policy.resource_field)
    if not isinstance(resource_ref, str) or not resource_ref.strip():
        raise ValidationFailed(
            "pipeline governed output resource ref is required",
            details={"nodeId": node.node_id, "field": policy.resource_field},
        )
    return {
        "nodeId": node.node_id,
        "descriptorId": node.descriptor_id,
        "specVersion": node.spec_version,
        "resourceRef": resource_ref.strip(),
        "config": dict(node.config),
        "artifactKind": policy.artifact_kind.value,
        "plane": policy.plane,
        "portId": policy.port_id,
        "candidateType": policy.candidate_type,
    }


def _upstream_error(
    node: PipelineV2RuntimeNode,
    missing: tuple[str, ...],
) -> JsonObject:
    return {
        "code": "PIPELINE_UPSTREAM_ARTIFACT_UNAVAILABLE",
        "message": "pipeline node was skipped because an upstream artifact failed",
        "details": {"nodeId": node.node_id, "sourceNodeIds": list(missing)},
    }


def _failed_timeline(
    node: PipelineV2RuntimeNode,
    error: Mapping[str, object],
    event: str,
) -> JsonObject:
    return {
        "event": event,
        "at": _now(),
        "nodeId": node.node_id,
        "descriptorId": node.descriptor_id,
        "specVersion": node.spec_version,
        "error": dict(error),
    }


def _terminal_timeline(state: _ExecutionState) -> JsonObject:
    if state.error is None:
        event = "pipeline.graph_v2.succeeded"
    elif _has_committed_output(state.outputs):
        event = "pipeline.graph_v2.partial"
    else:
        event = "pipeline.graph_v2.failed"
    return {
        "event": event,
        "at": _now(),
        "outputCount": len(state.outputs),
        "committedOutputCount": sum(output.get("status") == "COMMITTED" for output in state.outputs),
        "failedNodeId": state.failed_node_id,
        "skippedNodeIds": list(state.skipped),
    }


def _has_committed_output(outputs: Sequence[Mapping[str, object]]) -> bool:
    return any(output.get("status") == "COMMITTED" for output in outputs)


def _is_reconcilable_committed_output(
    node: PipelineV2RuntimeNode,
    artifact: PipelineV2RuntimeArtifact,
) -> bool:
    return node.kind == "output" and artifact.status == "COMMITTED" and artifact.is_serving


def _committed_output_evidence_error(
    node: PipelineV2RuntimeNode,
    cause: Mapping[str, object],
) -> JsonObject:
    return {
        "type": "PIPELINE_OUTPUT_EVIDENCE_PERSISTENCE_FAILED",
        "message": "serving output committed but its pipeline artifact passport requires reconciliation",
        "details": {
            "nodeId": node.node_id,
            "descriptorId": node.descriptor_id,
            "servingCommitPreserved": True,
            "cause": dict(cause),
        },
    }


def _committed_output_post_commit_error(
    node: PipelineV2RuntimeNode,
    cause: Mapping[str, object],
) -> JsonObject:
    return {
        "type": "PIPELINE_OUTPUT_POST_COMMIT_VALIDATION_FAILED",
        "message": "serving output committed but post-commit validation requires reconciliation",
        "details": {
            "nodeId": node.node_id,
            "descriptorId": node.descriptor_id,
            "servingCommitPreserved": True,
            "cause": dict(cause),
        },
    }


def _failed_output_payload(
    node: PipelineV2RuntimeNode,
    error: Mapping[str, object],
    disposition: str,
) -> JsonObject:
    payload = dict(error)
    payload["executionDisposition"] = disposition
    is_candidate = node.descriptor_id in {"output.semantic_index", "output.ontology"}
    return {
        "nodeId": node.node_id,
        "artifactKind": _output_artifact_kind(node),
        "plane": _output_plane(node),
        "status": "FAILED",
        "commitKind": "GOVERNED_CANDIDATE" if is_candidate else "SERVING_ASSET",
        "isServing": False,
        "ref": _failed_output_ref(node),
        "error": payload,
    }


def _failed_output_ref(node: PipelineV2RuntimeNode) -> JsonObject:
    fields = {
        "output.dataset": "outputDatasetRef",
        "output.media_set": "mediaSetRef",
        "output.semantic_index": "indexRef",
        "output.ontology": "mappingRef",
        "output.geospatial": "resourceRef",
    }
    field = fields.get(node.descriptor_id)
    resource_ref = node.config.get(field) if field is not None else None
    if node.descriptor_id == "output.dataset":
        return {"datasetRef": resource_ref}
    if node.descriptor_id == "output.media_set":
        return {"mediaSetRef": resource_ref}
    if node.descriptor_id == "output.geospatial":
        return {"resourceRef": resource_ref}
    return {
        "requestedResourceRef": resource_ref,
        "servingState": "CANDIDATE_NOT_COMMITTED",
        "servingAssetCreated": False,
    }


def _output_artifact_kind(node: PipelineV2RuntimeNode) -> str:
    return {
        "output.dataset": "dataset_version",
        "output.media_set": "media_set_selection",
        "output.semantic_index": "vector_index_generation",
        "output.ontology": "ontology_mapping",
        "output.geospatial": "geospatial_series",
    }.get(node.descriptor_id, "dataset_version")


def _output_plane(node: PipelineV2RuntimeNode) -> str:
    return {
        "output.dataset": "dataset",
        "output.media_set": "media",
        "output.semantic_index": "index",
        "output.ontology": "ontology",
        "output.geospatial": "geospatial",
    }.get(node.descriptor_id, "dataset")
