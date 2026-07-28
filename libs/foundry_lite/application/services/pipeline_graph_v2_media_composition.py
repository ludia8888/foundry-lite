"""Media runtime composition for Pipeline Graph v2 execution."""

from foundry_lite.application.services.pipeline_graph_v2_execution_bindings import (
    PipelineGraphV2ExecutionBindings,
)
from foundry_lite.application.services.pipeline_media_set_output import (
    PipelineMediaSetOutputCommitter,
)
from foundry_lite.application.services.pipeline_v2_runtime_media import (
    PipelineV2MediaRuntime,
)
from foundry_lite.domain.context import RequestContext


def build_pipeline_v2_media_runtime(
    service: PipelineGraphV2ExecutionBindings,
    ctx: RequestContext,
    run_id: str,
) -> PipelineV2MediaRuntime:
    return PipelineV2MediaRuntime(
        engine=service.engine,
        media_repository=service.media_repository,
        processor_registry=service.media_processor_registry,
        processing=service.media_processing_service,
        indexing=service.media_indexing_service,
        chunking=service.content_unit_chunking_service,
        embedding_model=service.embedding_model_adapter,
        output_committer=PipelineMediaSetOutputCommitter(
            engine=service.engine,
            media_repository=service.media_repository,
            media_derivative_repository=service.media_derivative_repository,
            media_storage=service.media_storage,
            media_catalog=service.media_catalog_service,
            media_transactions=service.media_transaction_service,
            media_uploads=service.media_upload_service,
            ctx=ctx,
            run_id=run_id,
            execution_lease_guard=service.execution_lease_guard,
        ),
        ctx=ctx,
        run_id=run_id,
    )
