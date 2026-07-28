"""Descriptor-owned dispatch for production Pipeline Graph v2 nodes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeNode,
    PipelineV2SourceContract,
    RuntimeInputs,
)
from foundry_lite.application.services.pipeline_v2_runtime_geospatial import (
    PipelineV2GeospatialRuntime,
)
from foundry_lite.application.services.pipeline_v2_runtime_media import (
    PipelineV2MediaRuntime,
)
from foundry_lite.application.services.pipeline_v2_runtime_rows import (
    PipelineV2RowRuntime,
)
from foundry_lite.application.services.pipeline_v2_runtime_trained_model import (
    PipelineV2TrainedModelRuntime,
)
from foundry_lite.domain.errors import InvariantViolation


class PipelineV2StructuredSourceRuntime(Protocol):
    """Exact committed Dataset, stream-checkpoint, and geospatial source boundary."""

    def source_dataset(
        self,
        node: PipelineV2RuntimeNode,
        contract: PipelineV2SourceContract,
    ) -> PipelineV2RuntimeArtifact: ...

    def source_stream(
        self,
        node: PipelineV2RuntimeNode,
        contract: PipelineV2SourceContract,
    ) -> PipelineV2RuntimeArtifact: ...

    def source_geospatial(
        self,
        node: PipelineV2RuntimeNode,
        contract: PipelineV2SourceContract,
    ) -> PipelineV2RuntimeArtifact: ...


class PipelineGraphV2RuntimeDispatcher:
    """Fail-closed dispatch with no descriptor or engine fallback."""

    def __init__(
        self,
        *,
        source_contracts: Mapping[str, PipelineV2SourceContract],
        dataset_sources: PipelineV2StructuredSourceRuntime,
        media: PipelineV2MediaRuntime,
        geospatial: PipelineV2GeospatialRuntime,
        rows: PipelineV2RowRuntime,
        trained_models: PipelineV2TrainedModelRuntime,
    ) -> None:
        self._source_contracts = source_contracts
        self._dataset_sources = dataset_sources
        self._media = media
        self._geospatial = geospatial
        self._rows = rows
        self._trained_models = trained_models

    def execute(
        self,
        node: PipelineV2RuntimeNode,
        inputs: RuntimeInputs,
    ) -> PipelineV2RuntimeArtifact:
        descriptor = node.descriptor_id
        if descriptor == "source.dataset":
            return self._dataset_sources.source_dataset(
                node,
                self._source_contract(node),
            )
        if descriptor == "source.media_set":
            return self._media.source_media(node, self._source_contract(node))
        if descriptor == "source.stream":
            return self._dataset_sources.source_stream(node, self._source_contract(node))
        if descriptor == "source.geospatial":
            return self._dataset_sources.source_geospatial(node, self._source_contract(node))
        return self._execute_transform_or_output(node, inputs)

    def _execute_transform_or_output(
        self,
        node: PipelineV2RuntimeNode,
        inputs: RuntimeInputs,
    ) -> PipelineV2RuntimeArtifact:
        handlers = {
            "transform.media": self._media.process_media,
            "transform.document_extract": self._media.document_extract,
            "transform.chunk": self._media.chunk_content,
            "transform.embedding.text": self._media.embed_text,
            "output.media_set": self._media.output_media_set,
            "transform.use_llm": self._rows.use_llm,
            "transform.trained_model": self._trained_models.execute,
            "bridge.media_to_table_rows": self._rows.media_to_rows,
            "bridge.content_units_to_dataset": self._rows.content_units_to_rows,
            "bridge.stream_to_dataset": self._rows.stream_to_rows,
            "output.dataset": self._rows.output_dataset,
            "output.geospatial": self._geospatial.output_geospatial,
        }
        handler = handlers.get(node.descriptor_id)
        if handler is None:
            raise InvariantViolation(
                "pipeline Graph v2 descriptor has no production executor",
                details={
                    "nodeId": node.node_id,
                    "descriptorId": node.descriptor_id,
                },
            )
        return handler(node, inputs)

    def _source_contract(
        self,
        node: PipelineV2RuntimeNode,
    ) -> PipelineV2SourceContract:
        contract = self._source_contracts.get(node.node_id)
        if contract is None:
            raise InvariantViolation(
                "pipeline source contract is missing at runtime",
                details={"nodeId": node.node_id},
            )
        return contract
