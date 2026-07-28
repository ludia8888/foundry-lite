"""Dispatcher composition for Pipeline Graph v2 execution."""

from foundry_lite.application.services.pipeline_graph_v2_execution_bindings import (
    PipelineGraphV2ExecutionBindings,
)
from foundry_lite.application.services.pipeline_graph_v2_media_composition import (
    build_pipeline_v2_media_runtime,
)
from foundry_lite.application.services.pipeline_graph_v2_row_composition import (
    build_pipeline_v2_row_runtime,
)
from foundry_lite.application.services.pipeline_graph_v2_runtime_dispatch import (
    PipelineGraphV2RuntimeDispatcher,
)
from foundry_lite.application.services.pipeline_graph_v2_runtime_plan import (
    PipelineGraphV2RuntimePlan,
)
from foundry_lite.application.services.pipeline_v2_runtime_dataset import (
    PipelineV2DatasetRuntime,
)
from foundry_lite.application.services.pipeline_v2_runtime_geospatial import (
    PipelineV2GeospatialRuntime,
)
from foundry_lite.application.services.pipeline_v2_runtime_trained_model import (
    PipelineV2TrainedModelRuntime,
)
from foundry_lite.domain.context import RequestContext


def build_pipeline_graph_v2_dispatcher(
    service: PipelineGraphV2ExecutionBindings,
    ctx: RequestContext,
    *,
    run_id: str,
    pipeline_id: str,
    deployment_id: str,
    plan: PipelineGraphV2RuntimePlan,
) -> PipelineGraphV2RuntimeDispatcher:
    return PipelineGraphV2RuntimeDispatcher(
        source_contracts=plan.source_contracts,
        dataset_sources=PipelineV2DatasetRuntime(
            reader=service.exact_dataset_version_reader_service,
            ctx=ctx,
        ),
        media=build_pipeline_v2_media_runtime(service, ctx, run_id),
        geospatial=PipelineV2GeospatialRuntime(
            dataset_registry=service.dataset_registry_service,
            dataset_ingest=service.dataset_ingest_service,
            ctx=ctx,
            run_id=run_id,
        ),
        rows=build_pipeline_v2_row_runtime(
            service,
            ctx,
            run_id=run_id,
            pipeline_id=pipeline_id,
            deployment_id=deployment_id,
        ),
        trained_models=PipelineV2TrainedModelRuntime(
            adapter=service.trained_model_inference_port,
            run_id=run_id,
        ),
    )
