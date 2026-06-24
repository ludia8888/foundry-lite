"""Video scene-frame OCR processor adapter (Media/Content Plane L10).

The scene-frame extractor is injected (a deterministic fake), so these run with no ffmpeg or
Tesseract binary. Cover the contract: ordered ``video_frame`` content units with deterministic
``text_hash`` + ``start_ms``, the ``video_scene_frames`` derivative kind, ``supports()`` gating,
the failure taxonomy (an unprocessable video as typed validation, a fail-closed timeout), and
the default extractor reporting ``frame_extractor_unavailable`` (real ffmpeg+Tesseract live).
"""

from __future__ import annotations

import hashlib
import threading

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.media_processor import MediaProcessingRequest, ProcessorSpec
from foundry_lite.infrastructure.adapters.video_probe_processor import (
    VideoFrameError,
    VideoSceneFrameProcessorAdapter,
    _default_scene_frame_extractor,
)

_FRAMES = [(0, "INVOICE 2026"), (1000, "TOTAL 4242 USD")]


def _request(*, processor: str = "video_frames_v1") -> MediaProcessingRequest:
    spec = ProcessorSpec(processor=processor, processor_version="1.0.0")
    return MediaProcessingRequest(
        tenant_id="t",
        media_item_version_id="miv-1",
        blob_key="blob-1",
        spec=spec,
        processing_spec_hash="spec-1",
        source_path="/sandbox/clip.mp4",
    )


def test_scene_frames_become_ordered_video_frame_units() -> None:
    adapter = VideoSceneFrameProcessorAdapter(scene_frame_extractor=lambda _path: list(_FRAMES))
    result = adapter.process(_request())

    assert result.derivative_kind == "video_scene_frames"
    assert [unit.unit_kind for unit in result.units] == ["video_frame", "video_frame"]
    assert [unit.ordinal for unit in result.units] == [0, 1]
    assert [unit.start_ms for unit in result.units] == [0, 1000]
    assert [unit.text for unit in result.units] == ["INVOICE 2026", "TOTAL 4242 USD"]
    assert result.units[0].text_hash == hashlib.sha256(b"INVOICE 2026").hexdigest()


def test_content_hash_is_deterministic_across_runs() -> None:
    adapter = VideoSceneFrameProcessorAdapter(scene_frame_extractor=lambda _path: list(_FRAMES))
    assert adapter.process(_request()).content_hash == adapter.process(_request()).content_hash


def test_supports_only_video_frames_processor() -> None:
    adapter = VideoSceneFrameProcessorAdapter(scene_frame_extractor=lambda _path: list(_FRAMES))
    assert adapter.supports(_request()) is True
    assert adapter.supports(_request(processor="ocr_v1")) is False


def test_failure_contract_declares_validation_and_timeout() -> None:
    contract = VideoSceneFrameProcessorAdapter(scene_frame_extractor=lambda _path: []).failure_contract()
    assert contract.adapter_profile == "video-scene-frames"
    assert {mode.kind for mode in contract.modes} >= {"validation", "timeout"}


def test_unprocessable_video_is_a_validation_failure() -> None:
    def _bad(_path: str) -> list[tuple[int, str]]:
        raise VideoFrameError("unprocessable_video")

    adapter = VideoSceneFrameProcessorAdapter(scene_frame_extractor=_bad)
    with pytest.raises(AdapterError) as excinfo:
        adapter.process(_request())
    assert excinfo.value.failure.kind == "validation"
    assert excinfo.value.failure.is_retryable is False


def test_timeout_fails_closed() -> None:
    release = threading.Event()

    def _blocking(_path: str) -> list[tuple[int, str]]:
        release.wait(timeout=10)
        return []

    adapter = VideoSceneFrameProcessorAdapter(timeout_seconds=0, scene_frame_extractor=_blocking)
    try:
        with pytest.raises(AdapterError) as excinfo:
            adapter.process(_request())
        assert excinfo.value.failure.kind == "timeout"
        assert excinfo.value.failure.is_retryable is True
    finally:
        release.set()


def test_process_requires_a_sandbox_source_path() -> None:
    adapter = VideoSceneFrameProcessorAdapter(scene_frame_extractor=lambda _path: list(_FRAMES))
    request = MediaProcessingRequest(
        tenant_id="t", media_item_version_id="miv-1", blob_key="b", spec=_request().spec, processing_spec_hash="s"
    )
    with pytest.raises(AdapterError) as excinfo:
        adapter.process(request)
    assert excinfo.value.failure.kind == "validation"


def test_default_extractor_without_bundled_ffmpeg_is_unavailable() -> None:
    # No ffmpeg/Tesseract is bundled in-process; the default reports a typed reason (a real
    # ``video-scene-frames`` profile injects the ffmpeg+Tesseract extractor at composition).
    with pytest.raises(VideoFrameError) as excinfo:
        _default_scene_frame_extractor("/sandbox/clip.mp4")
    assert excinfo.value.reason == "frame_extractor_unavailable"
