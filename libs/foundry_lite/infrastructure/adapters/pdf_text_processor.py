"""Local PDF raw-text processor (Media/Content Plane M2, doc §5.2/§7).

Extracts per-page raw text from a PDF with pypdf, in a worker thread bounded by a wall
clock so a decompression-bomb / hung parse fails closed (typed timeout) rather than
hanging. Encrypted or corrupt PDFs and over-limit page counts are typed validation
failures, never a partial result. The processor reads only a sandbox file path — never
raw storage credentials (doc §5.2) — and its output is deterministic: the same bytes +
spec yield identical per-page text hashes, which is what makes processing idempotent.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
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
from foundry_lite.infrastructure.adapters.pdf_page_selection import (
    PdfPageSelection,
    PdfPageSelectionError,
    pdf_page_selection,
    selected_page_indexes,
)

_DERIVATIVE_KIND = "pdf_text"
_DEFAULT_MAX_PAGES = 5000
_DEFAULT_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class PdfExtractedPage:
    page_number: int
    text: str


PageExtractor = Callable[[str, int, PdfPageSelection], Sequence[PdfExtractedPage]]


class PdfDocumentError(Exception):
    """Raised by a page extractor when a PDF is encrypted, corrupt, or over the page limit."""

    def __init__(self, reason: str, *, page: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.page = page


def _pypdf_extract(source_path: str, max_pages: int, selection: PdfPageSelection) -> list[PdfExtractedPage]:
    pypdf = import_module("pypdf")
    try:
        reader = pypdf.PdfReader(source_path)
    except Exception as exc:  # noqa: BLE001 - any parse error is a corrupt-PDF validation failure
        raise PdfDocumentError("corrupt_pdf") from exc
    if reader.is_encrypted:
        raise PdfDocumentError("encrypted_pdf")
    pages = reader.pages
    if len(pages) > max_pages:
        raise PdfDocumentError("page_limit_exceeded")
    return [
        PdfExtractedPage(index + 1, pages[index].extract_text() or "")
        for index in selected_page_indexes(len(pages), selection)
    ]


class PdfTextProcessorAdapter:
    """``MediaProcessorAdapter`` that extracts raw page text from PDFs (profile ``pdf-pypdf``)."""

    profile_name = "pdf-pypdf"

    def __init__(
        self,
        *,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
        page_extractor: PageExtractor | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._page_extractor = page_extractor or _pypdf_extract

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "process",
                    "validation",
                    False,
                    "PDF is encrypted, corrupt, or exceeds the page limit; it cannot be processed as-is.",
                ),
                AdapterFailureMode(
                    "process",
                    "timeout",
                    True,
                    "PDF text extraction exceeded its time budget (possible decompression bomb); retry bounded.",
                    timeout_seconds=_DEFAULT_TIMEOUT_SECONDS,
                ),
            ),
        )

    def supports(self, request: MediaProcessingRequest) -> bool:
        return request.spec.processor == "pdf_text_v1"

    def process(self, request: MediaProcessingRequest) -> MediaProcessingResult:
        if request.source_path is None:
            raise self._error("validation", "pdf processor requires a sandbox source_path", request, is_retryable=False)
        max_pages = _coerce_int(request.spec.parameters.get("maxPages"), _DEFAULT_MAX_PAGES)
        try:
            selection = pdf_page_selection(request.spec.parameters)
        except PdfPageSelectionError as exc:
            raise self._error("validation", str(exc), request, is_retryable=False) from exc
        pages = self._extract_within_timeout(request, max_pages, selection)
        units = tuple(
            ProcessedContentUnit(
                unit_kind="page",
                ordinal=index,
                page_number=page.page_number,
                text=page.text,
                text_hash=hashlib.sha256(page.text.encode("utf-8")).hexdigest(),
            )
            for index, page in enumerate(pages)
        )
        return MediaProcessingResult(
            media_item_version_id=request.media_item_version_id,
            processing_spec_hash=request.processing_spec_hash,
            derivative_kind=_DERIVATIVE_KIND,
            content_hash=_content_hash(units),
            mime_type="text/plain",
            units=units,
        )

    def _extract_within_timeout(
        self,
        request: MediaProcessingRequest,
        max_pages: int,
        selection: PdfPageSelection,
    ) -> Sequence[PdfExtractedPage]:
        assert request.source_path is not None
        # Not a context manager: on timeout we must NOT block on shutdown(wait=True) for a
        # hung extraction — we abandon the worker (shutdown wait=False) and fail closed.
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(self._page_extractor, request.source_path, max_pages, selection)
        try:
            result = future.result(timeout=self._timeout_seconds)
            pool.shutdown(wait=True)
            return result
        except FuturesTimeoutError as exc:
            pool.shutdown(wait=False)
            raise self._error("timeout", "pdf text extraction timed out", request, is_retryable=True) from exc
        except PdfDocumentError as exc:
            pool.shutdown(wait=False)
            raise self._error("validation", exc.reason, request, is_retryable=False, page=exc.page) from exc

    def _error(
        self,
        kind: str,
        reason: str,
        request: MediaProcessingRequest,
        *,
        is_retryable: bool,
        page: int | None = None,
    ) -> AdapterError:
        details: dict[str, object] = {
            "mediaItemVersionId": request.media_item_version_id,
            "processorSpecHash": request.processing_spec_hash,
            "reason": reason,
        }
        if page is not None:
            details["page"] = page
        return AdapterError(
            AdapterFailure(
                self.profile_name,
                "process",
                kind,  # type: ignore[arg-type]
                is_retryable,
                f"PDF processing failed: {reason}",
                timeout_seconds=_DEFAULT_TIMEOUT_SECONDS if kind == "timeout" else None,
                details=details,
            )
        )


def _content_hash(units: tuple[ProcessedContentUnit, ...]) -> str:
    digest = hashlib.sha256()
    for unit in units:
        digest.update(unit.text_hash.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _coerce_int(value: object, default: int) -> int:
    return value if isinstance(value, int) and value > 0 else default
