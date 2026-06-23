"""Local OCR processor (Media/Content Plane M5, doc §7.2/§8.2).

Optical character recognition is a SEPARATE processor family from PDF raw text: it
produces a distinct ``ocr_v1`` derivative (it never overwrites embedded text), and its
output is model-pinned because OCR is nondeterministic across engine versions. The OCR
engine is injectable — the default raises ``ocr_engine_unavailable`` because no system OCR
binary is bundled (a real profile injects one; live OCR is deferred like live-ES/live-S3),
so tests inject a deterministic fake and need no system binary.
Extraction runs in a worker thread bounded by a wall clock; a hung/over-large image fails
closed (typed timeout), and an undecodable image is a typed validation failure.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError

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

_DERIVATIVE_KIND = "ocr_v1"
_DEFAULT_TIMEOUT_SECONDS = 60

# source_path -> one text block per page/region (a single image yields one block).
OcrEngine = Callable[[str], Sequence[str]]


class OcrDocumentError(Exception):
    """Raised by an OCR engine when an image is undecodable or over the size limit."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _default_ocr_engine(source_path: str) -> list[str]:
    # No OCR engine is bundled (no system Tesseract in CI); a real profile injects one.
    # Shipping the adapter + contract now and deferring the live engine mirrors the
    # live-ES / live-S3 deferral — the injectable seam is what M5 proves.
    del source_path
    raise OcrDocumentError("ocr_engine_unavailable")


class OcrProcessorAdapter:
    """``MediaProcessorAdapter`` that OCRs an image into text (profile ``ocr-tesseract``)."""

    profile_name = "ocr-tesseract"

    def __init__(self, *, timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS, ocr_engine: OcrEngine | None = None) -> None:
        self._timeout_seconds = timeout_seconds
        self._ocr_engine = ocr_engine or _default_ocr_engine

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "process",
                    "validation",
                    False,
                    "Image is undecodable or exceeds the size limit; it cannot be OCR'd as-is.",
                ),
                AdapterFailureMode(
                    "process",
                    "timeout",
                    True,
                    "OCR exceeded its time budget (possible decompression bomb); retry bounded.",
                    timeout_seconds=_DEFAULT_TIMEOUT_SECONDS,
                ),
            ),
        )

    def supports(self, request: MediaProcessingRequest) -> bool:
        return request.spec.processor == "ocr_v1"

    def process(self, request: MediaProcessingRequest) -> MediaProcessingResult:
        if request.source_path is None:
            raise self._error("validation", "ocr processor requires a sandbox source_path", request, is_retryable=False)
        blocks = self._ocr_within_timeout(request)
        units = tuple(
            ProcessedContentUnit(
                unit_kind="page",
                ordinal=index,
                page_number=index + 1,
                text=text,
                text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
            for index, text in enumerate(blocks)
        )
        return MediaProcessingResult(
            media_item_version_id=request.media_item_version_id,
            processing_spec_hash=request.processing_spec_hash,
            derivative_kind=_DERIVATIVE_KIND,
            content_hash=_content_hash(units),
            mime_type="text/plain",
            units=units,
        )

    def _ocr_within_timeout(self, request: MediaProcessingRequest) -> Sequence[str]:
        assert request.source_path is not None
        # Not a context manager: on timeout we abandon the worker (shutdown wait=False)
        # rather than block on shutdown(wait=True) for a hung OCR call.
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(self._ocr_engine, request.source_path)
        try:
            result = future.result(timeout=self._timeout_seconds)
            pool.shutdown(wait=True)
            return result
        except FuturesTimeoutError as exc:
            pool.shutdown(wait=False)
            raise self._error("timeout", "ocr timed out", request, is_retryable=True) from exc
        except OcrDocumentError as exc:
            pool.shutdown(wait=False)
            raise self._error("validation", exc.reason, request, is_retryable=False) from exc

    def _error(self, kind: str, reason: str, request: MediaProcessingRequest, *, is_retryable: bool) -> AdapterError:
        return AdapterError(
            AdapterFailure(
                self.profile_name,
                "process",
                kind,  # type: ignore[arg-type]
                is_retryable,
                f"OCR processing failed: {reason}",
                timeout_seconds=_DEFAULT_TIMEOUT_SECONDS if kind == "timeout" else None,
                details={
                    "mediaItemVersionId": request.media_item_version_id,
                    "processorSpecHash": request.processing_spec_hash,
                    "reason": reason,
                },
            )
        )


def _content_hash(units: tuple[ProcessedContentUnit, ...]) -> str:
    digest = hashlib.sha256()
    for unit in units:
        digest.update(unit.text_hash.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()
