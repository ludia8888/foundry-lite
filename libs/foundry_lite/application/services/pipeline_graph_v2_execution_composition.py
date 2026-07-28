"""Engine-neutral orchestration for one immutable Pipeline Graph v2 plan."""

from collections.abc import Mapping, Sequence

from foundry_lite.application.services.pipeline_graph_v2_evidence_composition import (
    build_pipeline_graph_v2_candidates,
    build_pipeline_graph_v2_error_payload,
    build_pipeline_graph_v2_evidence,
)
from foundry_lite.application.services.pipeline_graph_v2_execution_bindings import (
    PipelineGraphV2ExecutionBindings,
)
from foundry_lite.application.services.pipeline_graph_v2_runtime_composition import (
    build_pipeline_graph_v2_dispatcher,
)
from foundry_lite.application.services.pipeline_graph_v2_runtime_executor import (
    PipelineGraphV2RuntimeExecutor,
)
from foundry_lite.application.services.pipeline_graph_v2_runtime_plan import (
    pipeline_graph_v2_runtime_plan,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RunResult,
)
from foundry_lite.domain.context import RequestContext


def execute_pipeline_graph_v2(
    service: PipelineGraphV2ExecutionBindings,
    ctx: RequestContext,
    *,
    run_id: str,
    pipeline_id: str,
    deployment_id: str,
    execution_plan: Mapping[str, object],
    target_node_ids: Sequence[str] = (),
) -> PipelineV2RunResult:
    runtime_plan = pipeline_graph_v2_runtime_plan(
        execution_plan,
        target_node_ids=target_node_ids,
    )
    dispatcher = build_pipeline_graph_v2_dispatcher(
        service,
        ctx,
        run_id=run_id,
        pipeline_id=pipeline_id,
        deployment_id=deployment_id,
        plan=runtime_plan,
    )
    return PipelineGraphV2RuntimeExecutor(
        run_id=run_id,
        nodes=runtime_plan.nodes,
        edges=runtime_plan.edges,
        dispatcher=dispatcher,
        evidence=build_pipeline_graph_v2_evidence(service, ctx, run_id),
        candidates=build_pipeline_graph_v2_candidates(
            service,
            ctx,
            run_id=run_id,
            execution_plan=execution_plan,
        ),
        error_payload=build_pipeline_graph_v2_error_payload(service, ctx, run_id),
    ).execute()
