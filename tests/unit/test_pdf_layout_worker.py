from __future__ import annotations

import io
import json
from collections.abc import Callable

import pytest
from foundry_lite.infrastructure.adapters import pdf_layout_processor, pdf_layout_worker
from foundry_lite.infrastructure.adapters.pdf_layout_processor import PdfLayoutFragment
from foundry_lite.infrastructure.adapters.pdf_page_selection import PdfPageSelection
from foundry_lite.infrastructure.adapters.pdf_text_processor import PdfDocumentError

WorkerExtractor = Callable[[str, int, PdfPageSelection], list[PdfLayoutFragment]]


def _run_worker(
    monkeypatch: pytest.MonkeyPatch,
    extractor: WorkerExtractor,
    *,
    selection_limit: int | None,
) -> dict[str, object]:
    stdin = io.StringIO(
        json.dumps(
            {
                "sourcePath": "/sandbox/layout.pdf",
                "maxPages": 20,
                "selection": {"start": 2, "limit": selection_limit},
            }
        )
    )
    stdout = io.StringIO()
    monkeypatch.setattr(pdf_layout_processor, "_pypdf_layout_extract", extractor)
    monkeypatch.setattr(pdf_layout_worker.sys, "stdin", stdin)
    monkeypatch.setattr(pdf_layout_worker.sys, "stdout", stdout)

    pdf_layout_worker.main()

    payload = json.loads(stdout.getvalue())
    assert isinstance(payload, dict)
    return payload


def test_layout_worker_serializes_successful_fragments(monkeypatch: pytest.MonkeyPatch) -> None:
    def _extract(source_path: str, max_pages: int, selection: PdfPageSelection) -> list[PdfLayoutFragment]:
        assert source_path == "/sandbox/layout.pdf"
        assert max_pages == 20
        assert selection == PdfPageSelection(start=2, limit=None)
        return [PdfLayoutFragment(2, "Revenue", 12.0, 700.0, 11.0, "Helvetica", 612.0, 792.0)]

    payload = _run_worker(monkeypatch, _extract, selection_limit=None)

    assert payload == {
        "kind": "ok",
        "fragments": [
            {
                "page_number": 2,
                "text": "Revenue",
                "x": 12.0,
                "baseline_y": 700.0,
                "font_size": 11.0,
                "font_name": "Helvetica",
                "page_width": 612.0,
                "page_height": 792.0,
            }
        ],
    }


def test_layout_worker_preserves_typed_document_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _extract(_source_path: str, _max_pages: int, selection: PdfPageSelection) -> list[PdfLayoutFragment]:
        assert selection == PdfPageSelection(start=2, limit=3)
        raise PdfDocumentError("page_limit_exceeded", page=4)

    payload = _run_worker(monkeypatch, _extract, selection_limit=3)

    assert payload == {"kind": "document_error", "reason": "page_limit_exceeded", "page": 4}


def test_layout_worker_normalizes_unexpected_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def _extract(_source_path: str, _max_pages: int, _selection: PdfPageSelection) -> list[PdfLayoutFragment]:
        raise RuntimeError("sensitive parser detail")

    payload = _run_worker(monkeypatch, _extract, selection_limit=1)

    assert payload == {"kind": "document_error", "reason": "corrupt_pdf", "page": None}
