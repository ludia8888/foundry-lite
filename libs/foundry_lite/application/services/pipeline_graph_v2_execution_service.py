"""Production composition boundary for engine-neutral Pipeline Graph v2 runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.pipeline_graph_v2_execution_bindings import (
    ExactDatasetVersionReader,
    MediaCatalogService,
    MediaTransactionService,
    MediaUploadService,
    PipelineGraphV2ExecutionBindings,
    PipelineV2ContentChunking,
    PipelineV2DatasetIngest,
    PipelineV2DatasetRegistry,
    PipelineV2MediaIndexing,
    PipelineV2MediaProcessing,
    RuntimeEvidenceBoundary,
)
from foundry_lite.application.services.pipeline_graph_v2_execution_composition import (
    execute_pipeline_graph_v2,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RunResult,
)
from foundry_lite.domain.context import RequestContext


class PipelineGraphV2ExecutionService(CoreService):
    """Execute one immutable deployment plan through explicit runtime strategies."""

    required_dependencies = (
        "engine",
        "policy",
        "pipeline_execution_repository",
        "dataset_repository",
        "dataset_version_repository",
        "media_repository",
        "media_derivative_repository",
        "media_storage",
        "media_processor_registry",
        "embedding_model_adapter",
        "governed_semantic_model_port",
        "trained_model_inference_port",
        "semantic_row_cache_repository",
    )
    required_collaborators = (
        "content_unit_chunking_service",
        "dataset_ingest_service",
        "dataset_registry_service",
        "exact_dataset_version_reader_service",
        "media_indexing_service",
        "media_catalog_service",
        "media_processing_service",
        "media_transaction_service",
        "media_upload_service",
        "runtime_service",
    )
    content_unit_chunking_service: PipelineV2ContentChunking
    dataset_ingest_service: PipelineV2DatasetIngest
    dataset_registry_service: PipelineV2DatasetRegistry
    exact_dataset_version_reader_service: ExactDatasetVersionReader
    media_indexing_service: PipelineV2MediaIndexing
    media_catalog_service: MediaCatalogService
    media_processing_service: PipelineV2MediaProcessing
    media_transaction_service: MediaTransactionService
    media_upload_service: MediaUploadService
    runtime_service: RuntimeEvidenceBoundary

    def execute(
        self,
        ctx: RequestContext,
        *,
        run_id: str,
        pipeline_id: str,
        deployment_id: str,
        execution_plan: Mapping[str, object],
        target_node_ids: Sequence[str] = (),
    ) -> PipelineV2RunResult:
        return execute_pipeline_graph_v2(
            self._execution_bindings(),
            ctx,
            run_id=run_id,
            pipeline_id=pipeline_id,
            deployment_id=deployment_id,
            execution_plan=execution_plan,
            target_node_ids=target_node_ids,
        )

    def _execution_bindings(self) -> PipelineGraphV2ExecutionBindings:
        return PipelineGraphV2ExecutionBindings(
            engine=self.engine,
            policy=self.policy,
            pipeline_execution_repository=self.pipeline_execution_repository,
            dataset_repository=self.dataset_repository,
            dataset_version_repository=self.dataset_version_repository,
            media_repository=self.media_repository,
            media_derivative_repository=self.media_derivative_repository,
            media_storage=self.media_storage,
            media_processor_registry=self.media_processor_registry,
            embedding_model_adapter=self.embedding_model_adapter,
            governed_semantic_model_port=self.governed_semantic_model_port,
            trained_model_inference_port=self.trained_model_inference_port,
            semantic_row_cache_repository=self.semantic_row_cache_repository,
            content_unit_chunking_service=self.content_unit_chunking_service,
            dataset_ingest_service=self.dataset_ingest_service,
            dataset_registry_service=self.dataset_registry_service,
            exact_dataset_version_reader_service=self.exact_dataset_version_reader_service,
            media_indexing_service=self.media_indexing_service,
            media_catalog_service=self.media_catalog_service,
            media_processing_service=self.media_processing_service,
            media_transaction_service=self.media_transaction_service,
            media_upload_service=self.media_upload_service,
            runtime_service=self.runtime_service,
        )
