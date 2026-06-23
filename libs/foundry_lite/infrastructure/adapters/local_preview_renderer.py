"""Local preview renderer for on-demand access patterns (Media/Content Plane M9, doc §M9).

Renders a rebuildable preview from a committed media version's bytes — never serving truth.
The ``thumbnail`` pattern uses real Pillow (a pure-Python wheel, so the real engine runs in
CI): it decodes a sandboxed image and emits deterministic PNG thumbnail bytes, with a
pixel-count cap as a decompression-bomb guard and a wall-clock timeout so a hung decode fails
closed. Audio/video patterns (``waveform`` / ``video_frame``) report
``preview_renderer_unavailable`` because their real engine (FFmpeg) is a deferred system
binary — mirroring the live-OCR / live-ASR deferral. Reads only a sandbox path, never raw
storage credentials.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from importlib import import_module

from foundry_lite.application.ports.adapter_failure import (
    AdapterFailureContract,
    AdapterFailureMode,
)
from foundry_lite.application.ports.media_preview_renderer import (
    PreviewRenderError,
    PreviewRenderRequest,
    RenderedPreview,
)

_THUMBNAIL = "thumbnail"
_DEFAULT_TIMEOUT_SECONDS = 30
_DEFAULT_MAX_PIXELS = 64_000_000  # ~64MP resource cap (decompression-bomb guard).
_DEFAULT_THUMBNAIL_DIM = 256

# source_path, max_pixels, thumbnail_dim -> PNG bytes (raises PreviewRenderError on bad input).
ThumbnailEngine = Callable[[str, int, int], bytes]


def _pillow_thumbnail(source_path: str, max_pixels: int, thumbnail_dim: int) -> bytes:
    pil_image = import_module("PIL.Image")
    try:
        with pil_image.open(source_path) as image:
            width, height = image.size
            if width * height > max_pixels:
                raise PreviewRenderError("image_too_large")
            thumbnail = image.copy()
            thumbnail.thumbnail((thumbnail_dim, thumbnail_dim))
            buffer = io.BytesIO()
            thumbnail.convert("RGB").save(buffer, format="PNG")
    except PreviewRenderError:
        raise
    except Exception as exc:  # noqa: BLE001 - any decode error is an undecodable-image failure
        raise PreviewRenderError("undecodable_image") from exc
    return buffer.getvalue()


class LocalPreviewRendererAdapter:
    """``MediaPreviewRendererAdapter`` that renders image thumbnails (profile ``local-preview``)."""

    profile_name = "local-preview"

    def __init__(
        self,
        *,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
        max_pixels: int = _DEFAULT_MAX_PIXELS,
        thumbnail_dim: int = _DEFAULT_THUMBNAIL_DIM,
        thumbnail_engine: ThumbnailEngine | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_pixels = max_pixels
        self._thumbnail_dim = thumbnail_dim
        self._thumbnail_engine = thumbnail_engine or _pillow_thumbnail

    def supports(self, access_pattern: str) -> bool:
        return access_pattern == _THUMBNAIL

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "render",
                    "validation",
                    False,
                    "Source is undecodable, over the pixel cap, or has no bundled engine for the pattern.",
                ),
                AdapterFailureMode(
                    "render",
                    "timeout",
                    True,
                    "Preview render exceeded its time budget (possible decompression bomb); retry bounded.",
                    timeout_seconds=_DEFAULT_TIMEOUT_SECONDS,
                ),
            ),
        )

    def render(self, request: PreviewRenderRequest) -> RenderedPreview:
        if request.access_pattern != _THUMBNAIL:
            # Audio/video previews need FFmpeg, a deferred system binary; fail closed rather
            # than fabricate an empty preview (the real renderer is injected by a future profile).
            raise PreviewRenderError("preview_renderer_unavailable")
        content = self._render_within_timeout(request.source_path)
        return RenderedPreview(access_pattern=_THUMBNAIL, content=content, mime_type="image/png")

    def _render_within_timeout(self, source_path: str) -> bytes:
        # Not a context manager: on timeout we abandon the worker (shutdown wait=False)
        # rather than block on shutdown(wait=True) for a hung decode.
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(self._thumbnail_engine, source_path, self._max_pixels, self._thumbnail_dim)
        try:
            result = future.result(timeout=self._timeout_seconds)
            pool.shutdown(wait=True)
            return result
        except FuturesTimeoutError as exc:
            pool.shutdown(wait=False)
            raise PreviewRenderError("render_timeout") from exc
