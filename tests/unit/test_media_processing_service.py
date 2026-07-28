"""M2 processing-orchestration invariants for the Media/Content Plane.

Assembles the media services over a real SQLite engine + local storage with a fake
processor and a fake runtime, so the atomic-commit, idempotency, security-inheritance,
and failure-evidence behaviour can be proven without a real PDF or external infra. The
processor is faked here; the real pypdf adapter is covered by test_pdf_text_processor.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pytest
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailure, AdapterFailureContract
from foundry_lite.application.ports.media_processor import (
    MediaProcessingRequest,
    MediaProcessingResult,
    ProcessedContentUnit,
    ProcessorSpec,
)
from foundry_lite.application.ports.media_processor_registry import (
    MediaProcessorDescriptor,
    MediaProcessorRegistration,
    ProcessorModelDescriptor,
    ProcessorPreviewCapability,
    ProcessorResourceRequirements,
)
from foundry_lite.application.services.media.catalog import MediaCatalogService, MediaSetSpec
from foundry_lite.application.services.media.content_unit_metadata import source_locator_payload
from foundry_lite.application.services.media.processing import MediaProcessingService
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.media.uploads import MediaUploadInput, MediaUploadService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.local_media_storage import LocalMediaStorageAdapter
from foundry_lite.infrastructure.adapters.media_processor_registry import StaticMediaProcessorRegistry
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaDerivativeRepository, SqlAlchemyMediaRepository
from sqlalchemy import create_engine

_FUTURE = "2099-01-01T00:00:00Z"
_SPEC = ProcessorSpec(processor="pdf_text_v1", processor_version="1.0.0", parameters={"maxPages": 100})


class _FakeRuntime:
    def __init__(self) -> None:
        self.fail_outbox = False
        self.audits: list[str] = []

    def _audit(self, conn: TransactionContext, ctx: RequestContext, *, event_type: str, **_: object) -> None:
        self.audits.append(event_type)

    def _outbox(
        self, conn: TransactionContext, ctx: RequestContext, event_type: str, *_: object, **__: object
    ) -> str | None:
        if self.fail_outbox:
            raise RuntimeError("injected outbox failure")
        return event_type


class _FakeProcessor:
    """Returns two content units; ``fail_with`` injects a processor-level AdapterError."""

    profile_name = "fake-pdf"

    def __init__(self) -> None:
        self.fail_with: AdapterError | None = None
        self.last_request: MediaProcessingRequest | None = None

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())

    def supports(self, request: MediaProcessingRequest) -> bool:
        return True

    def process(self, request: MediaProcessingRequest) -> MediaProcessingResult:
        self.last_request = request
        if self.fail_with is not None:
            raise self.fail_with
        units = tuple(
            ProcessedContentUnit(
                unit_kind="page",
                ordinal=i,
                page_number=i + 1,
                text=f"page {i}",
                text_hash=f"h{i}",
                bbox={"x": 10, "y": 20, "width": 100, "height": 30, "unit": "pt"},
                source_locator={"pageNumber": i + 1, "blockId": f"block-{i}"},
                structure={"kind": "paragraph"},
                confidence=0.95,
            )
            for i in range(2)
        )
        return MediaProcessingResult(
            media_item_version_id=request.media_item_version_id,
            processing_spec_hash=request.processing_spec_hash,
            derivative_kind="pdf_text",
            content_hash="content-1",
            units=units,
        )


class _RejectingProcessor(_FakeProcessor):
    def supports(self, request: MediaProcessingRequest) -> bool:
        return False


def _registry(processor: _FakeProcessor, *, version: str = "1.0.0") -> StaticMediaProcessorRegistry:
    descriptor = MediaProcessorDescriptor(
        processor="pdf_text_v1",
        processor_version=version,
        adapter_profile=processor.profile_name,
        input_formats=("pdf",),
        output_kinds=("pdf_text",),
        model=ProcessorModelDescriptor("fake-pdf", "1"),
        resources=ProcessorResourceRequirements(1, 128),
        preview=ProcessorPreviewCapability("bounded", max_media_items=1),
    )
    return StaticMediaProcessorRegistry((MediaProcessorRegistration(descriptor, processor),))


def _registry_processing(env: _Env, registry: StaticMediaProcessorRegistry) -> MediaProcessingService:
    processing = MediaProcessingService(
        engine=env.engine,
        media_repository=env.processing.media_repository,
        media_derivative_repository=env.derivative_repo,
        media_storage=env.processing.media_storage,
        media_processor_registry=registry,
    )
    processing.bind_collaborators({"runtime_service": env.runtime})
    return processing


@dataclass(frozen=True)
class _Env:
    ctx: RequestContext
    processing: MediaProcessingService
    derivative_repo: SqlAlchemyMediaDerivativeRepository
    engine: object
    runtime: _FakeRuntime
    processor: _FakeProcessor
    version_id: str
    upload: MediaUploadService
    transaction: MediaTransactionService
    media_set_id: str

    def stage_uncommitted(self, logical_path: str) -> str:
        tx = self.transaction.open(self.ctx, media_set_id=self.media_set_id, idempotency_key=f"idem-{logical_path}")
        session = self.upload.initiate(
            self.ctx, media_set_id=self.media_set_id, logical_path=logical_path, supplied_mime_type="application/pdf"
        )
        staged = self.upload.complete(
            self.ctx,
            inputs=MediaUploadInput(
                media_set_id=self.media_set_id,
                media_transaction_id=tx,
                logical_path=logical_path,
                supplied_mime_type="application/pdf",
                schema_type="document",
                format="pdf",
                security_envelope={"tenantId": self.ctx.tenant_id, "classification": "confidential"},
            ),
            upload=session,
            source=io.BytesIO(b"%PDF-1.4 staged only"),
        )
        return staged.media_item_version_id


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    engine = create_engine(f"sqlite:///{tmp_path / 'm2.db'}", future=True)
    db.create_database(engine)
    repo = SqlAlchemyMediaRepository(engine)
    derivative_repo = SqlAlchemyMediaDerivativeRepository(engine)
    storage = LocalMediaStorageAdapter(tmp_path / "media")
    runtime = _FakeRuntime()
    processor = _FakeProcessor()
    catalog = MediaCatalogService(engine=engine, media_repository=repo)
    catalog.bind_collaborators({"runtime_service": runtime})
    upload = MediaUploadService(engine=engine, media_repository=repo, media_storage=storage)
    transaction = MediaTransactionService(engine=engine, media_repository=repo, media_storage=storage)
    transaction.bind_collaborators({"runtime_service": runtime})
    processing = MediaProcessingService(
        engine=engine,
        media_repository=repo,
        media_derivative_repository=derivative_repo,
        media_storage=storage,
        media_processor=processor,
    )
    processing.bind_collaborators({"runtime_service": runtime})
    ctx = RequestContext()
    media_set = catalog.create_media_set(
        ctx,
        MediaSetSpec(
            namespace="legal",
            name="contracts",
            schema_type="document",
            primary_format="pdf",
            allowed_input_formats=("pdf",),
            classification="confidential",
        ),
    )
    tx = transaction.open(ctx, media_set_id=media_set.media_set_id, idempotency_key="idem-1")
    session = upload.initiate(
        ctx, media_set_id=media_set.media_set_id, logical_path="/a.pdf", supplied_mime_type="application/pdf"
    )
    staged = upload.complete(
        ctx,
        inputs=MediaUploadInput(
            media_set_id=media_set.media_set_id,
            media_transaction_id=tx,
            logical_path="/a.pdf",
            supplied_mime_type="application/pdf",
            schema_type="document",
            format="pdf",
            security_envelope={"tenantId": ctx.tenant_id, "classification": "confidential"},
        ),
        upload=session,
        source=io.BytesIO(b"%PDF-1.4 source bytes"),
    )
    transaction.commit(ctx, media_transaction_id=tx)
    return _Env(
        ctx=ctx,
        processing=processing,
        derivative_repo=derivative_repo,
        engine=engine,
        runtime=runtime,
        processor=processor,
        version_id=staged.media_item_version_id,
        upload=upload,
        transaction=transaction,
        media_set_id=media_set.media_set_id,
    )


def test_process_commits_derivative_with_inherited_security_envelope(env: _Env) -> None:
    outcome = env.processing.process(env.ctx, media_item_version_id=env.version_id, spec=_SPEC)
    assert outcome.status == "committed" and outcome.content_unit_count == 2 and outcome.is_duplicate is False
    assert env.processor.last_request is not None
    assert env.processor.last_request.source_mime_type == "application/pdf"

    derivative = env.processing.resolve_derivative(env.ctx, media_derivative_id=outcome.media_derivative_id)
    assert derivative.status == "COMMITTED" and derivative.derivative_kind == "pdf_text"
    # Invariant: a derivative carries the source version's security envelope, never weaker.
    assert derivative.security_envelope.get("classification") == "confidential"
    with env.engine.begin() as conn:  # type: ignore[attr-defined]
        units = env.derivative_repo.get_content_units(
            transaction=conn,
            tenant_id=env.ctx.tenant_id,
            derivative_id=outcome.media_derivative_id,
        )
    assert units[0].bbox == {"x": 10, "y": 20, "width": 100, "height": 30, "unit": "pt"}
    assert units[0].source_locator == {"pageNumber": 1, "blockId": "block-0"}
    assert units[0].structure == {"kind": "paragraph"}
    assert units[0].confidence == pytest.approx(0.95)


def test_process_rejects_bytes_changed_after_storage_stat(
    env: _Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        env.processing.media_storage,
        "open_stream",
        lambda _blob_key: io.BytesIO(b"%PDF-1.4 tampered bytes"),
    )

    with pytest.raises(ConflictDetected) as captured:
        env.processing.process(env.ctx, media_item_version_id=env.version_id, spec=_SPEC)

    assert captured.value.details["reason"] == "stream_size_mismatch"
    assert env.processor.last_request is None


def test_content_unit_page_is_read_only_paginated_and_tenant_scoped(env: _Env) -> None:
    outcome = env.processing.process(env.ctx, media_item_version_id=env.version_id, spec=_SPEC)
    audit_count = len(env.runtime.audits)

    first = env.processing.list_derivative_content_units(
        env.ctx,
        media_derivative_id=outcome.media_derivative_id,
        limit=1,
    )
    second = env.processing.list_derivative_content_units(
        env.ctx,
        media_derivative_id=outcome.media_derivative_id,
        after_ordinal=first.next_cursor,
        limit=1,
    )

    assert [unit.ordinal for unit in first.items] == [0]
    assert first.next_cursor == 0
    assert [unit.ordinal for unit in second.items] == [1]
    assert second.next_cursor is None
    assert len(env.runtime.audits) == audit_count
    with pytest.raises(NotFound):
        env.processing.list_derivative_content_units(
            RequestContext(tenant_id="tenant-other"),
            media_derivative_id=outcome.media_derivative_id,
        )


def test_empty_source_locator_falls_back_to_processor_coordinates() -> None:
    unit = ProcessedContentUnit(
        unit_kind="page",
        ordinal=0,
        page_number=3,
        text="page 3",
        text_hash="page-3",
        source_locator={},
    )

    assert source_locator_payload(unit) == {"pageNumber": 3}


def test_process_is_idempotent_duplicate_yields_one_derivative(env: _Env) -> None:
    first = env.processing.process(env.ctx, media_item_version_id=env.version_id, spec=_SPEC)
    second = env.processing.process(env.ctx, media_item_version_id=env.version_id, spec=_SPEC)
    assert second.is_duplicate is True
    assert second.media_derivative_id == first.media_derivative_id


def test_commit_failure_leaves_no_visible_derivative(env: _Env) -> None:
    env.runtime.fail_outbox = True
    with pytest.raises(RuntimeError):
        env.processing.process(env.ctx, media_item_version_id=env.version_id, spec=_SPEC)
    # The whole commit rolled back: no committed derivative is resolvable.
    env.runtime.fail_outbox = False
    retried = env.processing.process(env.ctx, media_item_version_id=env.version_id, spec=_SPEC)
    assert retried.status == "committed" and retried.is_duplicate is False


def test_processor_failure_records_failed_derivative_evidence(env: _Env) -> None:
    env.processor.fail_with = AdapterError(
        AdapterFailure("fake-pdf", "process", "validation", False, "encrypted_pdf", details={"reason": "encrypted_pdf"})
    )
    outcome = env.processing.process(env.ctx, media_item_version_id=env.version_id, spec=_SPEC)
    assert outcome.status == "failed"
    assert outcome.error is not None and outcome.error.get("operation") == "process"
    with pytest.raises(NotFound):
        env.processing.resolve_derivative(env.ctx, media_derivative_id=outcome.media_derivative_id)


def test_registry_rejects_unregistered_processor_version_without_legacy_fallback(env: _Env) -> None:
    processing = _registry_processing(env, _registry(env.processor, version="1.0"))

    outcome = processing.process(env.ctx, media_item_version_id=env.version_id, spec=_SPEC)

    assert outcome.status == "failed"
    assert outcome.error is not None
    assert outcome.error["kind"] == "unsupported"
    details = outcome.error["details"]
    assert isinstance(details, dict)
    assert details["reason"] == "processor_version_not_registered"


def test_registry_preserves_selected_adapter_failure_taxonomy(env: _Env) -> None:
    env.processor.fail_with = AdapterError(AdapterFailure("fake-pdf", "process", "validation", False, "encrypted_pdf"))
    processing = _registry_processing(env, _registry(env.processor))

    outcome = processing.process(env.ctx, media_item_version_id=env.version_id, spec=_SPEC)

    assert outcome.error is not None
    assert outcome.error["adapterProfile"] == "fake-pdf"
    assert outcome.error["kind"] == "validation"


def test_registry_selected_adapter_rejects_unsupported_request_without_fallback(env: _Env) -> None:
    processor = _RejectingProcessor()
    processing = _registry_processing(env, _registry(processor))

    outcome = processing.process(env.ctx, media_item_version_id=env.version_id, spec=_SPEC)

    assert outcome.status == "failed"
    assert outcome.error is not None
    assert outcome.error["adapterProfile"] == "fake-pdf"
    assert outcome.error["kind"] == "unsupported"


def test_process_rejects_unknown_version(env: _Env) -> None:
    with pytest.raises(NotFound):
        env.processing.process(env.ctx, media_item_version_id="miv-missing", spec=_SPEC)


def test_process_rejects_uncommitted_version(env: _Env) -> None:
    # A STAGED (not COMMITTED) version is not serving truth and cannot be processed.
    staged_version_id = env.stage_uncommitted("/staged.pdf")
    with pytest.raises(ConflictDetected):
        env.processing.process(env.ctx, media_item_version_id=staged_version_id, spec=_SPEC)


def test_sweep_orphan_staged_derivatives(env: _Env) -> None:
    from foundry_lite.application.ports.media_derivative_repository import MediaDerivativeRecord

    with env.engine.begin() as conn:  # type: ignore[attr-defined]
        env.derivative_repo.create_derivative_or_get_existing(
            transaction=conn,
            record=MediaDerivativeRecord(
                media_derivative_id="mder-orphan",
                tenant_id=env.ctx.tenant_id,
                source_media_item_version_id=env.version_id,
                derivative_kind="pdf_text",
                processor_spec_hash="orphan-spec",
                processor_name="pdf_text_v1",
                processor_version="1.0.0",
                model_name=None,
                model_version="",
                params_hash="orphan-spec",
                security_envelope={"classification": "confidential"},
                status="STAGED",
                created_at="2026-06-20T00:00:00Z",
            ),
        )
    swept = env.processing.sweep_orphan_derivatives(env.ctx, older_than=_FUTURE)
    assert "mder-orphan" in swept
