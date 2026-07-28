"""Committed Media, processor, Content Unit, and index handlers for Graph v2."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from foundry_lite.application.services.media.content_chunking import (
    CommittedContentUnitSetRef,
    ContentChunkOutcome,
    ContentChunkSpec,
)
from foundry_lite.application.services.media.indexing import IndexingOutcome
from foundry_lite.application.services.media.processing import (
    ContentUnitPage,
    ProcessingOutcome,
)
from foundry_lite.application.services.pipeline_media_reference import (
    attach_source_media_reference,
    source_media_references_by_version,
)
from foundry_lite.application.services.pipeline_media_set_output import (
    PipelineMediaSetOutputCommitter,
)
from foundry_lite.application.services.pipeline_v2_media_port_types import (
    EmbeddingModelAdapter,
    MediaDerivativeRecord,
    MediaProcessorDescriptor,
    MediaProcessorRegistry,
    MediaRepository,
    ProcessorSpec,
    TransactionManager,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeNode,
    PipelineV2SourceContract,
    PipelineV2SourceVersion,
    single_input_artifact,
)
from foundry_lite.application.services.pipeline_v2_runtime_media_payloads import (
    chunk_artifact,
    content_unit_item,
    derivative_artifact,
    derivative_ids,
    derivative_item,
    index_artifact,
    latest_committed_at,
    source_version_ids,
    verified_media_item,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation, ValidationFailed

JsonObject = dict[str, object]
RuntimeInputs = Mapping[str, Sequence[PipelineV2RuntimeArtifact]]
_CONTENT_PAGE_SIZE = 200


class PipelineV2MediaProcessing(Protocol):
    """Committed processor boundary used by Graph v2 nodes."""

    def process(
        self,
        ctx: RequestContext,
        *,
        media_item_version_id: str,
        spec: ProcessorSpec,
    ) -> ProcessingOutcome: ...

    def resolve_derivative(
        self,
        ctx: RequestContext,
        *,
        media_derivative_id: str,
    ) -> MediaDerivativeRecord: ...

    def list_derivative_content_units(
        self,
        ctx: RequestContext,
        *,
        media_derivative_id: str,
        after_ordinal: int | None = None,
        page_number: int | None = None,
        limit: int = 200,
    ) -> ContentUnitPage: ...


class PipelineV2MediaIndexing(Protocol):
    """Durable vector-index projection boundary."""

    def index_derivative(
        self,
        ctx: RequestContext,
        *,
        media_derivative_id: str,
        generation: str,
    ) -> IndexingOutcome: ...


class PipelineV2ContentChunking(Protocol):
    """Committed Content Unit chunking boundary."""

    def create_chunks(
        self,
        ctx: RequestContext,
        *,
        source: CommittedContentUnitSetRef,
        spec: ContentChunkSpec | None = None,
    ) -> ContentChunkOutcome: ...


class PipelineV2MediaRuntime:
    """Execute source, processor, extraction, and embedding nodes on committed truth."""

    def __init__(
        self,
        *,
        engine: TransactionManager,
        media_repository: MediaRepository,
        processor_registry: MediaProcessorRegistry | None,
        processing: PipelineV2MediaProcessing,
        indexing: PipelineV2MediaIndexing,
        chunking: PipelineV2ContentChunking,
        embedding_model: EmbeddingModelAdapter,
        output_committer: PipelineMediaSetOutputCommitter,
        ctx: RequestContext,
        run_id: str,
    ) -> None:
        self._engine = engine
        self._media_repository = media_repository
        self._processor_registry = processor_registry
        self._processing = processing
        self._indexing = indexing
        self._chunking = chunking
        self._embedding_model = embedding_model
        self._output_committer = output_committer
        self._ctx = ctx
        self._run_id = run_id

    def source_media(
        self,
        node: PipelineV2RuntimeNode,
        contract: PipelineV2SourceContract,
    ) -> PipelineV2RuntimeArtifact:
        _require_source_contract(node, contract)
        items = tuple(self._source_media_item(contract, pin) for pin in contract.version_pins)
        return PipelineV2RuntimeArtifact(
            node_id=node.node_id,
            descriptor_id=node.descriptor_id,
            spec_version=node.spec_version,
            port_id="media",
            artifact_kind="media_set_selection",
            plane="media",
            items=items,
            artifact_ref={
                "mediaSetRef": contract.resource_ref,
                "mediaSetId": contract.source_id,
                "mediaItemVersionIds": [pin.version_id for pin in contract.version_pins],
            },
            manifest={
                "selectionItemCount": len(items),
                "versionPins": [
                    {
                        "versionId": pin.version_id,
                        "contentFingerprint": pin.content_fingerprint,
                        "metadata": dict(pin.metadata),
                    }
                    for pin in contract.version_pins
                ],
            },
            security_envelope=dict(contract.security_envelope),
            status="COMMITTED",
            is_serving=True,
            committed_at=latest_committed_at(items),
        )

    def process_media(
        self,
        node: PipelineV2RuntimeNode,
        inputs: RuntimeInputs,
    ) -> PipelineV2RuntimeArtifact:
        source = single_input_artifact(node, inputs)
        spec, processor = self._processor_spec(node)
        results = [self._process_version(version_id, spec) for version_id in source_version_ids(source)]
        references = source_media_references_by_version(source.items)
        items = tuple(
            derivative_item(result, processor, references[result.source_media_item_version_id]) for result in results
        )
        return derivative_artifact(node, source, inputs, items, processor)

    def document_extract(
        self,
        node: PipelineV2RuntimeNode,
        inputs: RuntimeInputs,
    ) -> PipelineV2RuntimeArtifact:
        source = single_input_artifact(node, inputs)
        derivative_artifact = self.process_media(node, inputs)
        derivatives = derivative_ids(derivative_artifact)
        references = source_media_references_by_version(source.items)
        units = tuple(
            attach_source_media_reference(unit, references)
            for derivative_id in derivatives
            for unit in self._content_units(derivative_id)
        )
        manifest = {
            **dict(derivative_artifact.manifest),
            "contentUnitCount": len(units),
            "derivativeIds": derivatives,
        }
        return PipelineV2RuntimeArtifact(
            node_id=node.node_id,
            descriptor_id=node.descriptor_id,
            spec_version=node.spec_version,
            port_id="content",
            artifact_kind="content_unit_set",
            plane="content",
            items=units,
            artifact_ref={
                "mediaDerivativeIds": derivatives,
                "contentUnitIds": [str(unit["contentUnitId"]) for unit in units],
            },
            manifest=manifest,
            security_envelope=dict(derivative_artifact.security_envelope),
            status="COMMITTED",
            is_serving=True,
            committed_at=derivative_artifact.committed_at,
        )

    def embed_text(
        self,
        node: PipelineV2RuntimeNode,
        inputs: RuntimeInputs,
    ) -> PipelineV2RuntimeArtifact:
        source = single_input_artifact(node, inputs)
        model_ref = _required_text(node.config, "modelRef", node.node_id)
        self._require_embedding_model(model_ref)
        generation = _generation(node.config, self._run_id, node.node_id)
        outcomes = [
            self._indexing.index_derivative(
                self._ctx,
                media_derivative_id=derivative_id,
                generation=generation,
            )
            for derivative_id in derivative_ids(source)
        ]
        return index_artifact(node, source, inputs, generation, model_ref, outcomes)

    def chunk_content(
        self,
        node: PipelineV2RuntimeNode,
        inputs: RuntimeInputs,
    ) -> PipelineV2RuntimeArtifact:
        source = single_input_artifact(node, inputs)
        spec = _chunk_spec(node)
        outcomes = [
            self._chunking.create_chunks(
                self._ctx,
                source=CommittedContentUnitSetRef(media_derivative_id=derivative_id),
                spec=spec,
            )
            for derivative_id in derivative_ids(source)
        ]
        references = source_media_references_by_version(source.items)
        units = tuple(
            attach_source_media_reference(unit, references)
            for outcome in outcomes
            for unit in self._content_units(outcome.media_derivative_id)
        )
        return chunk_artifact(node, source, inputs, spec, outcomes, units)

    def output_media_set(
        self,
        node: PipelineV2RuntimeNode,
        inputs: RuntimeInputs,
    ) -> PipelineV2RuntimeArtifact:
        return self._output_committer.commit(node, inputs)

    def _source_media_item(
        self,
        contract: PipelineV2SourceContract,
        pin: PipelineV2SourceVersion,
    ) -> JsonObject:
        with self._engine.begin() as conn:
            version = self._media_repository.media_item_version_by_id(
                transaction=conn,
                tenant_id=self._ctx.tenant_id,
                media_item_version_id=pin.version_id,
            )
        return verified_media_item(contract, pin, version)

    def _processor_spec(
        self,
        node: PipelineV2RuntimeNode,
    ) -> tuple[ProcessorSpec, MediaProcessorDescriptor]:
        registry = self._processor_registry
        if registry is None:
            raise ValidationFailed("pipeline media processor registry is unavailable")
        processor_id = _required_text(node.config, "processorId", node.node_id)
        processor, version = _processor_identity(processor_id)
        descriptor = next(
            (item for item in registry.descriptors() if item.identity == (processor, version)),
            None,
        )
        if descriptor is None:
            raise ValidationFailed(
                "pinned pipeline media processor is not registered",
                details={"processorId": processor_id},
            )
        parameters = node.config.get("parameters")
        spec = ProcessorSpec(
            processor=processor,
            processor_version=version,
            model=descriptor.model.name,
            model_version=descriptor.model.version,
            parameters=dict(parameters) if isinstance(parameters, Mapping) else {},
        )
        return spec, descriptor

    def _process_version(
        self,
        version_id: str,
        spec: ProcessorSpec,
    ) -> MediaDerivativeRecord:
        outcome = self._processing.process(
            self._ctx,
            media_item_version_id=version_id,
            spec=spec,
        )
        if outcome.status != "committed":
            raise ConflictDetected(
                "pipeline media processor did not commit its derivative",
                details={"mediaItemVersionId": version_id, "status": outcome.status},
            )
        return self._processing.resolve_derivative(
            self._ctx,
            media_derivative_id=outcome.media_derivative_id,
        )

    def _content_units(self, derivative_id: str) -> tuple[JsonObject, ...]:
        rows: list[JsonObject] = []
        cursor: int | None = None
        while True:
            page = self._processing.list_derivative_content_units(
                self._ctx,
                media_derivative_id=derivative_id,
                after_ordinal=cursor,
                limit=_CONTENT_PAGE_SIZE,
            )
            rows.extend(content_unit_item(item) for item in page.items)
            if page.next_cursor is None:
                return tuple(rows)
            cursor = page.next_cursor

    def _require_embedding_model(self, model_ref: str) -> None:
        if not self._embedding_model.is_available:
            raise ValidationFailed("pipeline embedding model is unavailable")
        if model_ref != self._embedding_model.model_version:
            raise ValidationFailed(
                "pipeline embedding model does not match the deployed pin",
                details={
                    "modelRef": model_ref,
                    "runtimeModelVersion": self._embedding_model.model_version,
                },
            )


def _require_source_contract(
    node: PipelineV2RuntimeNode,
    contract: PipelineV2SourceContract,
) -> None:
    if contract.node_id == node.node_id and contract.descriptor_id == node.descriptor_id:
        return
    raise InvariantViolation(
        "pipeline source contract does not match the runtime node",
        details={"nodeId": node.node_id},
    )


def _processor_identity(processor_id: str) -> tuple[str, str]:
    processor, marker, version = processor_id.partition("@")
    if marker and processor and version:
        return processor, version
    raise ValidationFailed(
        "pipeline processorId must pin an exact version",
        details={"processorId": processor_id},
    )


def _generation(config: Mapping[str, object], run_id: str, node_id: str) -> str:
    value = config.get("generation")
    return value.strip() if isinstance(value, str) and value.strip() else f"pipeline-{run_id}-{node_id}"


def _required_text(config: Mapping[str, object], field: str, node_id: str) -> str:
    value = config.get(field)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValidationFailed(
        "pipeline runtime config field is required",
        details={"nodeId": node_id, "field": field},
    )


def _chunk_spec(node: PipelineV2RuntimeNode) -> ContentChunkSpec:
    chunk_size = node.config.get("chunkSize")
    overlap = node.config.get("overlap", 50)
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool):
        raise ValidationFailed(
            "pipeline chunkSize must be an integer",
            details={"nodeId": node.node_id},
        )
    if not isinstance(overlap, int) or isinstance(overlap, bool):
        raise ValidationFailed(
            "pipeline chunk overlap must be an integer",
            details={"nodeId": node.node_id},
        )
    return ContentChunkSpec(chunk_size=chunk_size, overlap=overlap)
