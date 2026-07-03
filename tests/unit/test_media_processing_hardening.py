"""Reliability hardening for the media processing orchestrator.

Two regression classes proven over a real SQLite engine + local storage:

* Bug 1 (retry poisoning): a retryable processor failure records a FAILED derivative
  whose ``derivative_kind`` collides with the eventual SUCCESS derivative on
  ``uq_media_derivative_spec`` (OCR/ASR: success kind == ``spec.processor``). A later
  retry must still be able to STAGE + COMMIT a fresh derivative instead of forever
  raising ``ConflictDetected`` off the poisoned FAILED row.
* Bug 2 (stuck RUNNING run): any failure on the commit path (outbox failure,
  IntegrityError, ConflictDetected) after ``_open_run`` must route to a durable FAILED
  run — the run is never left RUNNING with no verdict.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pytest
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailure
from foundry_lite.application.ports.media_processor import (
    MediaProcessingRequest,
    MediaProcessingResult,
    ProcessedContentUnit,
    ProcessorSpec,
)
from foundry_lite.application.services.media.catalog import MediaCatalogService, MediaSetSpec
from foundry_lite.application.services.media.processing import MediaProcessingService
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.media.uploads import MediaUploadInput, MediaUploadService
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.local_media_storage import LocalMediaStorageAdapter
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaDerivativeRepository, SqlAlchemyMediaRepository
from sqlalchemy import create_engine

# OCR spec: the SUCCESS derivative_kind ("ocr_v1") equals spec.processor, so a FAILED
# evidence row and the eventual COMMITTED row share the uq_media_derivative_spec key.
_SPEC = ProcessorSpec(processor="ocr_v1", processor_version="1.0", model="tesseract", model_version="5.3.0")


class _FakeRuntime:
    def __init__(self) -> None:
        self.fail_outbox = False

    def _audit(self, conn: TransactionContext, ctx: RequestContext, *, event_type: str, **_: object) -> None: ...

    def _outbox(
        self, conn: TransactionContext, ctx: RequestContext, event_type: str, *_: object, **__: object
    ) -> str | None:
        if self.fail_outbox:
            raise RuntimeError("injected outbox failure")
        return event_type


class _FlakyOcrProcessor:
    """Raises a retryable timeout on the first N calls, then succeeds with kind ``ocr_v1``."""

    profile_name = "ocr-tesseract"

    def __init__(self, *, fail_times: int = 1) -> None:
        self._fail_times = fail_times
        self.calls = 0

    def failure_contract(self) -> object: ...

    def supports(self, request: MediaProcessingRequest) -> bool:
        return True

    def process(self, request: MediaProcessingRequest) -> MediaProcessingResult:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise AdapterError(
                AdapterFailure("ocr-tesseract", "process", "timeout", True, "ocr timed out", timeout_seconds=60)
            )
        unit = ProcessedContentUnit(unit_kind="page", ordinal=0, page_number=1, text="recovered", text_hash="h-1")
        return MediaProcessingResult(
            media_item_version_id=request.media_item_version_id,
            processing_spec_hash=request.processing_spec_hash,
            derivative_kind="ocr_v1",
            content_hash="content-1",
            units=(unit,),
        )


@dataclass(frozen=True)
class _Env:
    ctx: RequestContext
    processing: MediaProcessingService
    runtime: _FakeRuntime
    processor: _FlakyOcrProcessor
    version_id: str


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    engine = create_engine(f"sqlite:///{tmp_path / 'harden.db'}", future=True)
    db.create_database(engine)
    repo = SqlAlchemyMediaRepository(engine)
    deriv = SqlAlchemyMediaDerivativeRepository(engine)
    storage = LocalMediaStorageAdapter(tmp_path / "media")
    runtime = _FakeRuntime()
    processor = _FlakyOcrProcessor()
    catalog = MediaCatalogService(engine=engine, media_repository=repo)
    catalog.bind_collaborators({"runtime_service": runtime})
    upload = MediaUploadService(engine=engine, media_repository=repo, media_storage=storage)
    transaction = MediaTransactionService(engine=engine, media_repository=repo, media_storage=storage)
    transaction.bind_collaborators({"runtime_service": runtime})
    processing = MediaProcessingService(
        engine=engine,
        media_repository=repo,
        media_derivative_repository=deriv,
        media_storage=storage,
        media_processor=processor,
    )
    processing.bind_collaborators({"runtime_service": runtime})
    ctx = RequestContext(tenant_id="tenant-demo")
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
        source=io.BytesIO(b"%PDF-1.4 source"),
    )
    transaction.commit(ctx, media_transaction_id=tx)
    return _Env(
        ctx=ctx,
        processing=processing,
        runtime=runtime,
        processor=processor,
        version_id=staged.media_item_version_id,
    )


def test_retry_after_retryable_failure_commits_a_derivative(env: _Env) -> None:
    # First attempt fails with a retryable timeout and records a FAILED evidence derivative
    # whose kind ("ocr_v1") collides with the eventual COMMITTED derivative's spec key.
    first = env.processing.process(env.ctx, media_item_version_id=env.version_id, spec=_SPEC)
    assert first.status == "failed"

    # The retry must recover: a COMMITTED derivative must be producible, not blocked forever
    # on the poisoned FAILED row (previously raised ConflictDetected on every retry).
    second = env.processing.process(env.ctx, media_item_version_id=env.version_id, spec=_SPEC)
    assert second.status == "committed"
    assert second.is_duplicate is False
    derivative = env.processing.resolve_derivative(env.ctx, media_derivative_id=second.media_derivative_id)
    assert derivative.status == "COMMITTED" and derivative.derivative_kind == "ocr_v1"


def test_commit_path_crash_marks_run_failed_not_running(env: _Env) -> None:
    # Make the processor succeed so we reach the commit path, then blow up the commit txn.
    env.processor._fail_times = 0
    env.runtime.fail_outbox = True
    with pytest.raises(RuntimeError):
        env.processing.process(env.ctx, media_item_version_id=env.version_id, spec=_SPEC)

    # The run opened its own txn before the crash; it must not be left RUNNING with no verdict.
    runs = env.processing.list_media_runs(env.ctx, source_media_item_version_id=env.version_id)
    assert len(runs) == 1
    assert runs[0].status == "FAILED"
    assert runs[0].finished_at is not None
    assert runs[0].failure_reason is not None
