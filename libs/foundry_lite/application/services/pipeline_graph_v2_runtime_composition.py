"""Dispatcher composition for Pipeline Graph v2 execution."""

from foundry_lite.application.ports.virtual_table import VirtualTableRecord
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
from foundry_lite.application.services.pipeline_v2_runtime_virtual_table import (
    PipelineV2VirtualTableRuntime,
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
        virtual_tables=_build_virtual_table_runtime(service, ctx),
        media=build_pipeline_v2_media_runtime(service, ctx, run_id),
        geospatial=PipelineV2GeospatialRuntime(
            dataset_registry=service.dataset_registry_service,
            dataset_ingest=service.dataset_ingest_service,
            ctx=ctx,
            run_id=run_id,
            execution_lease_guard=service.execution_lease_guard,
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
            model_refs=plan.model_refs,
            transaction_manager=service.engine,
            media_repository=service.media_repository,
            ctx=ctx,
        ),
    )


def _build_virtual_table_runtime(
    service: PipelineGraphV2ExecutionBindings,
    ctx: RequestContext,
) -> PipelineV2VirtualTableRuntime:
    return PipelineV2VirtualTableRuntime(
        tenant_id=ctx.tenant_id,
        resolver=_VirtualTableResolver(service),
        reader=service.virtual_table_reader,
    )


class _VirtualTableResolver:
    """Resolve a plan's virtualTableRef to its pointer and a connection URL from the vault.

    The pointer stores a secret *reference*, never a connection string. A URL with embedded
    credentials on the record would be copied into API responses, plan payloads, and build
    artifacts — the registry row is not a place secrets can live. This mirrors how database
    sources already resolve ``databaseUrlSecretRef`` through the vault at use time.
    """

    def __init__(self, service: PipelineGraphV2ExecutionBindings) -> None:
        self._service = service

    def resolve(self, *, tenant_id: str, rid: str) -> tuple[VirtualTableRecord, str] | None:
        record = self._service.virtual_table_repository.get(tenant_id=tenant_id, rid=rid)
        if record is None:
            return None
        secret_ref = record.config.get("databaseUrlSecretRef")
        if not isinstance(secret_ref, str) or not secret_ref:
            return None
        return record, self._service.secret_vault.get_secret(secret_ref).value
