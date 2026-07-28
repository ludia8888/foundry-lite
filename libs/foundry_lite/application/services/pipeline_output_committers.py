"""Typed committer strategies for Dataset and governed candidate Pipeline nodes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.primitives import CommitResult, _now
from foundry_lite.application.services.pipeline_candidate_output_committer import (
    GovernedPipelineCandidateCommitter,
)
from foundry_lite.application.services.pipeline_node_execution_evidence import (
    PipelineNodeExecutionEvidence,
)
from foundry_lite.application.services.pipeline_node_execution_evidence_types import (
    PipelineNodeAttemptContext,
)
from foundry_lite.application.services.transform_runs import TransformRunPlan
from foundry_lite.application.services.transform_service import TransformService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation

JsonObject = dict[str, object]


@dataclass(frozen=True, slots=True)
class PipelineNodeCommitOutcome:
    output: JsonObject | None = None
    timeline_event: JsonObject | None = None
    failed_attempt: PipelineNodeAttemptContext | None = None
    error: Exception | None = None


class PipelineNodeCommitter(Protocol):
    def commit(self, item: Mapping[str, object]) -> PipelineNodeCommitOutcome: ...


class DatasetPipelineNodeCommitter:
    """Commit tabular nodes through the existing Dataset transaction protocol."""

    def __init__(
        self,
        transform_service: TransformService,
        evidence: PipelineNodeExecutionEvidence,
        ctx: RequestContext,
    ) -> None:
        self._transform_service = transform_service
        self._evidence = evidence
        self._ctx = ctx

    def commit(self, item: Mapping[str, object]) -> PipelineNodeCommitOutcome:
        plan = self._transform_service._prepare_pipeline_transform_run(self._ctx, str(item["apiName"]))
        try:
            attempt = self._evidence.start_attempt(item, plan)
        except Exception as exc:
            self._transform_service._abort_pipeline_transform_run(self._ctx, plan, exc)
            return PipelineNodeCommitOutcome(error=exc)
        try:
            result = self._execute(plan, attempt)
        except Exception as exc:
            return PipelineNodeCommitOutcome(failed_attempt=attempt, error=exc)
        output = _dataset_output(item, result) if item.get("isOutput") is True else None
        return PipelineNodeCommitOutcome(output=output, timeline_event=_dataset_timeline(item, result))

    def _execute(
        self,
        plan: TransformRunPlan,
        attempt: PipelineNodeAttemptContext,
    ) -> CommitResult:
        return self._transform_service._execute_pipeline_transform_run(
            self._ctx,
            plan,
            after_transform_commit=lambda transaction, commit: self._evidence.succeed(
                transaction,
                attempt,
                commit,
            ),
        )


class GovernedCandidatePipelineOutputCommitter:
    """Commit an immutable governance candidate while keeping the serving plane untouched."""

    def __init__(self, committer: GovernedPipelineCandidateCommitter) -> None:
        self._committer = committer

    def commit(self, item: Mapping[str, object]) -> PipelineNodeCommitOutcome:
        try:
            result = self._committer.commit(item)
        except Exception as exc:
            return PipelineNodeCommitOutcome(error=exc)
        return PipelineNodeCommitOutcome(output=result.output, timeline_event=result.timeline_event)


class PipelineNodeCommitterRegistry:
    """Dispatch only explicitly registered execution kinds; no implicit fallback."""

    def __init__(
        self,
        *,
        dataset: DatasetPipelineNodeCommitter,
        governed_candidate: GovernedCandidatePipelineOutputCommitter,
    ) -> None:
        self._committers: dict[str, PipelineNodeCommitter] = {
            "dataset_transform": dataset,
            "governed_output_candidate": governed_candidate,
        }

    def commit(self, item: Mapping[str, object]) -> PipelineNodeCommitOutcome:
        execution_kind = str(item.get("executionKind") or "dataset_transform")
        committer = self._committers.get(execution_kind)
        if committer is None:
            raise InvariantViolation(
                "pipeline compiled node has no registered committer",
                details={"executionKind": execution_kind, "nodeId": item.get("nodeId")},
            )
        return committer.commit(item)


def _dataset_timeline(item: Mapping[str, object], result: CommitResult) -> JsonObject:
    return {
        "event": "pipeline.node.succeeded",
        "at": _now(),
        "nodeId": item["nodeId"],
        "transformApiName": item["apiName"],
        "operationRunType": "transform",
        "outputVersionId": result.version_id,
    }


def _dataset_output(item: Mapping[str, object], result: CommitResult) -> JsonObject:
    return {
        "nodeId": item["nodeId"],
        "artifactKind": "dataset_version",
        "plane": "dataset",
        "status": "COMMITTED",
        "commitKind": "SERVING_ASSET",
        "isServing": True,
        "ref": {"datasetRef": item["datasetRef"], "versionId": result.version_id},
    }
