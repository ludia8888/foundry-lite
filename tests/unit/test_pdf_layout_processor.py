from __future__ import annotations

from pathlib import Path

from foundry_lite.application.ports.media_processor import MediaProcessingRequest, ProcessorSpec
from foundry_lite.infrastructure.adapters.pdf_layout_processor import PdfLayoutProcessorAdapter


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


def test_layout_processor_supports_only_its_pinned_processor_family() -> None:
    adapter = PdfLayoutProcessorAdapter()
    assert adapter.supports(_request("source.pdf")) is True
    other = ProcessorSpec("pdf_text_v1", "1")
    request = MediaProcessingRequest("t", "v", "b", other, "h", source_path="source.pdf")
    assert adapter.supports(request) is False
