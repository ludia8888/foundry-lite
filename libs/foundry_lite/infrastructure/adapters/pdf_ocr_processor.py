"""PDF rasterization + OCR processor with page/bbox/structure evidence.

The processor keeps OCR as a distinct immutable derivative instead of pretending that
image OCR can consume a PDF directly. Poppler rasterizes the selected page window,
Tesseract returns positioned words, and this adapter groups them into line-level
Content Units with page coordinates and explicit heuristic H1/H2/body roles.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess  # nosec B404 - fixed argv only; remove if any shell execution is introduced.
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib import import_module
from math import ceil
from pathlib import Path
from statistics import median

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureKind,
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

_DERIVATIVE_KIND = "pdf_ocr"
_DEFAULT_MAX_PAGES = 5000
_MAX_PAGES = 10_000
_DEFAULT_TIMEOUT_SECONDS = 120
_DEFAULT_DPI = 150
_MIN_DPI = 72
_MAX_DPI = 300
_LANGUAGES_PATTERN = re.compile(r"^[A-Za-z0-9_.+-]{1,80}$")
_PAGE_FILE_PATTERN = re.compile(r"-(\d+)\.png$")


@dataclass(frozen=True)
class RasterizedPdfPage:
    """One immutable page image produced inside the processor sandbox."""

    page_number: int
    image_path: str
    width: int
    height: int


@dataclass(frozen=True)
class PdfOcrLine:
    """One positioned OCR line before structure-role classification."""

    page_number: int
    text: str
    x: int
    y: int
    width: int
    height: int
    page_width: int
    page_height: int
    confidence: float


PdfRasterizer = Callable[
    [str, str, int, PdfPageSelection, int, int],
    Sequence[RasterizedPdfPage],
]
PdfOcrEngine = Callable[[RasterizedPdfPage, str, int], Sequence[PdfOcrLine]]


class PdfOcrDocumentError(Exception):
    """Normalized Poppler/Tesseract failure with typed retry semantics."""

    def __init__(
        self,
        reason: str,
        *,
        kind: AdapterFailureKind = "validation",
        is_retryable: bool = False,
        page: int | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.kind: AdapterFailureKind = kind
        self.is_retryable = is_retryable
        self.page = page


class PdfOcrProcessorAdapter:
    """Rasterize PDFs and emit line-level OCR Content Units."""

    profile_name = "pdf-ocr-tesseract-poppler"

    def __init__(
        self,
        *,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
        rasterizer: PdfRasterizer | None = None,
        ocr_engine: PdfOcrEngine | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._rasterizer = rasterizer or _poppler_rasterize
        self._ocr_engine = ocr_engine or _tesseract_page_lines

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "process",
                    "validation",
                    False,
                    "PDF is corrupt, encrypted, over-limit, or cannot be recognized with the requested OCR settings.",
                ),
                AdapterFailureMode(
                    "process",
                    "unavailable",
                    False,
                    "The pinned Poppler or Tesseract runtime is unavailable.",
                ),
                AdapterFailureMode(
                    "process",
                    "timeout",
                    True,
                    "PDF rasterization or OCR exceeded its total bounded time budget.",
                    timeout_seconds=_DEFAULT_TIMEOUT_SECONDS,
                ),
            ),
        )

    def supports(self, request: MediaProcessingRequest) -> bool:
        return request.spec.processor == "pdf_ocr_v1"

    def process(self, request: MediaProcessingRequest) -> MediaProcessingResult:
        if request.source_path is None:
            raise self._error("validation", "pdf OCR requires a sandbox source_path", request, False)
        try:
            parameters = _normalized_parameters(request)
            lines, page_numbers = self._recognize(request.source_path, parameters)
        except PdfOcrDocumentError as exc:
            raise self._error(exc.kind, exc.reason, request, exc.is_retryable, page=exc.page) from exc
        units = _ocr_units(lines)
        return MediaProcessingResult(
            media_item_version_id=request.media_item_version_id,
            processing_spec_hash=request.processing_spec_hash,
            derivative_kind=_DERIVATIVE_KIND,
            content_hash=_content_hash(units),
            mime_type="application/json",
            units=units,
            processing_evidence={
                "toolchainVersion": pdf_ocr_model_version(),
                "dpi": parameters.dpi,
                "languages": parameters.languages,
                "selectedPages": page_numbers,
                "classificationMethod": "tesseract_line_height_position_heuristic_v1",
            },
        )

    def _recognize(
        self,
        source_path: str,
        parameters: _PdfOcrParameters,
    ) -> tuple[list[PdfOcrLine], list[int]]:
        deadline = time.monotonic() + self._timeout_seconds
        with tempfile.TemporaryDirectory(prefix="foundry-pdf-ocr-") as output_dir:
            pages = self._rasterizer(
                source_path,
                output_dir,
                parameters.max_pages,
                parameters.selection,
                parameters.dpi,
                _remaining_seconds(deadline),
            )
            lines = self._recognize_pages(pages, parameters.languages, deadline)
        return lines, [page.page_number for page in pages]

    def _recognize_pages(
        self,
        pages: Sequence[RasterizedPdfPage],
        languages: str,
        deadline: float,
    ) -> list[PdfOcrLine]:
        lines: list[PdfOcrLine] = []
        for page in pages:
            lines.extend(self._ocr_engine(page, languages, _remaining_seconds(deadline)))
        return lines

    def _error(
        self,
        kind: AdapterFailureKind,
        reason: str,
        request: MediaProcessingRequest,
        is_retryable: bool,
        *,
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
                kind,
                is_retryable,
                f"PDF OCR processing failed: {reason}",
                timeout_seconds=self._timeout_seconds if kind == "timeout" else None,
                details=details,
            )
        )


@dataclass(frozen=True)
class _PdfOcrParameters:
    max_pages: int
    selection: PdfPageSelection
    dpi: int
    languages: str


def _normalized_parameters(request: MediaProcessingRequest) -> _PdfOcrParameters:
    parameters = request.spec.parameters
    try:
        selection = pdf_page_selection(parameters)
    except PdfPageSelectionError as exc:
        raise PdfOcrDocumentError(str(exc)) from exc
    if selection.limit is not None and selection.limit > _MAX_PAGES:
        raise PdfOcrDocumentError("page_selection_limit_exceeded")
    return _PdfOcrParameters(
        max_pages=_max_pages(parameters.get("maxPages")),
        selection=selection,
        dpi=_bounded_int(parameters.get("dpi"), _DEFAULT_DPI, _MIN_DPI, _MAX_DPI),
        languages=_ocr_languages(parameters.get("languages")),
    )


def _poppler_rasterize(
    source_path: str,
    output_dir: str,
    max_pages: int,
    selection: PdfPageSelection,
    dpi: int,
    timeout_seconds: int,
) -> Sequence[RasterizedPdfPage]:
    deadline = time.monotonic() + timeout_seconds
    page_numbers = _selected_pdf_page_numbers(
        source_path,
        max_pages,
        selection,
        _remaining_seconds(deadline),
    )
    if not page_numbers:
        return ()
    executable = shutil.which("pdftoppm")
    if executable is None:
        raise PdfOcrDocumentError("pdftoppm_unavailable", kind="unavailable")
    prefix = str(Path(output_dir) / "page")
    command = _pdftoppm_command(executable, source_path, prefix, page_numbers, dpi)
    _run_pdftoppm(command, _remaining_seconds(deadline))
    return _rasterized_pages(Path(output_dir), page_numbers)


def _selected_pdf_page_numbers(
    source_path: str,
    max_pages: int,
    selection: PdfPageSelection,
    timeout_seconds: int,
) -> list[int]:
    page_count, is_encrypted = _pdf_page_metadata(source_path, timeout_seconds)
    if is_encrypted:
        raise PdfOcrDocumentError("encrypted_pdf")
    if page_count > max_pages:
        raise PdfOcrDocumentError("page_limit_exceeded")
    return [index + 1 for index in selected_page_indexes(page_count, selection)]


def _pdf_page_metadata(source_path: str, timeout_seconds: int) -> tuple[int, bool]:
    executable = shutil.which("pdfinfo")
    if executable is None:
        raise PdfOcrDocumentError("pdfinfo_unavailable", kind="unavailable")
    try:
        result = subprocess.run(  # nosec B603 - fixed Poppler binary and one sandbox-owned source path.
            [executable, source_path],
            check=False,
            capture_output=True,
            text=True,
            timeout=max(timeout_seconds, 1),
        )
    except subprocess.TimeoutExpired as exc:
        raise PdfOcrDocumentError(
            "pdf_page_discovery_timeout",
            kind="timeout",
            is_retryable=True,
        ) from exc
    except OSError as exc:
        raise PdfOcrDocumentError("pdfinfo_unavailable", kind="unavailable") from exc
    if result.returncode != 0:
        if "password" in result.stderr.lower():
            raise PdfOcrDocumentError("encrypted_pdf")
        raise PdfOcrDocumentError("corrupt_pdf")
    metadata = _pdfinfo_fields(result.stdout)
    pages = metadata.get("Pages")
    if pages is None or not pages.isdigit() or int(pages) < 1:
        raise PdfOcrDocumentError("corrupt_pdf")
    return int(pages), metadata.get("Encrypted", "no").strip().lower() != "no"


def _pdfinfo_fields(output: str) -> dict[str, str]:
    return {
        key.strip(): value.strip() for line in output.splitlines() if ":" in line for key, value in [line.split(":", 1)]
    }


def _pdftoppm_command(
    executable: str,
    source_path: str,
    prefix: str,
    page_numbers: Sequence[int],
    dpi: int,
) -> list[str]:
    return [
        executable,
        "-f",
        str(page_numbers[0]),
        "-l",
        str(page_numbers[-1]),
        "-r",
        str(dpi),
        "-png",
        source_path,
        prefix,
    ]


def _run_pdftoppm(command: Sequence[str], timeout_seconds: int) -> None:
    try:
        result = subprocess.run(  # nosec B603 - fixed binary and option set; no shell
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise PdfOcrDocumentError("pdf_rasterization_timeout", kind="timeout", is_retryable=True) from exc
    except OSError as exc:
        raise PdfOcrDocumentError("pdftoppm_unavailable", kind="unavailable") from exc
    if result.returncode != 0:
        raise PdfOcrDocumentError("pdf_rasterization_failed")


def _rasterized_pages(
    output_dir: Path,
    expected_page_numbers: Sequence[int],
) -> tuple[RasterizedPdfPage, ...]:
    by_page = {_page_number(path): path for path in output_dir.glob("page-*.png")}
    missing = [page for page in expected_page_numbers if page not in by_page]
    if missing:
        raise PdfOcrDocumentError("pdf_rasterization_incomplete", page=missing[0])
    pil_image = import_module("PIL.Image")
    pages: list[RasterizedPdfPage] = []
    for page_number in expected_page_numbers:
        path = by_page[page_number]
        with pil_image.open(path) as image:
            width, height = image.size
        pages.append(RasterizedPdfPage(page_number, str(path), int(width), int(height)))
    return tuple(pages)


def _page_number(path: Path) -> int:
    match = _PAGE_FILE_PATTERN.search(path.name)
    if match is None:
        raise PdfOcrDocumentError("pdf_rasterization_filename_invalid")
    return int(match.group(1))


def _tesseract_page_lines(
    page: RasterizedPdfPage,
    languages: str,
    timeout_seconds: int,
) -> Sequence[PdfOcrLine]:
    pytesseract = import_module("pytesseract")
    pil_image = import_module("PIL.Image")
    try:
        with pil_image.open(page.image_path) as image:
            data = pytesseract.image_to_data(
                image,
                lang=languages,
                output_type=pytesseract.Output.DICT,
                timeout=timeout_seconds,
            )
    except Exception as exc:  # noqa: BLE001 - wrapper types vary by pytesseract release
        raise _tesseract_error(exc, page.page_number) from exc
    return _lines_from_tesseract_data(data, page)


def _tesseract_error(exc: Exception, page_number: int) -> PdfOcrDocumentError:
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if "timeout" in name or "timeout" in message:
        return PdfOcrDocumentError("ocr_timeout", kind="timeout", is_retryable=True, page=page_number)
    if "notfound" in name or "not found" in message:
        return PdfOcrDocumentError("tesseract_unavailable", kind="unavailable", page=page_number)
    return PdfOcrDocumentError("ocr_failed", page=page_number)


def _lines_from_tesseract_data(
    raw: object,
    page: RasterizedPdfPage,
) -> tuple[PdfOcrLine, ...]:
    if not isinstance(raw, Mapping):
        raise PdfOcrDocumentError("ocr_output_invalid", page=page.page_number)
    groups: dict[tuple[int, int, int], list[tuple[str, int, int, int, int, float]]] = {}
    texts = _data_column(raw, "text")
    for index, value in enumerate(texts):
        text = str(value).strip()
        if not text:
            continue
        key = (
            _column_int(raw, "block_num", index),
            _column_int(raw, "par_num", index),
            _column_int(raw, "line_num", index),
        )
        groups.setdefault(key, []).append(
            (
                text,
                _column_int(raw, "left", index),
                _column_int(raw, "top", index),
                _column_int(raw, "width", index),
                _column_int(raw, "height", index),
                _column_float(raw, "conf", index),
            )
        )
    lines = [_line_from_words(words, page) for words in groups.values()]
    return tuple(sorted(lines, key=lambda item: (item.y, item.x, item.text)))


def _line_from_words(
    words: Sequence[tuple[str, int, int, int, int, float]],
    page: RasterizedPdfPage,
) -> PdfOcrLine:
    left = min(word[1] for word in words)
    top = min(word[2] for word in words)
    right = max(word[1] + word[3] for word in words)
    bottom = max(word[2] + word[4] for word in words)
    confidences = [word[5] for word in words if word[5] >= 0]
    confidence = sum(confidences) / len(confidences) / 100.0 if confidences else 0.0
    return PdfOcrLine(
        page.page_number,
        " ".join(word[0] for word in words),
        left,
        top,
        max(right - left, 1),
        max(bottom - top, 1),
        page.width,
        page.height,
        round(max(0.0, min(confidence, 1.0)), 6),
    )


def _data_column(raw: Mapping[object, object], name: str) -> Sequence[object]:
    value = raw.get(name)
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise PdfOcrDocumentError("ocr_output_invalid")
    return value


def _column_int(raw: Mapping[object, object], name: str, index: int) -> int:
    column = _data_column(raw, name)
    try:
        value = column[index]
    except IndexError as exc:
        raise PdfOcrDocumentError("ocr_output_invalid") from exc
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise PdfOcrDocumentError("ocr_output_invalid")
    try:
        return int(value)
    except ValueError as exc:
        raise PdfOcrDocumentError("ocr_output_invalid") from exc


def _column_float(raw: Mapping[object, object], name: str, index: int) -> float:
    column = _data_column(raw, name)
    try:
        value = column[index]
    except IndexError as exc:
        raise PdfOcrDocumentError("ocr_output_invalid") from exc
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise PdfOcrDocumentError("ocr_output_invalid")
    try:
        return float(value)
    except ValueError as exc:
        raise PdfOcrDocumentError("ocr_output_invalid") from exc


def _ocr_units(lines: Sequence[PdfOcrLine]) -> tuple[ProcessedContentUnit, ...]:
    page_metrics = _page_line_metrics(lines)
    return tuple(
        _ocr_unit(line, ordinal, page_metrics[line.page_number])
        for ordinal, line in enumerate(sorted(lines, key=lambda item: (item.page_number, item.y, item.x)))
    )


def _page_line_metrics(lines: Sequence[PdfOcrLine]) -> dict[int, tuple[float, int]]:
    grouped: dict[int, list[int]] = {}
    for line in lines:
        grouped.setdefault(line.page_number, []).append(line.height)
    return {page: (_body_line_height(heights), max(heights)) for page, heights in grouped.items()}


def _body_line_height(heights: Sequence[int]) -> float:
    ordered = sorted(heights)
    body_sample = ordered[: max(1, ceil(len(ordered) * 0.6))]
    return float(median(body_sample))


def _ocr_unit(
    line: PdfOcrLine,
    ordinal: int,
    line_metrics: tuple[float, int],
) -> ProcessedContentUnit:
    bbox = {
        "x": line.x,
        "y": line.y,
        "width": line.width,
        "height": line.height,
        "pageWidth": line.page_width,
        "pageHeight": line.page_height,
        "unit": "px",
    }
    role, role_confidence = _structure_role(line, *line_metrics)
    confidence = round((line.confidence + role_confidence) / 2.0, 6)
    return ProcessedContentUnit(
        unit_kind="ocr_line",
        ordinal=ordinal,
        page_number=line.page_number,
        text=line.text,
        text_hash=hashlib.sha256(line.text.encode("utf-8")).hexdigest(),
        bbox=bbox,
        source_locator={
            "pageNumber": line.page_number,
            "bbox": bbox,
            "coordinateSystem": "image_top_left_pixels",
        },
        structure={
            "role": role,
            "classificationMethod": "tesseract_line_height_position_heuristic_v1",
            "isHeuristic": True,
            "ocrConfidence": line.confidence,
        },
        confidence=confidence,
    )


def _structure_role(
    line: PdfOcrLine,
    body_height: float,
    maximum_height: int,
) -> tuple[str, float]:
    near_top = line.y <= line.page_height * 0.3
    if near_top and line.height >= max(body_height * 1.6, maximum_height * 0.9, 24):
        return "title", 0.78
    if line.height >= max(body_height * 1.3, maximum_height * 0.55):
        return "heading_1", 0.72
    if line.height >= body_height * 1.1:
        return "heading_2", 0.66
    return "body", 0.62


def _content_hash(units: Sequence[ProcessedContentUnit]) -> str:
    payload = [
        {
            "textHash": unit.text_hash,
            "pageNumber": unit.page_number,
            "bbox": dict(unit.bbox or {}),
            "structure": dict(unit.structure or {}),
            "confidence": unit.confidence,
        }
        for unit in units
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _max_pages(value: object) -> int:
    if value is None:
        return _DEFAULT_MAX_PAGES
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= _MAX_PAGES:
        raise PdfOcrDocumentError("max_pages_out_of_range")
    return value


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return max(minimum, min(value, maximum))


def _ocr_languages(value: object) -> str:
    if value is None:
        return "eng"
    if not isinstance(value, str) or not _LANGUAGES_PATTERN.fullmatch(value):
        raise PdfOcrDocumentError("ocr_languages_invalid")
    return value


def _remaining_seconds(deadline: float) -> int:
    remaining = int(deadline - time.monotonic())
    if remaining < 1:
        raise PdfOcrDocumentError("pdf_ocr_timeout", kind="timeout", is_retryable=True)
    return remaining


@lru_cache(maxsize=1)
def pdf_ocr_model_version() -> str:
    """Return the exact local OCR toolchain identity advertised by the registry."""

    return f"tesseract:{_tool_version('tesseract')}|poppler:{_tool_version('pdftoppm')}"


def _tool_version(executable_name: str) -> str:
    executable = shutil.which(executable_name)
    if executable is None:
        return "unavailable"
    version_flag = "-v" if executable_name == "pdftoppm" else "--version"
    try:
        result = subprocess.run(  # nosec B603 - fixed version command; no shell
            [executable, version_flag],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    first_line = (result.stdout or result.stderr).splitlines()
    return first_line[0].strip() if first_line else "unknown"
