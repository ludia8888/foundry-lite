"""Contract for exact, fail-closed media processor registry resolution."""

from __future__ import annotations

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailureContract
from foundry_lite.application.ports.media_processor import (
    MediaProcessingRequest,
    MediaProcessingResult,
    ProcessorSpec,
)
from foundry_lite.application.ports.media_processor_registry import (
    MediaProcessorDescriptor,
    MediaProcessorRegistration,
    MediaProcessorRegistry,
    ProcessorModelDescriptor,
    ProcessorPreviewCapability,
    ProcessorResourceRequirements,
)
from foundry_lite.infrastructure.adapters.media_processor_registry import StaticMediaProcessorRegistry


class _FakeProcessor:
    def __init__(self, profile_name: str) -> None:
        self.profile_name = profile_name

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(self.profile_name, ())

    def supports(self, request: MediaProcessingRequest) -> bool:
        return True

    def process(self, request: MediaProcessingRequest) -> MediaProcessingResult:
        return MediaProcessingResult(request.media_item_version_id, request.processing_spec_hash)


def _registration(processor: str, version: str, profile: str, input_format: str) -> MediaProcessorRegistration:
    adapter = _FakeProcessor(profile)
    descriptor = MediaProcessorDescriptor(
        processor=processor,
        processor_version=version,
        adapter_profile=profile,
        input_formats=(input_format,),
        output_kinds=(f"{processor}_output",),
        parameter_schema={"type": "object", "properties": {}},
        model=ProcessorModelDescriptor("model", "pinned-v1"),
        resources=ProcessorResourceRequirements(2, 1024, "cpu", 30),
        preview=ProcessorPreviewCapability("bounded", max_media_items=5),
        is_deterministic=True,
    )
    return MediaProcessorRegistration(descriptor, adapter)


def test_registry_resolves_multiple_processors_by_exact_identity() -> None:
    pdf = _registration("pdf_text_v1", "1.0", "pdf", "pdf")
    ocr = _registration("ocr_v1", "1.0", "ocr", "png")
    registry: MediaProcessorRegistry = StaticMediaProcessorRegistry((pdf, ocr))

    assert registry.resolve(ProcessorSpec("pdf_text_v1", "1.0"), input_format="PDF") is pdf.adapter
    assert registry.resolve(ProcessorSpec("ocr_v1", "1.0"), input_format="png") is ocr.adapter
    assert {descriptor.processor for descriptor in registry.descriptors()} == {"pdf_text_v1", "ocr_v1"}
    assert registry.descriptors()[0].model.version == "pinned-v1"
    assert registry.descriptors()[0].resources.memory_mb == 1024
    assert registry.descriptors()[0].preview.mode == "bounded"


@pytest.mark.parametrize(
    ("spec", "input_format", "reason"),
    [
        (ProcessorSpec("pdf_text_v1", "2.0"), "pdf", "processor_version_not_registered"),
        (ProcessorSpec("pdf_text_v1", "1.0"), "mp4", "input_format_not_supported"),
    ],
)
def test_registry_rejects_unknown_version_or_format_without_fallback(
    spec: ProcessorSpec,
    input_format: str,
    reason: str,
) -> None:
    registry = StaticMediaProcessorRegistry((_registration("pdf_text_v1", "1.0", "pdf", "pdf"),))

    with pytest.raises(AdapterError) as exc_info:
        registry.resolve(spec, input_format=input_format)

    failure = exc_info.value.failure
    assert failure.kind == "unsupported" and failure.is_retryable is False
    assert failure.details["reason"] == reason


def test_registry_rejects_duplicate_identity_during_startup() -> None:
    first = _registration("pdf_text_v1", "1.0", "pdf", "pdf")
    duplicate = _registration("pdf_text_v1", "1.0", "pdf", "pdf")

    with pytest.raises(ValueError, match="duplicate media processor registration"):
        StaticMediaProcessorRegistry((first, duplicate))
