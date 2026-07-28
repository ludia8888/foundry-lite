"""AIP dependency bundle kept separate from the core dependency facade."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.ports import AiEvalRepository, AiRunRepository, ContextProvider
from foundry_lite.application.ports.citation_source import CitationSourceVerifier
from foundry_lite.application.ports.completion_model import CompletionModelAdapter
from foundry_lite.application.ports.embedding_model import EmbeddingModelAdapter
from foundry_lite.application.ports.language_model import GovernedSemanticModelPort, LanguageModelAdapter
from foundry_lite.application.ports.model_registry_repository import ModelCatalogSeed, ModelRegistryRepository
from foundry_lite.application.ports.semantic_row_cache_repository import SemanticRowCacheRepository
from foundry_lite.application.ports.tool_executor import ToolExecutor
from foundry_lite.application.ports.trained_model_inference import TrainedModelInferencePort
from foundry_lite.application.ports.vision_embedding_model import VisionEmbeddingModelAdapter


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
