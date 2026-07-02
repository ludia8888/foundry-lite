"""OCR processor adapter (Media/Content Plane M5).

The OCR engine is injected (a deterministic fake), so these run with no system OCR
binary. Cover the contract: deterministic per-region text hashes, supports() gating,
typed validation (undecodable) and fail-closed timeout, and the failure taxonomy.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.media_processor import MediaProcessingRequest, ProcessorSpec
from foundry_lite.infrastructure.adapters.ocr_processor import (
    OcrDocumentError,
    OcrProcessorAdapter,
    _default_ocr_engine,
)


def _request(*, processor: str = "ocr_v1") -> MediaProcessingRequest:
    spec = ProcessorSpec(processor=processor, processor_version="1.0.0", model="tesseract", model_version="5.3.0")
    return MediaProcessingRequest(
        tenant_id="t",
        media_item_version_id="miv-1",
        blob_key="blob-1",
        spec=spec,
        processing_spec_hash="spec-1",
        source_path="/sandbox/page.png",
    )


def test_ocr_extracts_regions_deterministically() -> None:
    adapter = OcrProcessorAdapter(ocr_engine=lambda _path: ["Invoice 42", "Total due"])
    first = adapter.process(_request())
    second = adapter.process(_request())

    assert first.derivative_kind == "ocr_v1"
    assert [u.text for u in first.units] == ["Invoice 42", "Total due"]
    assert first.units[0].page_number == 1
    assert first.content_hash == second.content_hash
    assert [u.text_hash for u in first.units] == [u.text_hash for u in second.units]


def test_supports_only_ocr_processor() -> None:
    adapter = OcrProcessorAdapter(ocr_engine=lambda _path: [""])
    assert adapter.supports(_request()) is True
    assert adapter.supports(_request(processor="pdf_text_v1")) is False


def test_failure_contract_declares_validation_and_timeout() -> None:
    contract = OcrProcessorAdapter(ocr_engine=lambda _path: [""]).failure_contract()
    assert contract.adapter_profile == "ocr-tesseract"
    assert {mode.kind for mode in contract.modes} >= {"validation", "timeout"}


def test_undecodable_image_is_validation_failure() -> None:
    def _bad(_path: str) -> Sequence[str]:
        raise OcrDocumentError("undecodable_image")

    adapter = OcrProcessorAdapter(ocr_engine=_bad)
    with pytest.raises(AdapterError) as excinfo:
        adapter.process(_request())
    assert excinfo.value.failure.kind == "validation"
    assert excinfo.value.failure.is_retryable is False


def test_timeout_fails_closed() -> None:
    release = threading.Event()

    def _blocking(_path: str) -> Sequence[str]:
        release.wait(timeout=10)
        return ["never"]

    adapter = OcrProcessorAdapter(timeout_seconds=0, ocr_engine=_blocking)
    try:
        with pytest.raises(AdapterError) as excinfo:
            adapter.process(_request())
        assert excinfo.value.failure.kind == "timeout"
        assert excinfo.value.failure.is_retryable is True
    finally:
        release.set()


def test_process_requires_a_sandbox_source_path() -> None:
    adapter = OcrProcessorAdapter(ocr_engine=lambda _path: ["x"])
    request = MediaProcessingRequest(
        tenant_id="t", media_item_version_id="miv-1", blob_key="b", spec=_request().spec, processing_spec_hash="s"
    )
    with pytest.raises(AdapterError) as excinfo:
        adapter.process(request)
    assert excinfo.value.failure.kind == "validation"


def test_default_engine_without_a_bundled_ocr_library_is_a_validation_failure() -> None:
    # No OCR engine is bundled for CI/local; the default raises a typed validation error
    # (a real profile injects an engine — live OCR is deferred).
    with pytest.raises(OcrDocumentError) as excinfo:
        _default_ocr_engine("/sandbox/page.png")
    assert excinfo.value.reason == "ocr_engine_unavailable"


def test_repeated_timeouts_do_not_accumulate_worker_threads() -> None:
    # A per-call ThreadPoolExecutor abandoned on timeout (shutdown wait=False) leaks one live
    # thread per timeout; repeated timeouts must NOT grow the thread count unboundedly.
    release = threading.Event()

    def _blocking(_path: str) -> Sequence[str]:
        release.wait(timeout=10)
        return ["never"]

    adapter = OcrProcessorAdapter(timeout_seconds=0, ocr_engine=_blocking)
    baseline = threading.active_count()
    try:
        for _ in range(20):
            with pytest.raises(AdapterError) as excinfo:
                adapter.process(_request())
            assert excinfo.value.failure.kind == "timeout"
        # Bounded by the shared executor, not ~20 leaked threads.
        assert threading.active_count() - baseline <= 8
    finally:
        release.set()
