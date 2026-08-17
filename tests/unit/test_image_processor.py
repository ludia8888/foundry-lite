"""Image processor adapter (Media/Content Plane M6).

Pillow is a pure-Python wheel, so the REAL describer runs in CI — tests build a tiny PNG with
Pillow and assert deterministic metadata + thumbnail hashes, the pixel-cap resource guard
(M-T3-001), the undecodable-image validation, supports() gating, the failure taxonomy, and a
fail-closed timeout (no system binary, no sleep).
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.media_processor import MediaProcessingRequest, ProcessorSpec
from foundry_lite.infrastructure.adapters.image_processor import (
    ImageDescription,
    ImageDocumentError,
    ImageProcessorAdapter,
    _pillow_describe,
)
from PIL import Image


def _request(*, processor: str = "image_v1", source_path: str = "/sandbox/pic.png") -> MediaProcessingRequest:
    spec = ProcessorSpec(processor=processor, processor_version="1.0.0")
    return MediaProcessingRequest(
        tenant_id="t",
        media_item_version_id="miv-1",
        blob_key="blob-1",
        spec=spec,
        processing_spec_hash="spec-1",
        source_path=source_path,
    )


def _write_png(path: Path, *, size: tuple[int, int] = (320, 200), color: str = "red") -> Path:
    Image.new("RGB", size, color=color).save(path, format="PNG")
    return path


def test_real_pillow_describes_metadata_and_thumbnail_deterministically(tmp_path: Path) -> None:
    _write_png(tmp_path / "pic.png", size=(400, 200))
    adapter = ImageProcessorAdapter()  # real Pillow describer, no injection
    request = _request(source_path=str(tmp_path / "pic.png"))

    first = adapter.process(request)
    second = adapter.process(request)

    assert first.derivative_kind == "thumbnail"
    assert first.mime_type == "application/json"
    unit = first.units[0]
    assert "format=PNG" in unit.text and "width=400 height=200" in unit.text
    # thumbnail fits within the 256-box preserving aspect: 400x200 -> 256x128.
    assert "thumbnail=256x128" in unit.text
    assert first.content_hash == second.content_hash


def test_real_pillow_pixel_cap_is_a_resource_validation_failure(tmp_path: Path) -> None:
    _write_png(tmp_path / "pic.png", size=(64, 64))
    adapter = ImageProcessorAdapter(max_pixels=100)  # 64*64 > 100 -> decompression-bomb guard
    with pytest.raises(AdapterError) as excinfo:
        adapter.process(_request(source_path=str(tmp_path / "pic.png")))
    assert excinfo.value.failure.kind == "validation"
    assert excinfo.value.failure.is_retryable is False


def test_real_pillow_undecodable_bytes_are_a_validation_failure(tmp_path: Path) -> None:
    (tmp_path / "pic.png").write_bytes(b"not an image")
    adapter = ImageProcessorAdapter()
    with pytest.raises(AdapterError) as excinfo:
        adapter.process(_request(source_path=str(tmp_path / "pic.png")))
    assert excinfo.value.failure.kind == "validation"


def test_describe_helper_raises_typed_error_for_garbage(tmp_path: Path) -> None:
    (tmp_path / "junk").write_bytes(b"\x00\x01\x02")
    with pytest.raises(ImageDocumentError) as excinfo:
        _pillow_describe(str(tmp_path / "junk"), 1_000_000, 256)
    assert excinfo.value.reason == "undecodable_image"


def test_supports_only_image_processor() -> None:
    adapter = ImageProcessorAdapter()
    assert adapter.supports(_request()) is True
    assert adapter.supports(_request(processor="pdf_text_v1")) is False


def test_failure_contract_declares_validation_and_timeout() -> None:
    contract = ImageProcessorAdapter().failure_contract()
    assert contract.adapter_profile == "image-pillow"
    assert {mode.kind for mode in contract.modes} >= {"validation", "timeout"}


def test_process_requires_a_sandbox_source_path() -> None:
    adapter = ImageProcessorAdapter()
    request = MediaProcessingRequest(
        tenant_id="t", media_item_version_id="miv-1", blob_key="b", spec=_request().spec, processing_spec_hash="s"
    )
    with pytest.raises(AdapterError) as excinfo:
        adapter.process(request)
    assert excinfo.value.failure.kind == "validation"


def test_timeout_fails_closed() -> None:
    release = threading.Event()

    def _blocking(_path: str, _pixels: int, _dim: int) -> ImageDescription:
        release.wait(timeout=10)
        return ImageDescription("PNG", "RGB", 1, 1, 1, 1, "never")

    adapter = ImageProcessorAdapter(timeout_seconds=0, image_describer=_blocking)
    try:
        with pytest.raises(AdapterError) as excinfo:
            adapter.process(_request())
        assert excinfo.value.failure.kind == "timeout"
        assert excinfo.value.failure.is_retryable is True
    finally:
        release.set()


def test_injected_describer_too_large_is_validation() -> None:
    def _bomb(_path: str, _pixels: int, _dim: int) -> ImageDescription:
        raise ImageDocumentError("image_too_large")

    adapter = ImageProcessorAdapter(image_describer=_bomb)
    with pytest.raises(AdapterError) as excinfo:
        adapter.process(_request())
    assert excinfo.value.failure.kind == "validation"


def test_repeated_timeouts_do_not_accumulate_worker_threads() -> None:
    # A per-call ThreadPoolExecutor abandoned on timeout leaks a live thread per timeout;
    # repeated timeouts must NOT grow the thread count unboundedly.
    release = threading.Event()

    def _blocking(_path: str, _max_pixels: int, _thumb: int) -> ImageDescription:
        release.wait(timeout=10)
        raise ImageDocumentError("never")

    baseline = threading.active_count()
    try:
        for _ in range(20):
            # A new adapter mirrors repeatedly composed Foundry runtimes. The bound must hold
            # across instances, not only across repeated calls on one adapter.
            adapter = ImageProcessorAdapter(timeout_seconds=0, image_describer=_blocking)
            with pytest.raises(AdapterError) as excinfo:
                adapter.process(_request())
            assert excinfo.value.failure.kind == "timeout"
        assert threading.active_count() - baseline <= 8
    finally:
        release.set()
