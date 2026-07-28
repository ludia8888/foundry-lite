from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineNodeRunRow,
    PipelineRunArtifactRow,
)
from foundry_lite.application.services.pipeline_candidate_output_committer import (
    GovernedPipelineCandidateCommitter,
)
from foundry_lite.application.services.pipeline_candidate_output_records import (
    PipelineCandidateCommitResult,
)
from foundry_lite.application.services.pipeline_graph_v2_execution_evidence import (
    PipelineGraphV2ExecutionEvidenceWriter,
)
from foundry_lite.application.services.pipeline_graph_v2_execution_evidence_types import (
    PipelineGraphV2ArtifactSpec,
    PipelineGraphV2AttemptContext,
    PipelineGraphV2InputArtifact,
)
from foundry_lite.application.services.pipeline_graph_v2_run_completion import (
    graph_v2_terminal_state,
)
from foundry_lite.application.services.pipeline_graph_v2_runtime_executor import (
    PipelineGraphV2RuntimeExecutor,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeEdge,
    PipelineV2RuntimeNode,
    RuntimeInputs,
)

_RUN_ID = "pipeline-run-v2"
_COMMITTED_AT = "2026-07-17T12:00:00+00:00"


class RecordingDispatcher:
    def __init__(
        self,
        outcomes: Mapping[str, PipelineV2RuntimeArtifact | Exception],
    ) -> None:
        self._outcomes = outcomes
        self.calls: list[tuple[str, dict[str, tuple[PipelineV2RuntimeArtifact, ...]]]] = []

    def execute(
        self,
        node: PipelineV2RuntimeNode,
        inputs: RuntimeInputs,
    ) -> PipelineV2RuntimeArtifact:
        self.calls.append(
            (
                node.node_id,
                {port_id: tuple(artifacts) for port_id, artifacts in inputs.items()},
            )
        )
        outcome = self._outcomes[node.node_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@dataclass(frozen=True, slots=True)
class SkipCall:
    node_id: str
    input_artifacts: tuple[PipelineGraphV2InputArtifact, ...]
    error: Mapping[str, object]


class RecordingEvidence:
    def __init__(self) -> None:
        self.starts: list[PipelineGraphV2AttemptContext] = []
        self.successes: list[tuple[PipelineGraphV2AttemptContext, PipelineGraphV2ArtifactSpec]] = []
        self.failures: list[tuple[PipelineGraphV2AttemptContext, Mapping[str, object]]] = []
        self.skips: list[SkipCall] = []

    def start(
        self,
        *,
        node_id: str,
        descriptor_id: str,
        spec_version: int,
        executor_profile: str,
        input_artifacts: tuple[PipelineGraphV2InputArtifact, ...],
    ) -> PipelineGraphV2AttemptContext:
        attempt = PipelineGraphV2AttemptContext(
            node_id=node_id,
            descriptor_id=descriptor_id,
            spec_version=spec_version,
            executor_profile=executor_profile,
            node_run_id=f"node-run:{node_id}",
            attempt_id=f"attempt:{node_id}",
            attempt_number=1,
            input_artifacts=input_artifacts,
        )
        self.starts.append(attempt)
        return attempt

    def succeed(
        self,
        attempt: PipelineGraphV2AttemptContext,
        artifact: PipelineGraphV2ArtifactSpec,
    ) -> PipelineRunArtifactRow:
        self.successes.append((attempt, artifact))
        return _artifact_row(attempt, artifact)

    def fail(
        self,
        attempt: PipelineGraphV2AttemptContext,
        error: Mapping[str, object],
    ) -> None:
        self.failures.append((attempt, dict(error)))

    def skip(
        self,
        *,
        node_id: str,
        descriptor_id: str,
        spec_version: int,
        input_artifacts: tuple[PipelineGraphV2InputArtifact, ...],
        error: Mapping[str, object],
    ) -> PipelineNodeRunRow:
        self.skips.append(
            SkipCall(
                node_id=node_id,
                input_artifacts=input_artifacts,
                error=dict(error),
            )
        )
        return _skipped_node_row(node_id, descriptor_id, spec_version, error)


class RecordingCandidates:
    def __init__(
        self,
        outcomes: Mapping[str, PipelineCandidateCommitResult | Exception],
    ) -> None:
        self._outcomes = outcomes
        self.items: list[dict[str, object]] = []

    def commit(self, item: Mapping[str, object]) -> PipelineCandidateCommitResult:
        payload = dict(item)
        self.items.append(payload)
        outcome = self._outcomes[str(payload["nodeId"])]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_executes_named_port_dag_and_propagates_durable_artifact_passports() -> None:
    left = _node("left", "source", "source.dataset")
    right = _node("right", "source", "source.dataset")
    merge = _node("merge", "transform", "content.merge")
    output = _node(
        "dataset-output",
        "output",
        "output.dataset",
        {"outputDatasetRef": "curated.documents"},
    )
    join_artifact = _artifact(
        "merge",
        port_id="content",
        manifest={
            "processorId": "document-merge",
            "model": "claude-sonnet",
            "promptVersionId": "prompt-v3",
            "outputSchema": {"type": "object"},
        },
        items=({"byteSize": 12}, {"byteSize": 8}),
    )
    dispatcher = RecordingDispatcher(
        {
            "left": _artifact("left", port_id="rows"),
            "right": _artifact("right", port_id="rows"),
            "merge": join_artifact,
            "dataset-output": _dataset_output_artifact("dataset-output"),
        }
    )
    evidence = RecordingEvidence()
    edges = (
        _edge("right-edge", "right", "rows", "merge", "right"),
        _edge("left-edge", "left", "rows", "merge", "left"),
        _edge("output-edge", "merge", "content", "dataset-output", "input"),
    )

    result = _executor(
        nodes=(left, right, merge, output),
        edges=edges,
        dispatcher=dispatcher,
        evidence=evidence,
    ).execute()

    merge_inputs = dict(dispatcher.calls[2][1])
    assert list(merge_inputs) == ["left", "right"]
    assert merge_inputs["left"][0].node_id == "left"
    assert merge_inputs["right"][0].node_id == "right"
    merge_attempt = evidence.starts[2]
    assert [item.target_port_id for item in merge_attempt.input_artifacts] == [
        "left",
        "right",
    ]
    assert [item.artifact_id for item in merge_attempt.input_artifacts] == [
        "artifact:left:rows",
        "artifact:right:rows",
    ]
    merge_spec = evidence.successes[2][1]
    assert dict(merge_spec.pins) == {
        "processorId": "document-merge",
        "model": "claude-sonnet",
        "promptVersionId": "prompt-v3",
        "outputSchema": {"type": "object"},
    }
    assert merge_spec.item_count == 2
    assert merge_spec.byte_count == 20
    assert merge_spec.idempotency_key == (f"{_RUN_ID}:merge:content:{join_artifact.content_fingerprint}")
    assert result.outputs[0]["status"] == "COMMITTED"
    assert result.outputs[0]["isServing"] is True
    output_ref = cast(Mapping[str, object], result.outputs[0]["ref"])
    assert output_ref["artifactId"] == "artifact:dataset-output:dataset"
    assert result.timeline[-1]["event"] == "pipeline.graph_v2.succeeded"
    assert graph_v2_terminal_state(result).status == "succeeded"


def test_continues_independent_branch_and_skips_all_failed_descendants() -> None:
    nodes = (
        _node("failed-source", "source", "source.dataset"),
        _node("blocked-transform", "transform", "table.select"),
        _node(
            "blocked-output",
            "output",
            "output.dataset",
            {"outputDatasetRef": "curated.blocked"},
        ),
        _node("healthy-source", "source", "source.dataset"),
        _node(
            "healthy-output",
            "output",
            "output.dataset",
            {"outputDatasetRef": "curated.healthy"},
        ),
    )
    dispatcher = RecordingDispatcher(
        {
            "failed-source": RuntimeError("source unavailable"),
            "healthy-source": _artifact("healthy-source", port_id="rows"),
            "healthy-output": _dataset_output_artifact("healthy-output"),
        }
    )
    evidence = RecordingEvidence()
    edges = (
        _edge(
            "blocked-transform-edge",
            "failed-source",
            "rows",
            "blocked-transform",
            "input",
        ),
        _edge(
            "blocked-output-edge",
            "blocked-transform",
            "rows",
            "blocked-output",
            "input",
        ),
        _edge(
            "healthy-output-edge",
            "healthy-source",
            "rows",
            "healthy-output",
            "input",
        ),
    )

    result = _executor(
        nodes=nodes,
        edges=edges,
        dispatcher=dispatcher,
        evidence=evidence,
    ).execute()

    assert [node_id for node_id, _inputs in dispatcher.calls] == [
        "failed-source",
        "healthy-source",
        "healthy-output",
    ]
    assert result.failed_node_id == "failed-source"
    assert result.skipped_node_ids == ("blocked-transform", "blocked-output")
    assert [call.node_id for call in evidence.skips] == [
        "blocked-transform",
        "blocked-output",
    ]
    assert evidence.skips[0].error["details"] == {
        "nodeId": "blocked-transform",
        "sourceNodeIds": ["failed-source"],
    }
    assert evidence.skips[1].error["details"] == {
        "nodeId": "blocked-output",
        "sourceNodeIds": ["blocked-transform"],
    }
    assert [output["status"] for output in result.outputs] == [
        "FAILED",
        "COMMITTED",
    ]
    assert result.timeline[-1]["event"] == "pipeline.graph_v2.partial"
    assert result.timeline[-1]["committedOutputCount"] == 1
    assert graph_v2_terminal_state(result).status == "partial"


def test_candidate_failure_writes_evidence_and_failed_only_outputs_stay_failed() -> None:
    source = _node("content-source", "source", "source.dataset")
    candidate = _node(
        "semantic-index",
        "output",
        "output.semantic_index",
        {"indexRef": "search.documents"},
    )
    downstream = _node(
        "downstream-output",
        "output",
        "output.dataset",
        {"outputDatasetRef": "curated.downstream"},
    )
    evidence = RecordingEvidence()
    candidates = RecordingCandidates({"semantic-index": RuntimeError("candidate validation failed")})
    edges = (
        _edge(
            "candidate-edge",
            "content-source",
            "content",
            "semantic-index",
            "content",
        ),
        _edge(
            "downstream-edge",
            "semantic-index",
            "index",
            "downstream-output",
            "input",
        ),
    )

    result = _executor(
        nodes=(source, candidate, downstream),
        edges=edges,
        dispatcher=RecordingDispatcher({"content-source": _artifact("content-source", port_id="content")}),
        evidence=evidence,
        candidates=candidates,
    ).execute()

    candidate_attempt = evidence.starts[1]
    assert candidate_attempt.node_id == "semantic-index"
    assert len(candidate_attempt.input_artifacts) == 1
    assert candidate_attempt.input_artifacts[0].source_node_id == "content-source"
    assert candidate_attempt.input_artifacts[0].target_port_id == "content"
    assert evidence.failures[-1][0] == candidate_attempt
    assert evidence.failures[-1][1] == {
        "code": "RuntimeError",
        "message": "candidate validation failed",
    }
    assert result.failed_node_id == "semantic-index"
    assert result.skipped_node_ids == ("downstream-output",)
    assert all(output["status"] == "FAILED" for output in result.outputs)
    assert result.timeline[-1]["event"] == "pipeline.graph_v2.failed"
    assert result.timeline[-1]["committedOutputCount"] == 0
    assert graph_v2_terminal_state(result).status == "failed"


def test_committed_governed_candidate_makes_mixed_output_run_partial() -> None:
    source = _node("content-source", "source", "source.dataset")
    candidate = _node(
        "semantic-index",
        "output",
        "output.semantic_index",
        {"indexRef": "search.documents"},
    )
    failed_output = _node(
        "failed-output",
        "output",
        "output.dataset",
        {"outputDatasetRef": "curated.failed"},
    )
    candidate_result = PipelineCandidateCommitResult(
        output={
            "nodeId": "semantic-index",
            "artifactKind": "vector_index_generation",
            "plane": "index",
            "status": "COMMITTED",
            "commitKind": "GOVERNED_CANDIDATE",
            "isServing": False,
            "ref": {
                "artifactId": "artifact:semantic-index:index",
                "indexRef": "search.documents",
            },
        },
        timeline_event={
            "event": "pipeline.node.succeeded",
            "at": _COMMITTED_AT,
            "nodeId": "semantic-index",
        },
    )
    candidates = RecordingCandidates({"semantic-index": candidate_result})
    evidence = RecordingEvidence()

    result = _executor(
        nodes=(source, candidate, failed_output),
        edges=(
            _edge(
                "candidate-edge",
                "content-source",
                "content",
                "semantic-index",
                "content",
            ),
        ),
        dispatcher=RecordingDispatcher(
            {
                "content-source": _artifact("content-source", port_id="content"),
                "failed-output": RuntimeError("dataset commit failed"),
            }
        ),
        evidence=evidence,
        candidates=candidates,
    ).execute()

    assert candidates.items == [
        {
            "nodeId": "semantic-index",
            "descriptorId": "output.semantic_index",
            "specVersion": 1,
            "resourceRef": "search.documents",
            "config": {"indexRef": "search.documents"},
            "artifactKind": "vector_index_generation",
            "plane": "index",
            "portId": "index",
            "candidateType": "semantic_index_generation",
        }
    ]
    assert [output["status"] for output in result.outputs] == [
        "COMMITTED",
        "FAILED",
    ]
    assert result.outputs[0]["isServing"] is False
    assert result.timeline[-1]["event"] == "pipeline.graph_v2.partial"
    assert graph_v2_terminal_state(result).status == "partial"


def _executor(
    *,
    nodes: tuple[PipelineV2RuntimeNode, ...],
    edges: tuple[PipelineV2RuntimeEdge, ...],
    dispatcher: RecordingDispatcher,
    evidence: RecordingEvidence,
    candidates: RecordingCandidates | None = None,
) -> PipelineGraphV2RuntimeExecutor:
    candidate_committer = candidates or RecordingCandidates({})
    return PipelineGraphV2RuntimeExecutor(
        run_id=_RUN_ID,
        nodes=nodes,
        edges=edges,
        dispatcher=dispatcher,
        evidence=cast(PipelineGraphV2ExecutionEvidenceWriter, evidence),
        candidates=cast(GovernedPipelineCandidateCommitter, candidate_committer),
        error_payload=_error_payload,
    )


def _node(
    node_id: str,
    kind: str,
    descriptor_id: str,
    config: Mapping[str, object] | None = None,
) -> PipelineV2RuntimeNode:
    return PipelineV2RuntimeNode(
        node_id=node_id,
        kind=kind,
        descriptor_id=descriptor_id,
        spec_version=1,
        runtime_capability="python.test.v2",
        config=dict(config or {}),
    )


def _edge(
    edge_id: str,
    source_node_id: str,
    source_port_id: str,
    target_node_id: str,
    target_port_id: str,
) -> PipelineV2RuntimeEdge:
    return PipelineV2RuntimeEdge(
        edge_id=edge_id,
        source_node_id=source_node_id,
        source_port_id=source_port_id,
        target_node_id=target_node_id,
        target_port_id=target_port_id,
    )


def _artifact(
    node_id: str,
    *,
    port_id: str,
    artifact_kind: str = "content_unit_set",
    plane: str = "content",
    manifest: Mapping[str, object] | None = None,
    items: tuple[dict[str, object], ...] = ({"value": "row"},),
    artifact_ref: Mapping[str, object] | None = None,
    is_serving: bool = False,
) -> PipelineV2RuntimeArtifact:
    return PipelineV2RuntimeArtifact(
        node_id=node_id,
        descriptor_id=f"descriptor.{node_id}",
        spec_version=1,
        port_id=port_id,
        artifact_kind=artifact_kind,
        plane=plane,
        items=items,
        artifact_ref=dict(artifact_ref or {"generation": f"generation:{node_id}"}),
        manifest=dict(manifest or {"commitKind": "INTERMEDIATE"}),
        security_envelope={"classification": "INTERNAL"},
        status="COMMITTED",
        is_serving=is_serving,
        committed_at=_COMMITTED_AT,
    )


def _dataset_output_artifact(node_id: str) -> PipelineV2RuntimeArtifact:
    return _artifact(
        node_id,
        port_id="dataset",
        artifact_kind="dataset_version",
        plane="dataset",
        artifact_ref={
            "datasetRef": f"curated.{node_id}",
            "versionId": f"version:{node_id}",
        },
        is_serving=True,
    )


def _artifact_row(
    attempt: PipelineGraphV2AttemptContext,
    artifact: PipelineGraphV2ArtifactSpec,
) -> PipelineRunArtifactRow:
    return PipelineRunArtifactRow(
        id=f"artifact:{attempt.node_id}:{artifact.port_id}",
        tenant_id="tenant-v2",
        run_id=_RUN_ID,
        node_run_id=attempt.node_run_id,
        node_id=attempt.node_id,
        port_id=artifact.port_id,
        artifact_kind=artifact.artifact_kind.value,
        plane=artifact.plane,
        artifact_ref=dict(artifact.artifact_ref),
        manifest=dict(artifact.manifest),
        content_fingerprint=artifact.content_fingerprint,
        security_envelope=dict(artifact.security_envelope),
        status=artifact.status,
        is_serving=artifact.is_serving,
        idempotency_key=artifact.idempotency_key,
        committed_at=artifact.committed_at,
        created_at=_COMMITTED_AT,
    )


def _skipped_node_row(
    node_id: str,
    descriptor_id: str,
    spec_version: int,
    error: Mapping[str, object],
) -> PipelineNodeRunRow:
    return PipelineNodeRunRow(
        id=f"node-run:{node_id}",
        tenant_id="tenant-v2",
        run_id=_RUN_ID,
        node_id=node_id,
        descriptor_id=descriptor_id,
        spec_version=str(spec_version),
        status="SKIPPED",
        attempt_count=0,
        input_artifacts=[],
        output_artifacts=[],
        error=dict(error),
        started_at=None,
        completed_at=_COMMITTED_AT,
        created_at=_COMMITTED_AT,
        updated_at=_COMMITTED_AT,
    )


def _error_payload(exc: Exception) -> Mapping[str, object]:
    return {"code": type(exc).__name__, "message": str(exc)}
