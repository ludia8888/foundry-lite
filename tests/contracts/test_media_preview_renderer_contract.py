"""Contract for ``MediaPreviewRendererAdapter`` (Media/Content Plane M9).

A preview renderer turns sandboxed source bytes into preview bytes for an access pattern, and
declares a typed failure contract. The image pattern runs a real engine in CI; audio/video
patterns fail closed (their real engine is a deferred system binary) rather than fabricate a
preview.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from foundry_lite.application.ports.media_preview_renderer import (
    MediaPreviewRendererAdapter,
    PreviewRenderError,
    PreviewRenderRequest,
)
from foundry_lite.infrastructure.adapters.local_preview_renderer import LocalPreviewRendererAdapter
from PIL import Image


def _png(tmp_path: Path) -> str:
    path = tmp_path / "source.png"
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), color="red").save(buffer, format="PNG")
    path.write_bytes(buffer.getvalue())
    return str(path)


def test_renderer_produces_a_thumbnail_and_declares_its_contract(tmp_path: Path) -> None:
    adapter: MediaPreviewRendererAdapter = LocalPreviewRendererAdapter()
    assert adapter.supports("thumbnail") is True
    assert adapter.supports("waveform") is False

    rendered = adapter.render(PreviewRenderRequest(access_pattern="thumbnail", source_path=_png(tmp_path)))
    assert rendered.access_pattern == "thumbnail"
    assert rendered.mime_type == "image/png"
    assert rendered.content.startswith(b"\x89PNG\r\n\x1a\n")

    contract = adapter.failure_contract()
    assert contract.adapter_profile == "local-preview"
    assert {mode.kind for mode in contract.modes} >= {"validation", "timeout"}


def test_unsupported_access_pattern_fails_closed(tmp_path: Path) -> None:
    adapter = LocalPreviewRendererAdapter()
    with pytest.raises(PreviewRenderError) as excinfo:
        adapter.render(PreviewRenderRequest(access_pattern="waveform", source_path=_png(tmp_path)))
    assert excinfo.value.reason == "preview_renderer_unavailable"


def test_undecodable_source_is_a_render_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not-an-image")
    adapter = LocalPreviewRendererAdapter()
    with pytest.raises(PreviewRenderError) as excinfo:
        adapter.render(PreviewRenderRequest(access_pattern="thumbnail", source_path=str(bad)))
    assert excinfo.value.reason == "undecodable_image"
