"""Durable execution evidence composition for Pipeline Graph v2."""

from collections.abc import Callable, Mapping

from foundry_lite.application.services.pipeline_candidate_output_committer import (
    GovernedPipelineCandidateCommitter,
)
from foundry_lite.application.services.pipeline_graph_v2_execution_bindings import (
    PipelineGraphV2ExecutionBindings,
)
from foundry_lite.application.services.pipeline_graph_v2_execution_evidence import (
    PipelineGraphV2ExecutionEvidenceWriter,
)
from foundry_lite.domain.context import RequestContext

ErrorPayload = Callable[[Exception], Mapping[str, object]]


def build_pipeline_graph_v2_evidence(
    service: PipelineGraphV2ExecutionBindings,
    ctx: RequestContext,
    run_id: str,
) -> PipelineGraphV2ExecutionEvidenceWriter:
    return PipelineGraphV2ExecutionEvidenceWriter(
        transaction_manager=service.engine,
        repository=service.pipeline_execution_repository,
        ctx=ctx,
        run_id=run_id,
        execution_lease_guard=service.execution_lease_guard,
    )


def build_pipeline_graph_v2_candidates(
    service: PipelineGraphV2ExecutionBindings,
    ctx: RequestContext,
    *,
    run_id: str,
    execution_plan: Mapping[str, object],
) -> GovernedPipelineCandidateCommitter:
    return GovernedPipelineCandidateCommitter(
        transaction_manager=service.engine,
        repository=service.pipeline_execution_repository,
        dataset_repository=service.dataset_repository,
        dataset_version_repository=service.dataset_version_repository,
        runtime_service=service.runtime_service,
        ctx=ctx,
        run_id=run_id,
        execution_plan=execution_plan,
        execution_lease_guard=service.execution_lease_guard,
    )


def build_pipeline_graph_v2_error_payload(
    service: PipelineGraphV2ExecutionBindings,
    ctx: RequestContext,
    run_id: str,
) -> ErrorPayload:
    return lambda exc: service.runtime_service._error_payload(
        exc,
        ctx,
        run_id=run_id,
    )
