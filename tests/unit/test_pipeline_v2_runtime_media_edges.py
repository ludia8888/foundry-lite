from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from foundry_lite.application.ports.embedding_model import EmbeddingModelAdapter
from foundry_lite.application.services.media.indexing import IndexingOutcome
from foundry_lite.application.services.media.processing import ContentUnitPage, ProcessingOutcome
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeNode,
    PipelineV2SourceContract,
)
from foundry_lite.application.services.pipeline_v2_runtime_media import (
    PipelineV2MediaRuntime,
    _chunk_spec,
    _generation,
    _processor_identity,
    _require_source_contract,
    _required_text,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation, ValidationFailed


class _Indexing:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def index_derivative(
        self,
        ctx: RequestContext,
        *,
        media_derivative_id: str,
        generation: str,
    ) -> IndexingOutcome:
        self.calls.append((media_derivative_id, generation))
        return IndexingOutcome(generation=generation, indexed=2, failed=0)


class _Processing:
    def __init__(self) -> None:
        self.page = 0

    def process(self, ctx: RequestContext, **kwargs: object) -> ProcessingOutcome:
        return ProcessingOutcome("md-1", "failed", 0)

    def list_derivative_content_units(self, ctx: RequestContext, **kwargs: object) -> ContentUnitPage:
        self.page += 1
        return ContentUnitPage("md-1", "mv-1", (), 1 if self.page == 1 else None)


def _node(descriptor_id: str, **config: object) -> PipelineV2RuntimeNode:
    return PipelineV2RuntimeNode(
        node_id="node-1",
        kind="transform",
        descriptor_id=descriptor_id,
        spec_version=1,
        runtime_capability="media_runtime",
        config=config,
    )


def _artifact() -> PipelineV2RuntimeArtifact:
    return PipelineV2RuntimeArtifact(
        node_id="extract",
        descriptor_id="transform.document_extract",
        spec_version=1,
        port_id="content",
        artifact_kind="content_unit_set",
        plane="content",
        items=(),
        artifact_ref={"mediaDerivativeIds": ["md-1"]},
        manifest={},
        security_envelope={"classification": "INTERNAL"},
        status="COMMITTED",
        is_serving=True,
    )


def _runtime(
    *,
    embedding_model: object | None = None,
    processor_registry: object | None = None,
) -> tuple[PipelineV2MediaRuntime, _Indexing, _Processing]:
    indexing = _Indexing()
    processing = _Processing()
    runtime = PipelineV2MediaRuntime(
        engine=cast(object, None),  # type: ignore[arg-type]
        media_repository=cast(object, None),  # type: ignore[arg-type]
        processor_registry=cast(object, processor_registry),  # type: ignore[arg-type]
        processing=processing,
        indexing=indexing,
        chunking=cast(object, None),  # type: ignore[arg-type]
        embedding_model=cast(
            EmbeddingModelAdapter,
            embedding_model or SimpleNamespace(is_available=True, model_version="embed-v1"),
        ),
        output_committer=cast(object, None),  # type: ignore[arg-type]
        ctx=RequestContext(),
        run_id="run-1",
    )
    return runtime, indexing, processing


def test_media_runtime_embedding_uses_exact_model_and_generation_pin() -> None:
    runtime, indexing, _ = _runtime()
    source = _artifact()

    result = runtime.embed_text(
        _node("transform.embedding.text", modelRef="embed-v1", generation=" index-v1 "),
        {"input": (source,)},
    )

    assert indexing.calls == [("md-1", "index-v1")]
    assert result.artifact_ref == {"generation": "index-v1", "servingState": "SHADOW_NOT_PROMOTED"}
    assert result.is_serving is False


def test_media_runtime_rejects_processor_and_model_drift() -> None:
    runtime, _, _ = _runtime()
    with pytest.raises(ValidationFailed, match="registry is unavailable"):
        runtime._processor_spec(_node("transform.document_extract", processorId="pdf@1"))
    unavailable, _, _ = _runtime(embedding_model=SimpleNamespace(is_available=False, model_version="embed-v1"))
    with pytest.raises(ValidationFailed, match="unavailable"):
        unavailable._require_embedding_model("embed-v1")
    with pytest.raises(ValidationFailed, match="does not match"):
        runtime._require_embedding_model("embed-v2")

    empty_registry = SimpleNamespace(descriptors=lambda: ())
    with pytest.raises(ValidationFailed, match="not registered"):
        _runtime(processor_registry=empty_registry)[0]._processor_spec(
            _node("transform.document_extract", processorId="pdf@1")
        )


def test_media_runtime_requires_committed_processor_result_and_paginates_content_units() -> None:
    runtime, _, processing = _runtime()
    with pytest.raises(ConflictDetected, match="did not commit"):
        runtime._process_version(
            "mv-1",
            cast(object, SimpleNamespace()),  # type: ignore[arg-type]
        )

    assert runtime._content_units("md-1") == ()
    assert processing.page == 2


def test_media_runtime_helper_contracts_fail_closed_on_invalid_coordinates() -> None:
    node = _node("source.media_set")
    contract = PipelineV2SourceContract(
        node_id="other",
        descriptor_id="source.media_set",
        artifact_kind="media_set_selection",
        resource_ref="docs.reports",
        source_id="ms-1",
        schema_contract={},
        schema_hash="schema",
        schema_version=1,
        version_pins=(),
        security_envelope={},
        access_evidence={},
    )
    with pytest.raises(InvariantViolation, match="does not match"):
        _require_source_contract(node, contract)

    assert _processor_identity("pdf@1.0.0") == ("pdf", "1.0.0")
    assert _generation({}, "run-1", "node-1") == "pipeline-run-1-node-1"
    assert _generation({"generation": " fixed "}, "run-1", "node-1") == "fixed"
    assert _required_text({"field": " value "}, "field", "node-1") == "value"

    failures = (
        lambda: _processor_identity("pdf"),
        lambda: _processor_identity("@1"),
        lambda: _required_text({}, "field", "node-1"),
        lambda: _chunk_spec(_node("transform.chunk", chunkSize=True)),
        lambda: _chunk_spec(_node("transform.chunk", chunkSize=100, overlap=False)),
    )
    for failure in failures:
        with pytest.raises(ValidationFailed):
            failure()

    assert _chunk_spec(_node("transform.chunk", chunkSize=100, overlap=10)).chunk_size == 100
