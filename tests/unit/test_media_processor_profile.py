"""Media processor profile dispatch (Media/Content Plane M6 wiring).

`FOUNDRY_LITE_MEDIA_PROCESSOR_PROFILE` selects the processor family independently of storage.
M6 adds the `image-pillow` and `ffprobe` branches — assert each resolves to its adapter.
"""

from __future__ import annotations

import pytest
from foundry_lite.application.ports.media_processor import ProcessorSpec
from foundry_lite.infrastructure.adapters import (
    AsrProcessorAdapter,
    ImageProcessorAdapter,
    OcrProcessorAdapter,
    PdfLayoutProcessorAdapter,
    PdfOcrProcessorAdapter,
    PdfTextProcessorAdapter,
    VideoProbeProcessorAdapter,
    VideoSceneVisionProcessorAdapter,
)
from foundry_lite.infrastructure.adapters.local_vision_embedding import LocalVisionEmbeddingAdapter
from foundry_lite.infrastructure.adapters.media_processor_registry import build_default_media_processor_registry
from foundry_lite.infrastructure.local_runtime import _media_processor_adapter


@pytest.mark.parametrize(
    ("profile", "adapter_type"),
    [
        ("image-pillow", ImageProcessorAdapter),
        ("ffprobe", VideoProbeProcessorAdapter),
        ("video-scene-vision", VideoSceneVisionProcessorAdapter),
        ("ocr-tesseract", OcrProcessorAdapter),
        ("asr-whisper", AsrProcessorAdapter),
        ("pdf-layout-pypdf", PdfLayoutProcessorAdapter),
        ("pdf-ocr-tesseract-poppler", PdfOcrProcessorAdapter),
        ("pdf-pypdf", PdfTextProcessorAdapter),
    ],
)
def test_profile_selects_processor_adapter(monkeypatch: pytest.MonkeyPatch, profile: str, adapter_type: type) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_MEDIA_PROCESSOR_PROFILE", profile)
    assert isinstance(_media_processor_adapter("local", LocalVisionEmbeddingAdapter()), adapter_type)


def test_default_registry_exposes_all_processor_families_in_one_runtime() -> None:
    registry = build_default_media_processor_registry(LocalVisionEmbeddingAdapter())

    processors = {descriptor.processor for descriptor in registry.descriptors()}
    assert processors == {
        "pdf_text_v1",
        "pdf_layout_v1",
        "pdf_ocr_v1",
        "ocr_v1",
        "image_v1",
        "asr_v1",
        "video_probe_v1",
        "video_frames_v1",
        "video_vision_v1",
    }
    assert registry.resolve(ProcessorSpec("pdf_text_v1", "1"), input_format="pdf").profile_name == "pdf-pypdf"
    assert registry.resolve(ProcessorSpec("pdf_layout_v1", "1"), input_format="pdf").profile_name == "pdf-layout-pypdf"
    assert (
        registry.resolve(ProcessorSpec("pdf_ocr_v1", "1"), input_format="pdf").profile_name
        == "pdf-ocr-tesseract-poppler"
    )
    assert registry.resolve(ProcessorSpec("ocr_v1", "1.0"), input_format="png").profile_name == "ocr-tesseract"
    assert registry.resolve(ProcessorSpec("asr_v1", "1.0.0"), input_format="wav").profile_name == "asr-whisper"
