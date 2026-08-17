"""Local OCR processor (Media/Content Plane M5, doc §7.2/§8.2; real engine L1).

Optical character recognition is a SEPARATE processor family from PDF raw text: it
produces a distinct ``ocr_v1`` derivative (it never overwrites embedded text), and its
output is model-pinned because OCR is nondeterministic across engine versions. The OCR
engine is injectable — the ``ocr-tesseract`` profile injects the real Tesseract engine
(``_tesseract_ocr_engine``, L1) while the in-process default still raises
``ocr_engine_unavailable`` so deterministic unit tests inject a fake and need no binary.
Extraction runs in a worker thread bounded by a wall clock; a hung/over-large image fails
closed (typed timeout), and an undecodable image is a typed validation failure.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
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

_DERIVATIVE_KIND = "ocr_v1"
_DEFAULT_TIMEOUT_SECONDS = 60
# Cap on live worker threads across every adapter instance in this process. An in-thread OCR
# call cannot be force-cancelled, so on timeout we abandon the worker; a process-wide bounded
# pool prevents independently composed runtimes from each retaining their own worker threads.
_MAX_WORKER_THREADS = 4
_PROCESSOR_EXECUTOR = ThreadPoolExecutor(max_workers=_MAX_WORKER_THREADS, thread_name_prefix="ocr")

# source_path -> one text block per page/region (a single image yields one block).
OcrEngine = Callable[[str], Sequence[str]]


class OcrDocumentError(Exception):
    """Raised by an OCR engine when an image is undecodable or over the size limit."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _default_ocr_engine(source_path: str) -> list[str]:
    # In-process default: no OCR engine is wired here, so deterministic unit tests inject a
    # fake and the seam stays covered. The real ``ocr-tesseract`` profile injects
    # ``_tesseract_ocr_engine`` (L1) instead of relying on this default.
    del source_path
    raise OcrDocumentError("ocr_engine_unavailable")


def _tesseract_ocr_engine(source_path: str) -> list[str]:
    # Real engine (L1): lazily import the system-binary wrapper + Pillow so the module has
    # no import-time dependency on tesseract. A decode/OCR error is an undecodable-image
    # validation failure (the adapter classifies it as a typed ``validation`` failure).
    pytesseract = import_module("pytesseract")
    pil_image = import_module("PIL.Image")
    try:
        with pil_image.open(source_path) as image:
            # timeout kills the tesseract SUBPROCESS if it hangs, so the actual work stops
            # (the outer worker thread cannot cancel an in-thread call) — no zombie tesseract.
            text = pytesseract.image_to_string(image, timeout=_DEFAULT_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - any decode/OCR error is an undecodable-image validation failure
        raise OcrDocumentError("undecodable_image") from exc
    return [text.strip()]


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
        # Process-wide bounded pool: repeated composition roots cannot multiply the live-thread
        # cap when timed-out in-thread OCR calls cannot be force-cancelled.
        future = _PROCESSOR_EXECUTOR.submit(self._ocr_engine, request.source_path)
        try:
            return future.result(timeout=self._timeout_seconds)
        except FuturesTimeoutError as exc:
            future.cancel()
            raise self._error("timeout", "ocr timed out", request, is_retryable=True) from exc
        except OcrDocumentError as exc:
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
