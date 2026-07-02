"""Local image processor (Media/Content Plane M6, doc §7/§8).

Extracts image metadata (format/mode/dimensions) and a deterministic thumbnail spec with
Pillow — a pure-Python wheel, so there is NO system binary, and the real engine runs in CI.
Decoding runs in a worker thread bounded by a wall clock so a decompression-bomb / hung
decode fails closed (typed timeout) rather than hanging. An image whose pixel count exceeds
the cap (a decompression-bomb guard, M-T3-001) or an undecodable image is a typed validation
failure — never a partial result. The processor reads only a sandbox file path, never raw
storage credentials (doc §5.2), and its output is deterministic: the same bytes + spec yield
an identical thumbnail hash, which is what makes processing idempotent.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from importlib import import_module

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureMode,
)
from foundry_lite.application.ports.media_processor import (
    MediaProcessingRequest,
    MediaProcessingResult,
    ProcessedContentUnit,
)

_DERIVATIVE_KIND = "thumbnail"
_DEFAULT_TIMEOUT_SECONDS = 30
_DEFAULT_MAX_PIXELS = 64_000_000  # ~64MP resource cap (decompression-bomb guard, M-T3-001).
_DEFAULT_THUMBNAIL_DIM = 256
# Cap on live worker threads. An in-thread Pillow decode cannot be force-cancelled, so on timeout
# we abandon the worker; a SHARED bounded pool (not a fresh pool per call) means repeated timeouts
# reuse a fixed thread set instead of leaking one live thread per timeout.
_MAX_WORKER_THREADS = 4


@dataclass(frozen=True)
class ImageDescription:
    """Image metadata + the deterministic thumbnail spec a derivative pins."""

    image_format: str
    mode: str
    width: int
    height: int
    thumbnail_width: int
    thumbnail_height: int
    thumbnail_hash: str


# source_path, max_pixels, thumbnail_dim -> description (raises ImageDocumentError on bad input).
ImageDescriber = Callable[[str, int, int], ImageDescription]


class ImageDocumentError(Exception):
    """Raised by an image describer when an image is undecodable or over the pixel cap."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _pillow_describe(source_path: str, max_pixels: int, thumbnail_dim: int) -> ImageDescription:
    pil_image = import_module("PIL.Image")
    try:
        with pil_image.open(source_path) as image:
            width, height = image.size
            if width * height > max_pixels:
                raise ImageDocumentError("image_too_large")
            image_format, mode = image.format or "UNKNOWN", image.mode
            thumbnail = image.copy()
            thumbnail.thumbnail((thumbnail_dim, thumbnail_dim))
            buffer = io.BytesIO()
            thumbnail.convert("RGB").save(buffer, format="PNG")
            thumbnail_size = thumbnail.size
    except ImageDocumentError:
        raise
    except Exception as exc:  # noqa: BLE001 - any decode error is an undecodable-image validation failure
        raise ImageDocumentError("undecodable_image") from exc
    return ImageDescription(
        image_format=image_format,
        mode=mode,
        width=width,
        height=height,
        thumbnail_width=thumbnail_size[0],
        thumbnail_height=thumbnail_size[1],
        thumbnail_hash=hashlib.sha256(buffer.getvalue()).hexdigest(),
    )


class ImageProcessorAdapter:
    """``MediaProcessorAdapter`` that extracts image metadata + a thumbnail spec (profile ``image-pillow``)."""

    profile_name = "image-pillow"

    def __init__(
        self,
        *,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
        max_pixels: int = _DEFAULT_MAX_PIXELS,
        thumbnail_dim: int = _DEFAULT_THUMBNAIL_DIM,
        image_describer: ImageDescriber | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_pixels = max_pixels
        self._thumbnail_dim = thumbnail_dim
        self._image_describer = image_describer or _pillow_describe
        self._executor = ThreadPoolExecutor(max_workers=_MAX_WORKER_THREADS, thread_name_prefix="image")

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "process",
                    "validation",
                    False,
                    "Image is undecodable or exceeds the pixel cap; it cannot be processed as-is.",
                ),
                AdapterFailureMode(
                    "process",
                    "timeout",
                    True,
                    "Image processing exceeded its time budget (possible decompression bomb); retry bounded.",
                    timeout_seconds=_DEFAULT_TIMEOUT_SECONDS,
                ),
            ),
        )

    def supports(self, request: MediaProcessingRequest) -> bool:
        return request.spec.processor == "image_v1"

    def process(self, request: MediaProcessingRequest) -> MediaProcessingResult:
        if request.source_path is None:
            raise self._error(
                "validation", "image processor requires a sandbox source_path", request, is_retryable=False
            )
        description = self._describe_within_timeout(request)
        text = _describe_text(description)
        unit = ProcessedContentUnit(
            unit_kind="image",
            ordinal=0,
            text=text,
            text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
        return MediaProcessingResult(
            media_item_version_id=request.media_item_version_id,
            processing_spec_hash=request.processing_spec_hash,
            derivative_kind=_DERIVATIVE_KIND,
            content_hash=unit.text_hash,
            mime_type="application/json",
            units=(unit,),
        )

    def _describe_within_timeout(self, request: MediaProcessingRequest) -> ImageDescription:
        assert request.source_path is not None
        # Shared bounded pool: on timeout we abandon the worker (an in-thread Pillow decode cannot
        # be cancelled) but reuse a fixed thread set, so repeated timeouts do not leak threads.
        future = self._executor.submit(
            self._image_describer, request.source_path, self._max_pixels, self._thumbnail_dim
        )
        try:
            return future.result(timeout=self._timeout_seconds)
        except FuturesTimeoutError as exc:
            future.cancel()
            raise self._error("timeout", "image processing timed out", request, is_retryable=True) from exc
        except ImageDocumentError as exc:
            raise self._error("validation", exc.reason, request, is_retryable=False) from exc

    def _error(self, kind: str, reason: str, request: MediaProcessingRequest, *, is_retryable: bool) -> AdapterError:
        return AdapterError(
            AdapterFailure(
                self.profile_name,
                "process",
                kind,  # type: ignore[arg-type]
                is_retryable,
                f"Image processing failed: {reason}",
                timeout_seconds=_DEFAULT_TIMEOUT_SECONDS if kind == "timeout" else None,
                details={
                    "mediaItemVersionId": request.media_item_version_id,
                    "processorSpecHash": request.processing_spec_hash,
                    "reason": reason,
                },
            )
        )


def _describe_text(description: ImageDescription) -> str:
    return (
        f"format={description.image_format} mode={description.mode} "
        f"width={description.width} height={description.height} "
        f"thumbnail={description.thumbnail_width}x{description.thumbnail_height} "
        f"thumbnailHash={description.thumbnail_hash}"
    )
