"""Pipeline Builder v2 bounded multimodal previews never create serving artifacts."""

from __future__ import annotations

import io
import json
import wave
from collections.abc import Mapping, Sequence
from pathlib import Path

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.language_model import ModelRequest, ModelResponse
from foundry_lite.application.ports.media_processor_registry import (
    MediaProcessorDescriptor,
    MediaProcessorRegistration,
    ProcessorModelDescriptor,
    ProcessorPreviewCapability,
    ProcessorResourceRequirements,
)
from foundry_lite.application.primitives import _json_hash
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.asr_processor import (
    AsrProcessingBounds,
    AsrProcessorAdapter,
    TranscriptSegment,
)
from foundry_lite.infrastructure.adapters.fake_language_model import FakeLanguageModel
from foundry_lite.infrastructure.adapters.media_processor_registry import StaticMediaProcessorRegistry
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from sqlalchemy import func, select, update


class _StructuredPreviewLanguageModel(FakeLanguageModel):
    def __init__(self, content: Mapping[str, object]) -> None:
        self.content = json.dumps(content)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            provider="structured-preview",
            resolved_model_id="",
            resolved_model_revision="",
            content=self.content,
            finish_reason="stop",
            input_tokens=41,
            output_tokens=17,
        )


def test_preview_execution_claim_has_one_winner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    dependencies = create_local_core_dependencies(
        db_url=f"sqlite:///{tmp_path / 'pipeline-preview-claim.db'}",
        storage_root=tmp_path / "preview-claim-flite",
    )
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    branch = foundry.pipelines.create_branch(
        pipeline_id="preview-claim-pipeline",
        name="draft",
        idempotency_key="preview-claim-branch",
        ctx=ctx,
    )
    queued = foundry.pipelines.create_preview_run(
        str(branch["id"]),
        graph=_media_rows_graph("legal.claim_only", "version-claim-only"),
        target_node_id="out",
        limits={"tableRows": 10},
        idempotency_key="preview-claim-run",
        ctx=ctx,
    )
    with dependencies.engine.begin() as transaction:
        claimed = foundry._services.pipelines.preview.pipeline_execution_repository.claim_preview(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            preview_run_id=str(queued["id"]),
            started_at="2026-07-27T00:00:00Z",
        )
    assert claimed is not None

    def fail_if_duplicate_executes(*_args, **_kwargs):
        raise AssertionError("a RUNNING preview must not be executed by a second caller")

    monkeypatch.setattr(
        "foundry_lite.application.services.pipeline_preview_service.execute_pipeline_preview",
        fail_if_duplicate_executes,
    )

    replay = foundry.pipelines.execute_preview_run(str(queued["id"]), ctx=ctx)

    assert replay["status"] == "RUNNING"
    assert replay["startedAt"] == "2026-07-27T00:00:00Z"


def test_pdf_preview_extracts_and_chunks_without_committing_derivatives_or_datasets(tmp_path: Path) -> None:
    dependencies = create_local_core_dependencies(
        db_url=f"sqlite:///{tmp_path / 'pipeline-media-preview.db'}",
        storage_root=tmp_path / "flite",
    )
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    media_set = foundry.media.create_media_set(
        ctx,
        namespace="legal",
        name="preview_contracts",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        classification="confidential",
    )
    transaction_id = foundry.media.open_transaction(
        ctx,
        media_set_id=media_set.media_set_id,
        idempotency_key="preview-pdf-upload",
    )
    foundry.media.upload(
        ctx,
        media_set_id=media_set.media_set_id,
        media_transaction_id=transaction_id,
        logical_path="/contracts/acme.pdf",
        source=io.BytesIO(_make_pdf(["Acme contract payment due thirty days"])),
        supplied_mime_type="application/pdf",
        schema_type="document",
        format="pdf",
        security_envelope={"tenantId": ctx.tenant_id, "classification": "confidential"},
    )
    foundry.media.commit(ctx, media_transaction_id=transaction_id)
    branch = foundry.pipelines.create_branch(
        pipeline_id="pdf-preview-pipeline",
        name="draft",
        idempotency_key="pdf-preview-branch",
        ctx=ctx,
    )
    before = _serving_counts(dependencies.engine)
    graph = _pdf_preview_graph()
    queued = foundry.pipelines.create_preview_run(
        str(branch["id"]),
        graph=graph,
        target_node_id="out",
        limits={"pdfPages": 3, "tableRows": 50},
        idempotency_key="pdf-preview-run",
        ctx=ctx,
    )
    completed = foundry.pipelines.execute_preview_run(str(queued["id"]), ctx=ctx)
    after = _serving_counts(dependencies.engine)

    assert completed["status"] == "SUCCEEDED"
    assert completed["commitForbidden"] is True
    assert completed["servingVersionCreated"] is False
    rows = completed["outputs"][0]["items"]
    assert any("Acme contract" in str(row["text"]) for row in rows)
    assert rows[0]["sourceLocator"] == {"pageNumber": 1}
    assert completed["outputs"][0]["artifactKind"] == "dataset_version"
    assert any(
        envelope["classification"] == "confidential"
        for artifact in completed["artifacts"]
        for envelope in artifact["passport"]["securityEnvelopes"]
    )
    assert before == after == {"derivatives": 0, "contentUnits": 0, "datasetVersions": 0}
    assert _preview_commit_forbidden(dependencies.engine, str(queued["id"])) is True


def test_media_preview_rejects_bytes_changed_after_catalog_verification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    dependencies = create_local_core_dependencies(
        db_url=f"sqlite:///{tmp_path / 'pipeline-media-tamper.db'}",
        storage_root=tmp_path / "tamper-flite",
    )
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    version_id = _commit_pdf(foundry, classification="public")
    branch = foundry.pipelines.create_branch(
        pipeline_id="media-tamper-preview",
        name="draft",
        idempotency_key="media-tamper-branch",
        ctx=ctx,
    )
    queued = foundry.pipelines.create_preview_run(
        str(branch["id"]),
        graph=_pdf_page_graph("legal.public_prompt_contracts"),
        target_node_id="out",
        limits={"pdfPages": 3, "tableRows": 50},
        idempotency_key="media-tamper-run",
        ctx=ctx,
    )
    media_storage = foundry._services.pipelines.preview.media_storage
    monkeypatch.setattr(media_storage, "open_stream", lambda _blob_key: io.BytesIO(b"tampered-media"))

    completed = foundry.pipelines.execute_preview_run(str(queued["id"]), ctx=ctx)

    assert version_id
    assert completed["status"] == "FAILED"
    assert completed["error"]["type"] == "CONFLICT"
    assert completed["outputs"] == []
    assert _serving_counts(dependencies.engine) == {
        "derivatives": 0,
        "contentUnits": 0,
        "datasetVersions": 0,
    }


def test_long_pdf_preview_selects_first_pages_without_treating_preview_limit_as_hard_guard(tmp_path: Path) -> None:
    dependencies = create_local_core_dependencies(
        db_url=f"sqlite:///{tmp_path / 'pipeline-long-pdf-preview.db'}",
        storage_root=tmp_path / "long-pdf-flite",
    )
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    media_set = foundry.media.create_media_set(
        ctx,
        namespace="legal",
        name="long_preview_contracts",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        classification="public",
    )
    transaction_id = foundry.media.open_transaction(
        ctx,
        media_set_id=media_set.media_set_id,
        idempotency_key="long-preview-upload",
    )
    foundry.media.upload(
        ctx,
        media_set_id=media_set.media_set_id,
        media_transaction_id=transaction_id,
        logical_path="/contracts/long.pdf",
        source=io.BytesIO(_make_pdf([f"Contract page {index}" for index in range(1, 14)])),
        supplied_mime_type="application/pdf",
        schema_type="document",
        format="pdf",
        security_envelope={"tenantId": ctx.tenant_id, "classification": "public"},
    )
    foundry.media.commit(ctx, media_transaction_id=transaction_id)
    branch = foundry.pipelines.create_branch(
        pipeline_id="long-pdf-preview",
        name="draft",
        idempotency_key="long-pdf-branch",
        ctx=ctx,
    )
    queued = foundry.pipelines.create_preview_run(
        str(branch["id"]),
        graph=_pdf_page_graph("legal.long_preview_contracts"),
        target_node_id="out",
        limits={"pdfPages": 3, "tableRows": 50},
        idempotency_key="long-pdf-preview-run",
        ctx=ctx,
    )

    completed = foundry.pipelines.execute_preview_run(str(queued["id"]), ctx=ctx)

    assert completed["status"] == "SUCCEEDED"
    rows = completed["outputs"][0]["items"]
    assert [row["pageNumber"] for row in rows] == [1, 2, 3]
    assert [row["text"].strip() for row in rows] == ["Contract page 1", "Contract page 2", "Contract page 3"]


def test_pdf_ocr_rows_can_be_semantically_interpreted_with_a_pinned_prompt(tmp_path: Path) -> None:
    adapter = _StructuredPreviewLanguageModel(
        {
            "sections": [
                {
                    "level": "H1",
                    "title": "Payment terms",
                    "meaning": "The invoice is due within thirty days.",
                }
            ]
        }
    )
    dependencies = _dependencies_with_language_model(tmp_path, adapter)
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    media_item_version_id = _commit_pdf(foundry, classification="public")
    branch = foundry.pipelines.create_branch(
        pipeline_id="pdf-semantic-preview",
        name="draft",
        idempotency_key="pdf-semantic-branch",
        ctx=ctx,
    )
    before = _serving_counts(dependencies.engine)
    queued = foundry.pipelines.create_preview_run(
        str(branch["id"]),
        graph=_pdf_semantic_graph(media_item_version_id),
        target_node_id="out",
        limits={"pdfPages": 3, "tableRows": 50},
        idempotency_key="pdf-semantic-preview-run",
        ctx=ctx,
    )

    completed = foundry.pipelines.execute_preview_run(str(queued["id"]), ctx=ctx)
    replay_queued = foundry.pipelines.create_preview_run(
        str(branch["id"]),
        graph=_pdf_semantic_graph(media_item_version_id),
        target_node_id="out",
        limits={"pdfPages": 3, "tableRows": 50},
        idempotency_key="pdf-semantic-preview-run-cached",
        ctx=ctx,
    )
    replay_completed = foundry.pipelines.execute_preview_run(str(replay_queued["id"]), ctx=ctx)

    rows = completed["outputs"][0]["items"]
    replay_rows = replay_completed["outputs"][0]["items"]
    assert rows[0]["interpretation"]["sections"][0]["level"] == "H1"
    assert rows[0]["_pipelineModelEvidence"]["promptVersionId"] == "contract-semantics@1"
    assert rows[0]["_pipelineModelEvidence"]["resolvedModelId"] == "local-fake-model"
    assert rows[0]["_pipelineModelEvidence"]["cacheStatus"] == "miss"
    assert replay_rows[0]["_pipelineModelEvidence"]["cacheStatus"] == "hit"
    assert replay_rows[0]["_pipelineModelEvidence"]["cacheHit"] is True
    assert replay_rows[0]["_pipelineModelEvidence"]["cacheScopeKind"] == "branch"
    assert replay_rows[0]["_pipelineModelEvidence"]["cacheScopeId"] == branch["id"]
    assert replay_rows[0]["_pipelineModelEvidence"]["cacheNodeId"] == "semantic"
    assert replay_rows[0]["_pipelineModelEvidence"]["cacheGeneration"] == 1
    assert len(adapter.requests) == 1
    assert adapter.requests[0].messages[-1].media_references == ()
    assert "payment due thirty days" in adapter.requests[0].messages[-1].content
    assert _serving_counts(dependencies.engine) == before


def test_preview_rejects_legacy_media_version_weaker_than_media_set_before_model_invocation(
    tmp_path: Path,
) -> None:
    adapter = _StructuredPreviewLanguageModel({"documentType": "contract", "hasSignature": False})
    dependencies = _dependencies_with_language_model(tmp_path, adapter)
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    media_set = foundry.media.create_media_set(
        ctx,
        namespace="legal",
        name="weak_preview_contracts",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        classification="confidential",
    )
    version_id = _commit_pdf_to_set(foundry, media_set.media_set_id, "confidential")
    with dependencies.engine.begin() as conn:
        conn.execute(
            update(db.media_item_versions)
            .where(db.media_item_versions.c.id == version_id)
            .values(security_envelope={"tenantId": ctx.tenant_id, "classification": "public"})
        )
    branch = foundry.pipelines.create_branch(
        pipeline_id="weak-media-preview",
        name="draft",
        idempotency_key="weak-media-preview-branch",
        ctx=ctx,
    )
    before = _serving_counts(dependencies.engine)
    queued = foundry.pipelines.create_preview_run(
        str(branch["id"]),
        graph=_pdf_vision_graph(
            version_id,
            media_set_ref="legal.weak_preview_contracts",
        ),
        target_node_id="out",
        limits={"mediaItems": 1, "tableRows": 1},
        idempotency_key="weak-media-preview-run",
        ctx=ctx,
    )

    completed = foundry.pipelines.execute_preview_run(str(queued["id"]), ctx=ctx)

    assert completed["status"] == "FAILED"
    assert completed["error"]["type"] == "MEDIA_SECURITY_ENVELOPE_INVALID"
    assert completed["error"]["details"]["reason"] == "source_security_weakened"
    assert completed["error"]["details"]["weakenedFields"] == ["classification"]
    assert adapter.requests == []
    assert completed["outputs"] == []
    assert _serving_counts(dependencies.engine) == before


def test_preview_public_security_projection_hides_private_envelope_extensions(
    tmp_path: Path,
) -> None:
    dependencies = create_local_core_dependencies(
        db_url=f"sqlite:///{tmp_path / 'pipeline-security-projection.db'}",
        storage_root=tmp_path / "security-projection-flite",
    )
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    media_set = foundry.media.create_media_set(
        ctx,
        namespace="legal",
        name="security_projection",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        classification="confidential",
    )
    full_envelope = {
        "tenantId": ctx.tenant_id,
        "classification": "confidential",
        "policyVersion": "policy-v3",
        "allowedPrincipalSetId": "legal-readers",
        "hasLegalHold": True,
        "privateExtension": {"operatorOnly": "not-public"},
    }
    version_id = _commit_pdf_to_set(
        foundry,
        media_set.media_set_id,
        "confidential",
        security_envelope=full_envelope,
    )
    branch = foundry.pipelines.create_branch(
        pipeline_id="security-projection-preview",
        name="draft",
        idempotency_key="security-projection-branch",
        ctx=ctx,
    )
    queued = foundry.pipelines.create_preview_run(
        str(branch["id"]),
        graph=_media_rows_graph("legal.security_projection", version_id),
        target_node_id="out",
        limits={"mediaItems": 1, "tableRows": 1},
        idempotency_key="security-projection-run",
        ctx=ctx,
    )

    completed = foundry.pipelines.execute_preview_run(str(queued["id"]), ctx=ctx)

    public_envelope = completed["outputs"][0]["securityEnvelopes"][0]
    assert completed["status"] == "SUCCEEDED"
    assert public_envelope == {
        "securityEnvelopeFingerprint": _json_hash(full_envelope),
        "tenantId": ctx.tenant_id,
        "classification": "confidential",
        "policyVersion": "policy-v3",
        "allowedPrincipalSetId": "legal-readers",
        "hasLegalHold": True,
    }
    assert all(
        envelope == public_envelope
        for artifact in completed["artifacts"]
        for envelope in artifact["passport"]["securityEnvelopes"]
    )
    assert not any(_contains_key(artifact["items"], "securityEnvelope") for artifact in completed["artifacts"])
    serialized = json.dumps(completed)
    assert "privateExtension" not in serialized
    assert "operatorOnly" not in serialized
    assert "not-public" not in serialized


def test_pdf_vision_prompt_requires_media_reference_table_bridge(tmp_path: Path) -> None:
    adapter = _StructuredPreviewLanguageModel({"documentType": "contract", "hasSignature": False})
    dependencies = _dependencies_with_language_model(tmp_path, adapter)
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    media_item_version_id = _commit_pdf(foundry, classification="public")
    branch = foundry.pipelines.create_branch(
        pipeline_id="pdf-vision-preview",
        name="draft",
        idempotency_key="pdf-vision-branch",
        ctx=ctx,
    )
    queued = foundry.pipelines.create_preview_run(
        str(branch["id"]),
        graph=_pdf_vision_graph(media_item_version_id),
        target_node_id="out",
        limits={"mediaItems": 5, "tableRows": 50},
        idempotency_key="pdf-vision-preview-run",
        ctx=ctx,
    )

    completed = foundry.pipelines.execute_preview_run(str(queued["id"]), ctx=ctx)

    assert completed["outputs"][0]["items"][0]["interpretation"] == {
        "documentType": "contract",
        "hasSignature": False,
    }
    references = adapter.requests[0].messages[-1].media_references
    assert references[0].media_item_version_id == media_item_version_id
    assert references[0].mime_type == "application/pdf"


def test_layout_aware_pdf_vision_combines_preprocessed_text_and_media_reference(
    tmp_path: Path,
) -> None:
    adapter = _StructuredPreviewLanguageModel({"documentType": "contract", "hasSignature": False})
    dependencies = _dependencies_with_language_model(tmp_path, adapter)
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    media_item_version_id = _commit_pdf(foundry, classification="public")
    branch = foundry.pipelines.create_branch(
        pipeline_id="pdf-preprocessed-vision-preview",
        name="draft",
        idempotency_key="pdf-preprocessed-vision-branch",
        ctx=ctx,
    )
    queued = foundry.pipelines.create_preview_run(
        str(branch["id"]),
        graph=_pdf_preprocessed_vision_graph(media_item_version_id),
        target_node_id="out",
        limits={"pdfPages": 3, "mediaItems": 5, "tableRows": 50},
        idempotency_key="pdf-preprocessed-vision-preview-run",
        ctx=ctx,
    )

    completed = foundry.pipelines.execute_preview_run(str(queued["id"]), ctx=ctx)

    row = completed["outputs"][0]["items"][0]
    assert row["interpretation"]["documentType"] == "contract"
    request = adapter.requests[0]
    assert request.messages[-1].media_references[0].media_item_version_id == media_item_version_id
    assert "payment due thirty days" in request.messages[-1].content
    assert "sourceLocator" in request.messages[-1].content


def test_pipeline_semantic_bridge_preserves_aip_region_egress_denial(tmp_path: Path) -> None:
    adapter = _StructuredPreviewLanguageModel({"sections": []})
    dependencies = _dependencies_with_language_model(tmp_path, adapter)
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    graph = _pdf_semantic_graph(_commit_pdf(foundry, classification="public"))
    semantic = next(node for node in graph["nodes"] if node["id"] == "semantic")
    semantic["config"]["regionRequirement"] = "eu-west-1"
    branch = foundry.pipelines.create_branch(
        pipeline_id="pdf-semantic-egress-preview",
        name="draft",
        idempotency_key="pdf-semantic-egress-branch",
        ctx=ctx,
    )
    queued = foundry.pipelines.create_preview_run(
        str(branch["id"]),
        graph=graph,
        target_node_id="out",
        limits={"pdfPages": 3, "tableRows": 50},
        idempotency_key="pdf-semantic-egress-run",
        ctx=ctx,
    )

    completed = foundry.pipelines.execute_preview_run(str(queued["id"]), ctx=ctx)

    assert completed["status"] == "FAILED"
    assert "region" in json.dumps(completed["error"])
    assert adapter.requests == []


def test_audio_preview_enforces_processor_bounds_and_never_commits_serving_artifacts(tmp_path: Path) -> None:
    observed_bounds: list[AsrProcessingBounds] = []

    def _bounded_asr(_source_path: str, bounds: AsrProcessingBounds) -> Sequence[TranscriptSegment]:
        observed_bounds.append(bounds)
        return (
            TranscriptSegment(0, 1500, "first", speaker="spk_1", language="en"),
            TranscriptSegment(1500, 4000, "second", speaker="spk_2", language="en"),
            TranscriptSegment(3000, 5000, "outside", speaker="spk_3", language="en"),
        )

    dependencies = _dependencies_with_asr(tmp_path, AsrProcessorAdapter(asr_engine=_bounded_asr))
    foundry = FoundryLite(dependencies=dependencies)
    ctx = demo_admin_context()
    version_id = _commit_wav(foundry)
    branch = foundry.pipelines.create_branch(
        pipeline_id="bounded-audio-preview",
        name="draft",
        idempotency_key="bounded-audio-branch",
        ctx=ctx,
    )
    before = _serving_counts(dependencies.engine)
    queued = foundry.pipelines.create_preview_run(
        str(branch["id"]),
        graph=_audio_preview_graph(version_id),
        target_node_id="asr",
        limits={"audioVideoSeconds": 2, "tableRows": 50},
        idempotency_key="bounded-audio-preview-run",
        ctx=ctx,
    )

    completed = foundry.pipelines.execute_preview_run(str(queued["id"]), ctx=ctx)

    item = completed["outputs"][0]["items"][0]
    assert completed["status"] == "SUCCEEDED"
    assert item["processingEvidence"] == {
        "requested": {"maxDurationMs": 90_000},
        "applied": {"maxDurationMs": 2_000},
        "observed": {"unitCount": 2, "maxStartMs": 1_500, "maxEndMs": 2_000},
    }
    assert [(unit["startMs"], unit["endMs"]) for unit in item["units"]] == [(0, 1500), (1500, 2000)]
    assert all(unit["processingEvidence"] == item["processingEvidence"] for unit in item["units"])
    assert observed_bounds == [AsrProcessingBounds(max_duration_ms=2000, requested_max_duration_ms=90_000)]
    assert _serving_counts(dependencies.engine) == before


def _dependencies_with_language_model(
    tmp_path: Path,
    adapter: _StructuredPreviewLanguageModel,
) -> CoreDependencies:
    base = create_local_core_dependencies(
        db_url=f"sqlite:///{tmp_path / 'pipeline-semantic-preview.db'}",
        storage_root=tmp_path / "semantic-flite",
    )
    return CoreDependencies(
        paths=base.paths,
        security=base.security,
        action=base.action,
        data=base.data,
        object_store=base.object_store,
        runtime=base.runtime,
        aip=base.aip,
        media=base.media,
        source=base.source,
        profile=base.profile,
        language_model_adapter=adapter,
    )


def _dependencies_with_asr(tmp_path: Path, adapter: AsrProcessorAdapter) -> CoreDependencies:
    base = create_local_core_dependencies(
        db_url=f"sqlite:///{tmp_path / 'pipeline-audio-preview.db'}",
        storage_root=tmp_path / "audio-flite",
    )
    registry = StaticMediaProcessorRegistry(
        (
            MediaProcessorRegistration(
                descriptor=MediaProcessorDescriptor(
                    processor="asr_v1",
                    processor_version="1",
                    adapter_profile=adapter.profile_name,
                    input_formats=("wav",),
                    output_kinds=("asr_v1",),
                    model=ProcessorModelDescriptor("whisper", "test"),
                    resources=ProcessorResourceRequirements(1, 128),
                    preview=ProcessorPreviewCapability("bounded", max_media_items=5, max_duration_seconds=60),
                ),
                adapter=adapter,
            ),
        )
    )
    return CoreDependencies(
        paths=base.paths,
        security=base.security,
        action=base.action,
        data=base.data,
        object_store=base.object_store,
        runtime=base.runtime,
        aip=base.aip,
        media=base.media,
        source=base.source,
        profile=base.profile,
        media_processor_registry=registry,
    )


def _commit_pdf(foundry: FoundryLite, *, classification: str) -> str:
    ctx = demo_admin_context()
    media_set = foundry.media.create_media_set(
        ctx,
        namespace="legal",
        name=f"{classification}_prompt_contracts",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        classification=classification,
    )
    transaction_id = foundry.media.open_transaction(
        ctx,
        media_set_id=media_set.media_set_id,
        idempotency_key=f"{classification}-prompt-upload",
    )
    staged = foundry.media.upload(
        ctx,
        media_set_id=media_set.media_set_id,
        media_transaction_id=transaction_id,
        logical_path="/contracts/acme.pdf",
        source=io.BytesIO(_make_pdf(["Acme contract payment due thirty days"])),
        supplied_mime_type="application/pdf",
        schema_type="document",
        format="pdf",
        security_envelope={"tenantId": ctx.tenant_id, "classification": classification},
    )
    foundry.media.commit(ctx, media_transaction_id=transaction_id)
    return staged.media_item_version_id


def _commit_pdf_to_set(
    foundry: FoundryLite,
    media_set_id: str,
    classification: str,
    *,
    security_envelope: dict[str, object] | None = None,
) -> str:
    ctx = demo_admin_context()
    transaction_id = foundry.media.open_transaction(
        ctx,
        media_set_id=media_set_id,
        idempotency_key=f"{media_set_id}-preview-upload",
    )
    staged = foundry.media.upload(
        ctx,
        media_set_id=media_set_id,
        media_transaction_id=transaction_id,
        logical_path="/contracts/security.pdf",
        source=io.BytesIO(_make_pdf(["Security contract"])),
        supplied_mime_type="application/pdf",
        schema_type="document",
        format="pdf",
        security_envelope=security_envelope or {"tenantId": ctx.tenant_id, "classification": classification},
    )
    foundry.media.commit(ctx, media_transaction_id=transaction_id)
    return staged.media_item_version_id


def _commit_wav(foundry: FoundryLite) -> str:
    ctx = demo_admin_context()
    media_set = foundry.media.create_media_set(
        ctx,
        namespace="audio",
        name="bounded_preview",
        schema_type="audio",
        primary_format="wav",
        allowed_input_formats=("wav",),
        classification="confidential",
    )
    transaction_id = foundry.media.open_transaction(
        ctx,
        media_set_id=media_set.media_set_id,
        idempotency_key="bounded-audio-upload",
    )
    staged = foundry.media.upload(
        ctx,
        media_set_id=media_set.media_set_id,
        media_transaction_id=transaction_id,
        logical_path="/calls/bounded.wav",
        source=io.BytesIO(_silent_wav()),
        supplied_mime_type="audio/wav",
        schema_type="audio",
        format="wav",
        security_envelope={"tenantId": ctx.tenant_id, "classification": "confidential"},
    )
    foundry.media.commit(ctx, media_transaction_id=transaction_id)
    return staged.media_item_version_id


def _silent_wav() -> bytes:
    target = io.BytesIO()
    with wave.open(target, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 8000)
    return target.getvalue()


def _serving_counts(engine: object) -> dict[str, int]:
    with engine.begin() as conn:  # type: ignore[attr-defined]
        return {
            "derivatives": int(conn.execute(select(func.count()).select_from(db.media_derivatives)).scalar_one()),
            "contentUnits": int(conn.execute(select(func.count()).select_from(db.content_units)).scalar_one()),
            "datasetVersions": int(conn.execute(select(func.count()).select_from(db.dataset_versions)).scalar_one()),
        }


def _preview_commit_forbidden(engine: object, preview_run_id: str) -> bool:
    with engine.begin() as conn:  # type: ignore[attr-defined]
        value = conn.execute(
            select(db.pipeline_preview_runs.c.is_commit_forbidden).where(
                db.pipeline_preview_runs.c.id == preview_run_id
            )
        ).scalar_one()
    return bool(value)


def _pdf_preview_graph() -> dict[str, object]:
    nodes = [
        _node(
            "media",
            "source",
            "source.media_set",
            {"mediaSetRef": "legal.preview_contracts"},
        ),
        _node("extract", "transform", "transform.document_extract", {"processorId": "pdf_text_v1@1"}),
        _node("chunk", "transform", "transform.chunk", {"chunkSize": 4, "overlap": 1}),
        _node("rows", "transform", "bridge.content_units_to_dataset", {}),
        _node("out", "output", "output.dataset", {"outputDatasetRef": "preview.contract_chunks"}),
    ]
    edges = [
        _edge("media-extract", "media", "media", "extract", "media"),
        _edge("extract-chunk", "extract", "content", "chunk", "content"),
        _edge("chunk-rows", "chunk", "content", "rows", "content"),
        _edge("rows-out", "rows", "dataset", "out", "input"),
    ]
    return {
        "schemaVersion": 2,
        "nodes": nodes,
        "edges": edges,
        "layout": {},
        "outputContract": {"columns": []},
        "tests": [],
        "schedule": None,
    }


def _audio_preview_graph(media_item_version_id: str) -> dict[str, object]:
    nodes = [
        _node(
            "media",
            "source",
            "source.media_set",
            {
                "mediaSetRef": "audio.bounded_preview",
                "mediaItemVersionIds": [media_item_version_id],
            },
        ),
        _node(
            "asr",
            "transform",
            "transform.media",
            {
                "processorId": "asr_v1@1",
                "processingBounds": {"maxDurationMs": 90_000},
            },
        ),
        _node("out", "output", "output.media_set", {"mediaSetRef": "audio.preview_output"}),
    ]
    edges = [
        _edge("media-asr", "media", "media", "asr", "media"),
        _edge("asr-out", "asr", "derivatives", "out", "media"),
    ]
    return _graph(nodes, edges)


def _pdf_page_graph(media_set_ref: str) -> dict[str, object]:
    nodes = [
        _node("media", "source", "source.media_set", {"mediaSetRef": media_set_ref}),
        _node("extract", "transform", "transform.document_extract", {"processorId": "pdf_text_v1@1"}),
        _node("rows", "transform", "bridge.content_units_to_dataset", {}),
        _node("out", "output", "output.dataset", {"outputDatasetRef": "preview.pdf_pages"}),
    ]
    edges = [
        _edge("media-extract", "media", "media", "extract", "media"),
        _edge("extract-rows", "extract", "content", "rows", "content"),
        _edge("rows-out", "rows", "dataset", "out", "input"),
    ]
    return _graph(nodes, edges)


def _pdf_semantic_graph(media_item_version_id: str) -> dict[str, object]:
    nodes = [
        _node(
            "media",
            "source",
            "source.media_set",
            {"mediaSetRef": "legal.public_prompt_contracts", "mediaItemVersionIds": [media_item_version_id]},
        ),
        _node("extract", "transform", "transform.document_extract", {"processorId": "pdf_text_v1@1"}),
        _node("chunk", "transform", "transform.chunk", {"chunkSize": 20, "overlap": 2}),
        _node("rows", "transform", "bridge.content_units_to_dataset", {}),
        _node("semantic", "transform", "transform.use_llm", _semantic_config()),
        _node("out", "output", "output.dataset", {"outputDatasetRef": "preview.contract_semantics"}),
    ]
    edges = [
        _edge("media-extract", "media", "media", "extract", "media"),
        _edge("extract-chunk", "extract", "content", "chunk", "content"),
        _edge("chunk-rows", "chunk", "content", "rows", "content"),
        _edge("rows-semantic", "rows", "dataset", "semantic", "input"),
        _edge("semantic-out", "semantic", "dataset", "out", "input"),
    ]
    return _graph(nodes, edges)


def _pdf_vision_graph(
    media_item_version_id: str,
    *,
    media_set_ref: str = "legal.public_prompt_contracts",
) -> dict[str, object]:
    config = {
        **_semantic_config(),
        "promptVersionId": "contract-vision@1",
        "promptTemplate": "Inspect this PDF and classify the document.",
        "inputFields": ["mediaReference"],
        "mediaReferenceField": "mediaReference",
        "outputSchema": {
            "type": "object",
            "required": ["documentType", "hasSignature"],
            "properties": {
                "documentType": {"type": "string"},
                "hasSignature": {"type": "boolean"},
            },
        },
    }
    nodes = [
        _node(
            "media",
            "source",
            "source.media_set",
            {"mediaSetRef": media_set_ref, "mediaItemVersionIds": [media_item_version_id]},
        ),
        _node("rows", "transform", "bridge.media_to_table_rows", {}),
        _node("semantic", "transform", "transform.use_llm", config),
        _node("out", "output", "output.dataset", {"outputDatasetRef": "preview.contract_vision"}),
    ]
    edges = [
        _edge("media-rows", "media", "media", "rows", "media"),
        _edge("rows-semantic", "rows", "dataset", "semantic", "input"),
        _edge("semantic-out", "semantic", "dataset", "out", "input"),
    ]
    return _graph(nodes, edges)


def _media_rows_graph(media_set_ref: str, media_item_version_id: str) -> dict[str, object]:
    nodes = [
        _node(
            "media",
            "source",
            "source.media_set",
            {"mediaSetRef": media_set_ref, "mediaItemVersionIds": [media_item_version_id]},
        ),
        _node("rows", "transform", "bridge.media_to_table_rows", {}),
        _node("out", "output", "output.dataset", {"outputDatasetRef": "preview.security_projection"}),
    ]
    edges = [
        _edge("media-rows", "media", "media", "rows", "media"),
        _edge("rows-out", "rows", "dataset", "out", "input"),
    ]
    return _graph(nodes, edges)


def _pdf_preprocessed_vision_graph(media_item_version_id: str) -> dict[str, object]:
    config = {
        **_semantic_config(),
        "promptVersionId": "contract-layout-vision@1",
        "promptMode": "layout_aware_vision",
        "promptTemplate": "Interpret the attached PDF using the supplied layout evidence.",
        "inputFields": ["mediaReference", "text", "structure", "sourceLocator"],
        "mediaReferenceField": "mediaReference",
        "outputSchema": {
            "type": "object",
            "required": ["documentType", "hasSignature"],
            "properties": {
                "documentType": {"type": "string"},
                "hasSignature": {"type": "boolean"},
            },
        },
    }
    nodes = [
        _node(
            "media",
            "source",
            "source.media_set",
            {"mediaSetRef": "legal.public_prompt_contracts", "mediaItemVersionIds": [media_item_version_id]},
        ),
        _node("extract", "transform", "transform.document_extract", {"processorId": "pdf_layout_v1@1"}),
        _node("rows", "transform", "bridge.content_units_to_dataset", {}),
        _node("semantic", "transform", "transform.use_llm", config),
        _node("out", "output", "output.dataset", {"outputDatasetRef": "preview.preprocessed_vision"}),
    ]
    edges = [
        _edge("media-extract", "media", "media", "extract", "media"),
        _edge("extract-rows", "extract", "content", "rows", "content"),
        _edge("rows-semantic", "rows", "dataset", "semantic", "input"),
        _edge("semantic-out", "semantic", "dataset", "out", "input"),
    ]
    return _graph(nodes, edges)


def _semantic_config() -> dict[str, object]:
    return {
        "modelAlias": "default-completion",
        "promptVersionId": "contract-semantics@1",
        "promptTemplate": "Interpret this document section: {{text}}",
        "systemPrompt": "Extract the semantic meaning of the document structure.",
        "inputFields": ["text", "sourceLocator"],
        "outputColumn": "interpretation",
        "outputSchema": {
            "type": "object",
            "required": ["sections"],
            "properties": {
                "sections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["level", "title", "meaning"],
                        "properties": {
                            "level": {"type": "string"},
                            "title": {"type": "string"},
                            "meaning": {"type": "string"},
                        },
                    },
                }
            },
        },
        "dataClassification": "public",
        "outputMode": "simple",
        "skipRecomputingRows": True,
        "modelParameters": {"temperature": 0, "maxOutputTokens": 500},
    }


def _graph(nodes: list[dict[str, object]], edges: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "nodes": nodes,
        "edges": edges,
        "layout": {},
        "outputContract": {"columns": []},
        "tests": [],
        "schedule": None,
    }


def _contains_key(value: object, target: str) -> bool:
    if isinstance(value, Mapping):
        return target in value or any(_contains_key(item, target) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_key(item, target) for item in value)
    return False


def _node(node_id: str, kind: str, descriptor_id: str, config: Mapping[str, object]) -> dict[str, object]:
    return {
        "id": node_id,
        "kind": kind,
        "descriptorId": descriptor_id,
        "specVersion": 1,
        "config": dict(config),
    }


def _edge(edge_id: str, source: str, source_port: str, target: str, target_port: str) -> dict[str, object]:
    return {
        "id": edge_id,
        "sourceNodeId": source,
        "sourcePortId": source_port,
        "targetNodeId": target,
        "targetPortId": target_port,
    }


def _make_pdf(pages: list[str]) -> bytes:
    objects: list[bytes] = [b"<</Type/Catalog/Pages 2 0 R>>"]
    kids = " ".join(f"{3 + 2 * index} 0 R" for index in range(len(pages)))
    objects.append(("<</Type/Pages/Kids[" + kids + f"]/Count {len(pages)}>>").encode())
    font_object = 3 + 2 * len(pages)
    for index, text in enumerate(pages):
        content = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.extend(_pdf_page_objects(index, font_object, content))
    objects.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    return _serialize_pdf(objects)


def _pdf_page_objects(index: int, font_object: int, content: bytes) -> list[bytes]:
    page = (
        f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents {4 + 2 * index} 0 R"
        f"/Resources<</Font<</F1 {font_object} 0 R>>>>>>"
    ).encode()
    stream = b"<</Length %d>>stream\n" % len(content) + content + b"\nendstream"
    return [page, stream]


def _serialize_pdf(objects: list[bytes]) -> bytes:
    output = b"%PDF-1.4\n"
    offsets: list[int] = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(output))
        output += f"{number} 0 obj".encode() + body + b"endobj\n"
    xref = len(output)
    output += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        output += b"%010d 00000 n \n" % offset
    return output + b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % (len(objects) + 1, xref)
