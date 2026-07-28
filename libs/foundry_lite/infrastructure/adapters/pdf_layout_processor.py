"""Deterministic PDF layout-block extraction backed by pypdf visitors."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess  # nosec B404 - adapter-owned argv, no shell; remove if construction becomes user-controlled.
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from statistics import median

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
from foundry_lite.infrastructure.adapters.pdf_text_processor import PdfDocumentError

_DEFAULT_MAX_PAGES = 5000
_DEFAULT_MAX_RESULT_BYTES = 8 * 1024 * 1024
_DEFAULT_TIMEOUT_SECONDS = 30
_DERIVATIVE_KIND = "pdf_layout"
_TABLE_PATTERN = re.compile(r"(?:\S+\s{2,}){2,}\S+")


@dataclass(frozen=True)
class PdfLayoutFragment:
    """One positioned text fragment before semantic-role heuristics are applied."""

    page_number: int
    text: str
    x: float
    baseline_y: float
    font_size: float
    font_name: str
    page_width: float
    page_height: float


PageLayoutExtractor = Callable[[str, int, PdfPageSelection], Sequence[PdfLayoutFragment]]


def _pypdf_layout_extract(
    source_path: str,
    max_pages: int,
    selection: PdfPageSelection,
) -> list[PdfLayoutFragment]:
    pypdf = import_module("pypdf")
    try:
        reader = pypdf.PdfReader(source_path)
    except Exception as exc:  # noqa: BLE001 - normalized into a typed corrupt-PDF failure
        raise PdfDocumentError("corrupt_pdf") from exc
    if reader.is_encrypted:
        raise PdfDocumentError("encrypted_pdf")
    if len(reader.pages) > max_pages:
        raise PdfDocumentError("page_limit_exceeded")
    fragments: list[PdfLayoutFragment] = []
    for page_index in selected_page_indexes(len(reader.pages), selection):
        fragments.extend(_page_fragments(reader.pages[page_index], page_index + 1))
    return fragments


def _page_fragments(page: object, page_number: int) -> list[PdfLayoutFragment]:
    width = float(page.mediabox.width)  # type: ignore[attr-defined]
    height = float(page.mediabox.height)  # type: ignore[attr-defined]
    fragments: list[PdfLayoutFragment] = []

    def visitor(text: object, _cm: object, tm: object, font: object, size: object) -> None:
        if not isinstance(text, str) or not text.strip():
            return
        coordinates = list(tm) if isinstance(tm, Sequence) else []
        x = float(coordinates[4]) if len(coordinates) > 5 else 0.0
        y = float(coordinates[5]) if len(coordinates) > 5 else 0.0
        font_name = _font_name(font)
        normalized = text.strip()
        if normalized:
            fragments.append(
                PdfLayoutFragment(
                    page_number,
                    normalized,
                    x,
                    y,
                    _positive_float(size, 10.0),
                    font_name,
                    width,
                    height,
                )
            )

    page.extract_text(visitor_text=visitor)  # type: ignore[attr-defined]
    return fragments


def _font_name(font: object) -> str:
    if not isinstance(font, dict):
        return "unknown"
    value = font.get("/BaseFont") or font.get("BaseFont") or "unknown"
    return str(value)


def _fragments_from_payload(payload: object) -> list[PdfLayoutFragment]:
    if not isinstance(payload, list):
        raise ValueError("layout worker fragments must be a list")
    fragments: list[PdfLayoutFragment] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("layout worker fragment must be an object")
        fragments.append(
            PdfLayoutFragment(
                page_number=int(item["page_number"]),
                text=str(item["text"]),
                x=float(item["x"]),
                baseline_y=float(item["baseline_y"]),
                font_size=float(item["font_size"]),
                font_name=str(item["font_name"]),
                page_width=float(item["page_width"]),
                page_height=float(item["page_height"]),
            )
        )
    return fragments


class _PdfLayoutWorkerTimeout(Exception):
    """The isolated layout worker exceeded its wall-clock budget."""


class _PdfLayoutWorkerOutputLimit(Exception):
    """The isolated layout worker exceeded its response byte budget."""


def _read_bounded_worker_output(
    process: subprocess.Popen[bytes],
    worker_input: bytes,
    *,
    timeout_seconds: int,
    max_result_bytes: int,
) -> bytes:
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("layout worker pipes are unavailable")
    deadline = time.monotonic() + timeout_seconds
    process.stdin.write(worker_input)
    process.stdin.close()
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(process.stdout.read, max_result_bytes + 1)
    try:
        output = future.result(timeout=max(deadline - time.monotonic(), 0.0))
    except FuturesTimeoutError as exc:
        _terminate_worker(process)
        future.result()
        raise _PdfLayoutWorkerTimeout from exc
    finally:
        pool.shutdown(wait=True)
    if len(output) > max_result_bytes:
        _terminate_worker(process)
        raise _PdfLayoutWorkerOutputLimit
    try:
        process.wait(timeout=max(deadline - time.monotonic(), 0.0))
    except subprocess.TimeoutExpired as exc:
        _terminate_worker(process)
        raise _PdfLayoutWorkerTimeout from exc
    return output


def _terminate_worker(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait()


class PdfLayoutProcessorAdapter:
    """Extract positioned PDF blocks with explicitly heuristic structure labels."""

    profile_name = "pdf-layout-pypdf"

    def __init__(
        self,
        *,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
        max_result_bytes: int = _DEFAULT_MAX_RESULT_BYTES,
        layout_extractor: PageLayoutExtractor | None = None,
        should_isolate_extractor: bool | None = None,
        worker_command: Sequence[str] | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_result_bytes = max(1, max_result_bytes)
        self._layout_extractor = layout_extractor or _pypdf_layout_extract
        self._should_isolate_extractor = (
            layout_extractor is None if should_isolate_extractor is None else should_isolate_extractor
        )
        self._worker_command = tuple(
            worker_command
            or (
                sys.executable,
                str(Path(__file__).with_name("pdf_layout_worker.py")),
            )
        )

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "process",
                    "validation",
                    False,
                    "PDF is encrypted, corrupt, or exceeds the page limit; layout extraction stopped.",
                ),
                AdapterFailureMode(
                    "process",
                    "timeout",
                    True,
                    "PDF layout extraction exceeded its bounded time budget.",
                    timeout_seconds=_DEFAULT_TIMEOUT_SECONDS,
                ),
                AdapterFailureMode(
                    "process",
                    "validation",
                    False,
                    "PDF layout extraction exceeded its bounded response size.",
                ),
            ),
        )

    def supports(self, request: MediaProcessingRequest) -> bool:
        return request.spec.processor == "pdf_layout_v1"

    def process(self, request: MediaProcessingRequest) -> MediaProcessingResult:
        if request.source_path is None:
            raise self._error("validation", "layout processor requires a sandbox source_path", request, False)
        max_pages = _coerce_int(request.spec.parameters.get("maxPages"), _DEFAULT_MAX_PAGES)
        try:
            selection = pdf_page_selection(request.spec.parameters)
        except PdfPageSelectionError as exc:
            raise self._error("validation", str(exc), request, False) from exc
        fragments = self._extract_within_timeout(request, max_pages, selection)
        units = _layout_units(fragments)
        return MediaProcessingResult(
            media_item_version_id=request.media_item_version_id,
            processing_spec_hash=request.processing_spec_hash,
            derivative_kind=_DERIVATIVE_KIND,
            content_hash=_content_hash(units),
            mime_type="application/json",
            units=units,
        )

    def _extract_within_timeout(
        self,
        request: MediaProcessingRequest,
        max_pages: int,
        selection: PdfPageSelection,
    ) -> Sequence[PdfLayoutFragment]:
        if self._should_isolate_extractor:
            return self._extract_in_subprocess(request, max_pages, selection)
        return self._extract_in_thread(request, max_pages, selection)

    def _extract_in_thread(
        self,
        request: MediaProcessingRequest,
        max_pages: int,
        selection: PdfPageSelection,
    ) -> Sequence[PdfLayoutFragment]:
        assert request.source_path is not None
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(self._layout_extractor, request.source_path, max_pages, selection)
        try:
            result = future.result(timeout=self._timeout_seconds)
            pool.shutdown(wait=True)
            return result
        except FuturesTimeoutError as exc:
            pool.shutdown(wait=False)
            raise self._error("timeout", "pdf layout extraction timed out", request, True) from exc
        except PdfDocumentError as exc:
            pool.shutdown(wait=False)
            raise self._error("validation", exc.reason, request, False, page=exc.page) from exc

    def _extract_in_subprocess(
        self,
        request: MediaProcessingRequest,
        max_pages: int,
        selection: PdfPageSelection,
    ) -> Sequence[PdfLayoutFragment]:
        assert request.source_path is not None
        process = subprocess.Popen(  # nosec B603 - adapter-owned argv, no shell; only tests replace the command.
            self._worker_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        worker_input = json.dumps(
            {
                "sourcePath": request.source_path,
                "maxPages": max_pages,
                "selection": {"start": selection.start, "limit": selection.limit},
            }
        ).encode("utf-8")
        try:
            stdout = _read_bounded_worker_output(
                process,
                worker_input,
                timeout_seconds=self._timeout_seconds,
                max_result_bytes=self._max_result_bytes,
            )
        except _PdfLayoutWorkerTimeout as exc:
            raise self._error("timeout", "pdf layout extraction timed out", request, True) from exc
        except _PdfLayoutWorkerOutputLimit as exc:
            raise self._error("validation", "output_limit", request, False) from exc
        return self._subprocess_result(stdout.decode("utf-8"), process.returncode, request)

    def _subprocess_result(
        self,
        stdout: str,
        return_code: int,
        request: MediaProcessingRequest,
    ) -> Sequence[PdfLayoutFragment]:
        try:
            payload = json.loads(stdout)
            if return_code != 0 or not isinstance(payload, dict):
                raise ValueError("layout worker failed")
            if payload.get("kind") == "document_error":
                page = payload.get("page")
                page_number = int(page) if isinstance(page, int) else None
                raise self._error("validation", str(payload.get("reason")), request, False, page=page_number)
            if payload.get("kind") != "ok":
                raise ValueError("layout worker returned an invalid result")
            return _fragments_from_payload(payload.get("fragments"))
        except AdapterError:
            raise
        except Exception as exc:
            raise self._error("validation", "corrupt_pdf", request, False) from exc

    def _error(
        self,
        kind: str,
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
        if reason == "output_limit":
            details["maxResultBytes"] = self._max_result_bytes
        return AdapterError(
            AdapterFailure(
                self.profile_name,
                "process",
                kind,  # type: ignore[arg-type]
                is_retryable,
                f"PDF layout processing failed: {reason}",
                timeout_seconds=_DEFAULT_TIMEOUT_SECONDS if kind == "timeout" else None,
                details=details,
            )
        )


def _layout_units(fragments: Sequence[PdfLayoutFragment]) -> tuple[ProcessedContentUnit, ...]:
    ordered = sorted(fragments, key=lambda item: (item.page_number, -item.baseline_y, item.x, item.text))
    page_sizes = _page_median_font_sizes(ordered)
    return tuple(
        _layout_unit(fragment, ordinal, page_sizes[fragment.page_number]) for ordinal, fragment in enumerate(ordered)
    )


def _page_median_font_sizes(fragments: Sequence[PdfLayoutFragment]) -> dict[int, float]:
    grouped: dict[int, list[float]] = {}
    for fragment in fragments:
        grouped.setdefault(fragment.page_number, []).append(fragment.font_size)
    return {page_number: float(median(values)) for page_number, values in grouped.items()}


def _layout_unit(fragment: PdfLayoutFragment, ordinal: int, median_size: float) -> ProcessedContentUnit:
    bbox = _bbox(fragment)
    role, confidence = _structure_role(fragment, median_size)
    structure = {
        "role": role,
        "classificationMethod": "font_position_text_pattern_heuristic_v1",
        "isHeuristic": True,
        "fontSize": round(fragment.font_size, 3),
        "fontName": fragment.font_name,
    }
    locator = {"pageNumber": fragment.page_number, "bbox": bbox, "coordinateSystem": "pdf_top_left_points"}
    return ProcessedContentUnit(
        unit_kind="layout_block",
        ordinal=ordinal,
        page_number=fragment.page_number,
        text=fragment.text,
        text_hash=hashlib.sha256(fragment.text.encode("utf-8")).hexdigest(),
        bbox=bbox,
        source_locator=locator,
        structure=structure,
        confidence=confidence,
    )


def _bbox(fragment: PdfLayoutFragment) -> dict[str, object]:
    longest_line = max((len(line) for line in fragment.text.splitlines()), default=0)
    width = min(max(longest_line * fragment.font_size * 0.5, 1.0), max(fragment.page_width - fragment.x, 1.0))
    height = _fragment_height(fragment)
    top = _fragment_top(fragment)
    return {
        "x": round(max(fragment.x, 0.0), 3),
        "y": round(top, 3),
        "width": round(width, 3),
        "height": round(height, 3),
        "pageWidth": round(fragment.page_width, 3),
        "pageHeight": round(fragment.page_height, 3),
        "unit": "pt",
    }


def _structure_role(
    fragment: PdfLayoutFragment,
    median_size: float,
) -> tuple[str, float]:
    normalized = fragment.text.strip()
    special_role = _special_structure_role(normalized)
    if special_role is not None:
        return special_role
    return _typographic_structure_role(fragment, median_size)


def _special_structure_role(normalized: str) -> tuple[str, float] | None:
    lowered = normalized.lower()
    if lowered.startswith(("figure ", "fig. ")):
        return "figure", 0.62
    if lowered.startswith("table ") or "|" in normalized or _TABLE_PATTERN.fullmatch(normalized):
        return "table", 0.58
    return None


def _typographic_structure_role(
    fragment: PdfLayoutFragment,
    median_size: float,
) -> tuple[str, float]:
    is_bold = "bold" in fragment.font_name.lower()
    near_top = _fragment_top(fragment) <= fragment.page_height * 0.3
    if near_top and fragment.font_size >= max(median_size * 1.6, 18.0):
        return "title", 0.84
    if fragment.font_size >= median_size * 1.35 or (is_bold and fragment.font_size >= median_size * 1.2):
        return "heading_1", 0.79
    if fragment.font_size >= median_size * 1.15 or is_bold:
        return "heading_2", 0.72
    return "body", 0.66


def _fragment_height(fragment: PdfLayoutFragment) -> float:
    return _fragment_line_height(fragment) * max(len(fragment.text.splitlines()), 1)


def _fragment_top(fragment: PdfLayoutFragment) -> float:
    return max(fragment.page_height - fragment.baseline_y - _fragment_line_height(fragment), 0.0)


def _fragment_line_height(fragment: PdfLayoutFragment) -> float:
    return max(fragment.font_size * 1.2, 1.0)


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
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _coerce_int(value: object, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default


def _positive_float(value: object, default: float) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool) and float(value) > 0:
        return float(value)
    return default
