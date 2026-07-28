"""Row runtime composition for Pipeline Graph v2 execution."""

from foundry_lite.application.services.pipeline_graph_v2_execution_bindings import (
    PipelineGraphV2ExecutionBindings,
)
from foundry_lite.application.services.pipeline_semantic_row_cache import (
    SemanticRowCacheSession,
    semantic_resource_security_policy_fingerprint,
)
from foundry_lite.application.services.pipeline_v2_runtime_rows import (
    PipelineV2RowRuntime,
)
from foundry_lite.domain.context import RequestContext


def build_pipeline_v2_row_runtime(
    service: PipelineGraphV2ExecutionBindings,
    ctx: RequestContext,
    *,
    run_id: str,
    pipeline_id: str,
    deployment_id: str,
) -> PipelineV2RowRuntime:
    decision = service.policy.decide(ctx, "pipeline:run")
    return PipelineV2RowRuntime(
        dataset_registry=service.dataset_registry_service,
        dataset_ingest=service.dataset_ingest_service,
        model_gateway=service.governed_semantic_model_port,
        semantic_cache=SemanticRowCacheSession(
            transaction_manager=service.engine,
            repository=service.semantic_row_cache_repository,
            model_gateway=service.governed_semantic_model_port,
        ),
        ctx=ctx,
        run_id=run_id,
        pipeline_id=pipeline_id,
        deployment_id=deployment_id,
        resource_security_policy_fingerprint=semantic_resource_security_policy_fingerprint(
            permission="pipeline:run",
            policy_reason=decision.reason,
            sensitive_fields=tuple(service.policy.sensitive_column_names(ctx)),
            masked_fields=tuple(service.policy.masked_column_names(ctx)),
        ),
    )
