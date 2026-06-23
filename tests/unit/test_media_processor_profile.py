"""Media processor profile dispatch (Media/Content Plane M6 wiring).

`FOUNDRY_LITE_MEDIA_PROCESSOR_PROFILE` selects the processor family independently of storage.
M6 adds the `image-pillow` and `ffprobe` branches — assert each resolves to its adapter.
"""

from __future__ import annotations

import pytest
from foundry_lite.infrastructure.adapters import (
    AsrProcessorAdapter,
    ImageProcessorAdapter,
    OcrProcessorAdapter,
    PdfTextProcessorAdapter,
    VideoProbeProcessorAdapter,
)
from foundry_lite.infrastructure.local_runtime import _media_processor_adapter


@pytest.mark.parametrize(
    ("profile", "adapter_type"),
    [
        ("image-pillow", ImageProcessorAdapter),
        ("ffprobe", VideoProbeProcessorAdapter),
        ("ocr-tesseract", OcrProcessorAdapter),
        ("asr-whisper", AsrProcessorAdapter),
        ("pdf-pypdf", PdfTextProcessorAdapter),
    ],
)
def test_profile_selects_processor_adapter(monkeypatch: pytest.MonkeyPatch, profile: str, adapter_type: type) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_MEDIA_PROCESSOR_PROFILE", profile)
    assert isinstance(_media_processor_adapter("local"), adapter_type)
