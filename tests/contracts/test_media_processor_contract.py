"""Contract for ``MediaProcessorAdapter`` (ADR-0001 §5.2 / §7.3).

Processing is pinned to (version + processing_spec_hash); the spec hash is the
idempotency key. Contract only this sprint — the first concrete processor (PDF raw
text) lands later. We lock the Protocol shape and the spec/request/result DTOs.
"""

from __future__ import annotations

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.media_processor import (
    MediaProcessingRequest,
    MediaProcessingResult,
    MediaProcessorAdapter,
    ProcessorSpec,
)


class _FakeMediaProcessor:
    """Processor that only handles the document/pdf spec it was pinned to."""

    @property
    def profile_name(self) -> str:
        return "fake-processor"

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile="fake-processor", modes=())

    def supports(self, request: MediaProcessingRequest) -> bool:
        return request.spec.processor == "pdf-raw-text"

    def process(self, request: MediaProcessingRequest) -> MediaProcessingResult:
        return MediaProcessingResult(
            media_item_version_id=request.media_item_version_id,
            processing_spec_hash=request.processing_spec_hash,
        )


def test_media_processor_pins_spec_hash_and_reports_support() -> None:
    adapter: MediaProcessorAdapter = _FakeMediaProcessor()
    spec = ProcessorSpec(processor="pdf-raw-text", processor_version="1")
    request = MediaProcessingRequest(
        tenant_id="t",
        media_item_version_id="miv-1",
        blob_key="blob-1",
        spec=spec,
        processing_spec_hash="sha-1",
    )

    assert adapter.supports(request) is True
    assert (
        adapter.supports(
            MediaProcessingRequest(
                tenant_id="t",
                media_item_version_id="miv-2",
                blob_key="blob-2",
                spec=ProcessorSpec(processor="asr", processor_version="1"),
                processing_spec_hash="sha-2",
            )
        )
        is False
    )
    result = adapter.process(request)
    assert result.processing_spec_hash == "sha-1"
    assert adapter.failure_contract().adapter_profile == "fake-processor"
