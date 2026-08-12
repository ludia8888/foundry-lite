"""AIP dependency bundle kept separate from the core dependency facade."""

from __future__ import annotations

from dataclasses import dataclass, field

from foundry_lite.application.dependency_aip_ports import (
    CitationSourceVerifier,
    CompletionModelAdapter,
    EmbeddingModelAdapter,
    GovernedSemanticModelPort,
    LanguageModelAdapter,
    ModelRegistryRepository,
    SemanticRowCacheRepository,
    ToolExecutor,
    TrainedModelInferencePort,
    VisionEmbeddingModelAdapter,
)
from foundry_lite.application.dependency_release import GovernedReleaseDependencies
from foundry_lite.application.ports import AiEvalRepository, AiRunRepository, ContextProvider
from foundry_lite.application.ports.model_registry_repository import ModelCatalogSeed


@dataclass(frozen=True)
class AipDependencies:
    ai_eval_repository: AiEvalRepository
    ai_run_repository: AiRunRepository
    embedding_model_adapter: EmbeddingModelAdapter
    completion_model_adapter: CompletionModelAdapter
    vision_embedding_model_adapter: VisionEmbeddingModelAdapter
    language_model_adapter: LanguageModelAdapter
    model_registry_repository: ModelRegistryRepository
    semantic_row_cache_repository: SemanticRowCacheRepository
    context_provider: ContextProvider
    prompt_artifact_store: object
    citation_source_verifier: CitationSourceVerifier
    tool_executor: ToolExecutor
    governed_semantic_model_port: GovernedSemanticModelPort | None = None
    trained_model_inference_port: TrainedModelInferencePort | None = None
    model_catalog_seed: ModelCatalogSeed | None = None
    governed_release: GovernedReleaseDependencies = field(default_factory=GovernedReleaseDependencies)
