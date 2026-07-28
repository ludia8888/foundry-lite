from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.media_processor import MediaProcessingRequest, ProcessorSpec
from foundry_lite.infrastructure.adapters.pdf_layout_processor import (
    PdfLayoutFragment,
    PdfLayoutProcessorAdapter,
    _pypdf_layout_extract,
)
from foundry_lite.infrastructure.adapters.pdf_page_selection import PdfPageSelection
from foundry_lite.infrastructure.adapters.pdf_text_processor import PdfDocumentError


def _layout_pdf() -> bytes:
    content = b"\n".join(
        (
            b"BT /F1 24 Tf 72 744 Td (Annual Report) Tj ET",
            b"BT /F2 16 Tf 72 690 Td (Financial Overview) Tj ET",
            b"BT /F2 13 Tf 72 650 Td (Revenue Detail) Tj ET",
            b"BT /F1 10 Tf 72 610 Td (Revenue increased during the quarter.) Tj ET",
            b"BT /F1 10 Tf 72 570 Td (Table 1 Revenue by region) Tj ET",
            b"BT /F1 10 Tf 72 530 Td (Figure 1 Growth trend) Tj ET",
        )
    )
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R/F2 6 0 R>>>>>>",
        b"<</Length %d>>stream\n" % len(content) + content + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica-Bold>>",
    ]
    output = b"%PDF-1.4\n"
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{number} 0 obj".encode() + body + b"endobj\n"
    xref = len(output)
    output += b"xref\n0 7\n0000000000 65535 f \n"
    for offset in offsets:
        output += b"%010d 00000 n \n" % offset
    return output + b"trailer<</Size 7/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % xref


def _request(source_path: str) -> MediaProcessingRequest:
    return MediaProcessingRequest(
        tenant_id="tenant-demo",
        media_item_version_id="miv-layout-1",
        blob_key="blob-layout-1",
        spec=ProcessorSpec("pdf_layout_v1", "1", parameters={"maxPages": 3}),
        processing_spec_hash="layout-spec-1",
        source_path=source_path,
        source_format="pdf",
        source_mime_type="application/pdf",
    )


def test_generated_pdf_yields_deterministic_structured_blocks_with_coordinates(tmp_path: Path) -> None:
    source = tmp_path / "layout.pdf"
    source.write_bytes(_layout_pdf())
    adapter = PdfLayoutProcessorAdapter()

    first = adapter.process(_request(str(source)))
    second = adapter.process(_request(str(source)))

    roles = [str(unit.structure["role"]) for unit in first.units if unit.structure is not None]
    assert first.derivative_kind == "pdf_layout"
    assert first.mime_type == "application/json"
    assert roles == ["title", "heading_1", "heading_2", "body", "table", "figure"]
    assert first.content_hash == second.content_hash
    assert [unit.text_hash for unit in first.units] == [unit.text_hash for unit in second.units]
    for unit in first.units:
        assert unit.page_number == 1
        assert unit.bbox is not None
        assert unit.source_locator == {
            "pageNumber": 1,
            "bbox": unit.bbox,
            "coordinateSystem": "pdf_top_left_points",
        }
        assert unit.structure is not None and unit.structure["isHeuristic"] is True
        assert unit.confidence is not None and 0 < unit.confidence < 1


def test_direct_pypdf_extractor_preserves_position_and_font_evidence(tmp_path: Path) -> None:
    source = tmp_path / "layout.pdf"
    source.write_bytes(_layout_pdf())

    fragments = _pypdf_layout_extract(str(source), 3, PdfPageSelection(start=1, limit=None))

    assert [fragment.text for fragment in fragments] == [
        "Annual Report",
        "Financial Overview",
        "Revenue Detail",
        "Revenue increased during the quarter.",
        "Table 1 Revenue by region",
        "Figure 1 Growth trend",
    ]
    assert all(fragment.page_number == 1 for fragment in fragments)
    assert [(fragment.x, fragment.baseline_y) for fragment in fragments] == [
        (72.0, 744.0),
        (72.0, 690.0),
        (72.0, 650.0),
        (72.0, 610.0),
        (72.0, 570.0),
        (72.0, 530.0),
    ]
    assert fragments[0].font_name.endswith("Helvetica")
    assert fragments[1].font_name.endswith("Helvetica-Bold")


def test_direct_pypdf_extractor_normalizes_corrupt_and_page_limit_failures(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"not-a-pdf")

    with pytest.raises(PdfDocumentError, match="corrupt_pdf"):
        _pypdf_layout_extract(str(corrupt), 3, PdfPageSelection(start=1, limit=None))

    valid = tmp_path / "layout.pdf"
    valid.write_bytes(_layout_pdf())
    with pytest.raises(PdfDocumentError, match="page_limit_exceeded"):
        _pypdf_layout_extract(str(valid), 0, PdfPageSelection(start=1, limit=None))


def test_in_process_extractor_success_and_document_error_keep_typed_evidence() -> None:
    fragment = PdfLayoutFragment(1, "Title", 10.0, 90.0, 20.0, "Bold", 100.0, 100.0)
    adapter = PdfLayoutProcessorAdapter(
        layout_extractor=lambda _path, _max_pages, _selection: [fragment],
        should_isolate_extractor=False,
    )

    result = adapter.process(_request("/sandbox/layout.pdf"))

    assert [unit.text for unit in result.units] == ["Title"]

    def _document_error(_path: str, _max_pages: int, _selection: PdfPageSelection) -> list[PdfLayoutFragment]:
        raise PdfDocumentError("corrupt_pdf", page=2)

    failing_adapter = PdfLayoutProcessorAdapter(
        layout_extractor=_document_error,
        should_isolate_extractor=False,
    )
    with pytest.raises(AdapterError) as captured:
        failing_adapter.process(_request("/sandbox/layout.pdf"))

    assert captured.value.failure.kind == "validation"
    assert captured.value.failure.details["reason"] == "corrupt_pdf"
    assert captured.value.failure.details["page"] == 2


def test_in_process_extractor_timeout_fails_closed_without_waiting_for_worker() -> None:
    release = threading.Event()

    def _blocking(_path: str, _max_pages: int, _selection: PdfPageSelection) -> list[PdfLayoutFragment]:
        release.wait(timeout=10)
        return []

    adapter = PdfLayoutProcessorAdapter(
        timeout_seconds=0,
        layout_extractor=_blocking,
        should_isolate_extractor=False,
    )
    try:
        with pytest.raises(AdapterError) as captured:
            adapter.process(_request("/sandbox/layout.pdf"))
        assert captured.value.failure.kind == "timeout"
        assert captured.value.failure.is_retryable is True
    finally:
        release.set()


def test_layout_processor_supports_only_its_pinned_processor_family() -> None:
    adapter = PdfLayoutProcessorAdapter()
    assert adapter.supports(_request("source.pdf")) is True
    other = ProcessorSpec("pdf_text_v1", "1")
    request = MediaProcessingRequest("t", "v", "b", other, "h", source_path="source.pdf")
    assert adapter.supports(request) is False


def test_layout_timeout_terminates_the_isolated_extractor_process(tmp_path: Path) -> None:
    source = tmp_path / "layout.pdf"
    source.write_bytes(_layout_pdf())
    pid_file = tmp_path / "worker.pid"
    adapter = PdfLayoutProcessorAdapter(
        timeout_seconds=1,
        worker_command=(
            sys.executable,
            "-c",
            "import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)",
            str(pid_file),
        ),
    )

    with pytest.raises(AdapterError) as captured:
        adapter.process(_request(str(source)))

    assert captured.value.failure.kind == "timeout"
    worker_pid = int(pid_file.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(worker_pid, 0)
