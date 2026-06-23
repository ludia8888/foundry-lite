"""Preview renderer boundary for on-demand access patterns (Media/Content Plane M9).

An access pattern (thumbnail / waveform / video frame) is a *rebuildable* derived view of a
committed media version, never serving truth (ADR-0001 invariant: ``access_pattern_cache_is_
not_truth``). The renderer turns sandboxed source bytes into preview bytes; the image pattern
runs a real engine (pure-Python Pillow, so it works in CI), while audio/video patterns default
to ``preview_renderer_unavailable`` because their real engine (FFmpeg) is a deferred system
binary — exactly like live-OCR / live-ASR. The renderer reads only a sandbox path, never raw
storage credentials (doc §5.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract


def _empty_spec() -> dict[str, object]:
    return {}


@dataclass(frozen=True)
class PreviewRenderRequest:
    """A request to render one access-pattern preview from a sandbox source path."""

    access_pattern: str
    source_path: str
    spec: dict[str, object] = field(default_factory=_empty_spec)


@dataclass(frozen=True)
class RenderedPreview:
    """The rendered preview bytes + their MIME type (the cache stores these, not the source)."""

    access_pattern: str
    content: bytes
    mime_type: str


class PreviewRenderError(Exception):
    """Raised when a source is unrenderable or no engine is bundled for the access pattern."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class MediaPreviewRendererAdapter(Protocol):
    """Render boundary for on-demand access-pattern previews (caches, never truth)."""

    @property
    def profile_name(self) -> str: ...

    def supports(self, access_pattern: str) -> bool:
        """Whether this renderer can produce the given access pattern."""
        ...

    def render(self, request: PreviewRenderRequest) -> RenderedPreview:
        """Render preview bytes from a sandbox source path, or raise ``PreviewRenderError``."""
        ...

    def failure_contract(self) -> AdapterFailureContract:
        """Declare the typed failure modes (validation / timeout) this renderer fails closed on."""
        ...
