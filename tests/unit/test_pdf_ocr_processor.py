from __future__ import annotations

import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.media_processor import MediaProcessingRequest, ProcessorSpec
from foundry_lite.infrastructure.adapters import pdf_ocr_processor as pdf_ocr
from foundry_lite.infrastructure.adapters.pdf_ocr_processor import (
    PdfEmbeddedImage,
    PdfOcrDocumentError,
    PdfOcrLine,
    PdfOcrProcessorAdapter,
    RasterizedPdfPage,
)
from foundry_lite.infrastructure.adapters.pdf_page_selection import PdfPageSelection


def _request(source_path: str, **parameters: object) -> MediaProcessingRequest:
    return MediaProcessingRequest(
        tenant_id="tenant-demo",
        media_item_version_id="miv-scanned-pdf-1",
        blob_key="blob-scanned-pdf-1",
        spec=ProcessorSpec("pdf_ocr_v1", "1", parameters=parameters),
        processing_spec_hash="pdf-ocr-spec-1",
        source_path=source_path,
        source_format="pdf",
        source_mime_type="application/pdf",
    )


def test_pdf_ocr_emits_deterministic_page_bbox_and_hierarchy_units(tmp_path: Path) -> None:
    calls: list[tuple[int, int, str]] = []

    def rasterizer(
        source_path: str,
        output_dir: str,
        max_pages: int,
        selection: PdfPageSelection,
        dpi: int,
        timeout_seconds: int,
    ) -> tuple[RasterizedPdfPage, ...]:
        del source_path, output_dir, max_pages, timeout_seconds
        calls.append((selection.start, dpi, "raster"))
        return (RasterizedPdfPage(2, "page-2.png", 1000, 1400),)

    def ocr_engine(
        page: RasterizedPdfPage,
        languages: str,
        timeout_seconds: int,
    ) -> tuple[PdfOcrLine, ...]:
        del timeout_seconds
        calls.append((page.page_number, 0, languages))
        return (
            PdfOcrLine(2, "Annual Report", 80, 60, 700, 52, 1000, 1400, 0.97),
            PdfOcrLine(2, "Financial Overview", 80, 190, 620, 31, 1000, 1400, 0.94),
            PdfOcrLine(2, "Revenue Detail", 80, 280, 500, 24, 1000, 1400, 0.92),
            PdfOcrLine(2, "Revenue increased.", 80, 380, 600, 18, 1000, 1400, 0.91),
            PdfOcrLine(2, "Margin improved.", 80, 430, 560, 18, 1000, 1400, 0.90),
        )

    source = tmp_path / "scanned.pdf"
    source.write_bytes(b"fake-pdf-for-injected-runtime")
    adapter = PdfOcrProcessorAdapter(rasterizer=rasterizer, ocr_engine=ocr_engine)
    request = _request(
        str(source),
        maxPages=10,
        pageSelection={"start": 2, "limit": 1},
        dpi=200,
        languages="eng+kor",
    )

    first = adapter.process(request)
    second = adapter.process(request)

    assert first.derivative_kind == "pdf_ocr"
    assert first.mime_type == "application/json"
    assert first.content_hash == second.content_hash
    assert [unit.structure["role"] for unit in first.units if unit.structure] == [
        "title",
        "heading_1",
        "heading_2",
        "body",
        "body",
    ]
    assert first.processing_evidence is not None
    assert first.processing_evidence["selectedPages"] == [2]
    assert first.processing_evidence["dpi"] == 200
    assert first.processing_evidence["languages"] == "eng+kor"
    assert calls == [(2, 200, "raster"), (2, 0, "eng+kor")] * 2
    for unit in first.units:
        assert unit.unit_kind == "ocr_line"
        assert unit.page_number == 2
        assert unit.bbox is not None
        assert unit.source_locator == {
            "pageNumber": 2,
            "bbox": unit.bbox,
            "coordinateSystem": "image_top_left_pixels",
        }
        assert unit.structure is not None
        assert unit.structure["classificationMethod"] == "tesseract_line_height_position_heuristic_v1"
        assert unit.confidence is not None and 0 < unit.confidence < 1


def test_pdf_ocr_invalid_language_is_typed_and_does_not_invoke_runtime(tmp_path: Path) -> None:
    source = tmp_path / "scanned.pdf"
    source.write_bytes(b"fake")
    adapter = PdfOcrProcessorAdapter(
        rasterizer=lambda *_args: pytest.fail("invalid settings must fail before rasterization"),
    )

    with pytest.raises(AdapterError) as exc_info:
        adapter.process(_request(str(source), languages="eng;rm -rf"))

    failure = exc_info.value.failure
    assert failure.kind == "validation"
    assert failure.is_retryable is False
    assert failure.details["reason"] == "ocr_languages_invalid"
    assert "sourcePath" not in failure.details


@pytest.mark.parametrize(
    ("parameters", "reason"),
    [
        ({"maxPages": 10_001}, "max_pages_out_of_range"),
        ({"pageSelection": {"start": 1, "limit": 10_001}}, "page_selection_limit_exceeded"),
    ],
)
def test_pdf_ocr_rejects_page_bounds_before_invoking_runtime(
    tmp_path: Path,
    parameters: dict[str, object],
    reason: str,
) -> None:
    source = tmp_path / "scanned.pdf"
    source.write_bytes(b"fake")
    adapter = PdfOcrProcessorAdapter(
        rasterizer=lambda *_args: pytest.fail("invalid page bounds must fail before rasterization"),
    )

    with pytest.raises(AdapterError) as exc_info:
        adapter.process(_request(str(source), **parameters))

    assert exc_info.value.failure.kind == "validation"
    assert exc_info.value.failure.details["reason"] == reason


def test_pdf_ocr_runtime_timeout_is_retryable_and_page_scoped(tmp_path: Path) -> None:
    source = tmp_path / "scanned.pdf"
    source.write_bytes(b"fake")

    def timeout_rasterizer(*_args: object) -> tuple[RasterizedPdfPage, ...]:
        raise PdfOcrDocumentError(
            "pdf_rasterization_timeout",
            kind="timeout",
            is_retryable=True,
            page=3,
        )

    adapter = PdfOcrProcessorAdapter(rasterizer=timeout_rasterizer)
    with pytest.raises(AdapterError) as exc_info:
        adapter.process(_request(str(source)))

    failure = exc_info.value.failure
    assert failure.kind == "timeout"
    assert failure.is_retryable is True
    assert failure.details["page"] == 3
    assert failure.timeout_seconds == 120


def test_pdf_ocr_supports_only_the_pinned_pdf_ocr_family() -> None:
    adapter = PdfOcrProcessorAdapter()
    assert adapter.supports(_request("source.pdf")) is True
    other = MediaProcessingRequest(
        "tenant",
        "version",
        "blob",
        ProcessorSpec("ocr_v1", "1"),
        "hash",
        source_path="source.pdf",
    )
    assert adapter.supports(other) is False


def test_pdf_ocr_contract_and_missing_source_are_typed() -> None:
    adapter = PdfOcrProcessorAdapter()
    contract = adapter.failure_contract()
    request = _request("source.pdf")
    missing_source = MediaProcessingRequest(
        request.tenant_id,
        request.media_item_version_id,
        request.blob_key,
        request.spec,
        request.processing_spec_hash,
    )

    assert {mode.kind for mode in contract.modes} == {"validation", "unavailable", "timeout"}
    with pytest.raises(AdapterError) as exc_info:
        adapter.process(missing_source)
    assert exc_info.value.failure.details["reason"] == "pdf OCR requires a sandbox source_path"


def test_pdf_ocr_poppler_selection_and_command_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = PdfPageSelection(start=1, limit=None)
    monkeypatch.setattr(pdf_ocr, "_require_pdf_raster_bound", lambda *_args: None)
    monkeypatch.setattr(pdf_ocr, "_selected_pdf_page_numbers", lambda *_args: [])
    assert pdf_ocr._poppler_rasterize("a.pdf", str(tmp_path), 10, selection, 150, 3) == ()

    monkeypatch.setattr(pdf_ocr, "_selected_pdf_page_numbers", lambda *_args: [2, 3])
    monkeypatch.setattr(pdf_ocr.shutil, "which", lambda _name: None)
    with pytest.raises(PdfOcrDocumentError, match="pdftoppm_unavailable"):
        pdf_ocr._poppler_rasterize("a.pdf", str(tmp_path), 10, selection, 150, 3)

    monkeypatch.setattr(pdf_ocr.shutil, "which", lambda _name: "/usr/bin/pdftoppm")
    commands: list[tuple[list[str], int]] = []
    monkeypatch.setattr(pdf_ocr, "_run_pdftoppm", lambda command, timeout: commands.append((list(command), timeout)))
    expected = (RasterizedPdfPage(2, "page-2.png", 1, 1),)
    monkeypatch.setattr(pdf_ocr, "_rasterized_pages", lambda *_args: expected)
    assert pdf_ocr._poppler_rasterize("a.pdf", str(tmp_path), 10, selection, 200, 7) == expected
    assert commands[0][0] == [
        "/usr/bin/pdftoppm",
        "-f",
        "2",
        "-l",
        "3",
        "-r",
        "200",
        "-png",
        "a.pdf",
        str(tmp_path / "page"),
    ]
    assert 1 <= commands[0][1] <= 7


def test_pdf_ocr_raster_geometry_is_bounded_before_poppler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = PdfPageSelection(start=1, limit=1)
    monkeypatch.setattr(pdf_ocr, "_selected_pdf_page_numbers", lambda *_args: [1])
    monkeypatch.setattr(pdf_ocr, "_pdf_page_dimensions", lambda *_args: {1: (20_000.0, 20_000.0)})
    monkeypatch.setattr(
        pdf_ocr,
        "_run_pdftoppm",
        lambda *_args: pytest.fail("over-limit geometry must fail before Poppler rasterization"),
    )

    with pytest.raises(PdfOcrDocumentError, match="raster_page_pixel_limit_exceeded"):
        pdf_ocr._poppler_rasterize("a.pdf", str(tmp_path), 1, selection, 300, 5)


def test_pdf_ocr_parses_per_page_geometry_and_rejects_missing_pages() -> None:
    output = "\n".join(
        (
            "Pages:           2",
            "Page    1 size:  612 x 792 pts (letter)",
            "Page    2 size:  595.28 x 841.89 pts (A4)",
        )
    )

    assert pdf_ocr._parsed_pdf_page_dimensions(output, [1, 2]) == {
        1: (612.0, 792.0),
        2: (595.28, 841.89),
    }
    with pytest.raises(PdfOcrDocumentError, match="pdf_page_geometry_invalid"):
        pdf_ocr._parsed_pdf_page_dimensions(output, [1, 2, 3])


def test_pdf_ocr_rejects_total_raster_pixel_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = list(range(1, 51))
    monkeypatch.setattr(
        pdf_ocr,
        "_pdf_page_dimensions",
        lambda *_args: {page: (612.0, 792.0) for page in pages},
    )

    with pytest.raises(PdfOcrDocumentError, match="raster_total_pixel_limit_exceeded"):
        pdf_ocr._require_pdf_raster_bound("a.pdf", pages, 150, 5)


def test_pdf_ocr_rejects_large_compressed_image_before_poppler_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = PdfPageSelection(start=1, limit=1)
    monkeypatch.setattr(pdf_ocr, "_selected_pdf_page_numbers", lambda *_args: [1])
    monkeypatch.setattr(pdf_ocr, "_pdf_page_dimensions", lambda *_args: {1: (612.0, 792.0)})
    monkeypatch.setattr(
        pdf_ocr,
        "_pdf_embedded_images",
        lambda *_args: (PdfEmbeddedImage(1, 30_000, 30_000),),
    )
    monkeypatch.setattr(
        pdf_ocr,
        "_run_pdftoppm",
        lambda *_args: pytest.fail("over-limit embedded image must fail before Poppler decoding"),
    )

    with pytest.raises(PdfOcrDocumentError, match="embedded_image_pixel_limit_exceeded"):
        pdf_ocr._poppler_rasterize("a.pdf", str(tmp_path), 1, selection, 150, 5)


def test_pdf_ocr_rejects_cumulative_embedded_image_pixel_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    images = tuple(PdfEmbeddedImage(page, 4_000, 4_000) for page in range(1, 8))
    monkeypatch.setattr(pdf_ocr, "_pdf_embedded_images", lambda *_args: images)

    with pytest.raises(PdfOcrDocumentError, match="embedded_image_total_pixel_limit_exceeded"):
        pdf_ocr._require_pdf_embedded_image_bound("a.pdf", list(range(1, 8)), 5)


def test_pdf_ocr_parses_embedded_image_metadata_and_filters_selected_pages() -> None:
    output = "\n".join(
        (
            "page   num  type   width height color comp bpc enc interp object ID x-ppi y-ppi size ratio",
            "------------------------------------------------------------------------------------------",
            "   1     0 image     640    480  rgb     3   8  jpeg   no       10  0   72   72  12K 2%",
            "   2     1 image   30000  30000  rgb     3   8  jpeg   no       11  0   72   72  20K 1%",
        )
    )

    assert pdf_ocr._parsed_pdf_embedded_images(output, [2]) == (PdfEmbeddedImage(2, 30_000, 30_000),)
    with pytest.raises(PdfOcrDocumentError, match="pdf_image_metadata_invalid"):
        pdf_ocr._parsed_pdf_embedded_images("1 0 image bad 480 rgb 3 8", [1])
    with pytest.raises(PdfOcrDocumentError, match="pdf_image_metadata_invalid"):
        pdf_ocr._parsed_pdf_embedded_images("1 0 image 0 480 rgb 3 8", [1])


def test_pdfimages_discovery_uses_fixed_binary_and_selected_page_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[tuple[list[str], int]] = []
    monkeypatch.setattr(pdf_ocr.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        pdf_ocr,
        "_bounded_pdfimages_output",
        lambda command, timeout: commands.append((list(command), timeout)) or "2 0 image 640 480 rgb 3 8",
    )

    images = pdf_ocr._pdf_embedded_images("/sandbox/input.pdf", [2, 3], 7)

    assert images == (PdfEmbeddedImage(2, 640, 480),)
    assert commands == [(["/usr/bin/pdfimages", "-f", "2", "-l", "3", "-list", "/sandbox/input.pdf"], 7)]
    monkeypatch.setattr(pdf_ocr.shutil, "which", lambda _name: None)
    with pytest.raises(PdfOcrDocumentError, match="pdfimages_unavailable"):
        pdf_ocr._pdf_embedded_images("/sandbox/input.pdf", [2], 7)


def test_bounded_pdfimages_output_handles_success_nonzero_and_invalid_utf8() -> None:
    output = pdf_ocr._bounded_pdfimages_output([sys.executable, "-c", "print('metadata')"], 5)
    assert output == "metadata\n"

    with pytest.raises(PdfOcrDocumentError, match="corrupt_pdf"):
        pdf_ocr._bounded_pdfimages_output([sys.executable, "-c", "raise SystemExit(3)"], 5)
    with pytest.raises(PdfOcrDocumentError, match="pdf_image_metadata_invalid"):
        pdf_ocr._bounded_pdfimages_output(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(bytes([255]))"],
            5,
        )


def test_bounded_pdfimages_output_maps_start_and_timeout_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_start(*_args: object, **_kwargs: object) -> None:
        raise OSError("missing binary")

    monkeypatch.setattr(pdf_ocr.subprocess, "Popen", fail_start)
    with pytest.raises(PdfOcrDocumentError, match="pdfimages_unavailable"):
        pdf_ocr._bounded_pdfimages_output(["pdfimages", "-list", "a.pdf"], 5)

    process = SimpleNamespace(
        stdout=object(),
        returncode=None,
        is_killed=False,
    )
    process.poll = lambda: None
    process.kill = lambda: setattr(process, "is_killed", True)
    process.wait = lambda timeout: setattr(process, "returncode", -9)
    monkeypatch.setattr(pdf_ocr.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        pdf_ocr,
        "_read_bounded_pdfimages_stdout",
        lambda *_args: (_ for _ in ()).throw(subprocess.TimeoutExpired("pdfimages", 5)),
    )

    with pytest.raises(PdfOcrDocumentError, match="pdf_image_discovery_timeout"):
        pdf_ocr._bounded_pdfimages_output(["pdfimages", "-list", "a.pdf"], 5)
    assert process.is_killed is True


@pytest.mark.parametrize(
    ("byte_limit", "line_limit", "program"),
    (
        (64, 10_000, "print('x' * 1_000)"),
        (2 * 1024 * 1024, 2, "print('a\\nb\\nc')"),
    ),
)
def test_pdfimages_listing_is_bounded_before_buffering(
    monkeypatch: pytest.MonkeyPatch,
    byte_limit: int,
    line_limit: int,
    program: str,
) -> None:
    monkeypatch.setattr(pdf_ocr, "_MAX_PDF_IMAGE_METADATA_BYTES", byte_limit)
    monkeypatch.setattr(pdf_ocr, "_MAX_PDF_IMAGE_METADATA_LINES", line_limit)

    with pytest.raises(PdfOcrDocumentError, match="pdf_image_metadata_limit_exceeded"):
        pdf_ocr._bounded_pdfimages_output([sys.executable, "-c", program], 5)


def test_pdf_ocr_page_discovery_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    selection = PdfPageSelection(start=1, limit=2)
    monkeypatch.setattr(pdf_ocr, "_pdf_page_metadata", lambda *_args: (3, False))
    assert pdf_ocr._selected_pdf_page_numbers("a.pdf", 3, selection, 5) == [1, 2]
    with pytest.raises(PdfOcrDocumentError, match="page_limit_exceeded"):
        pdf_ocr._selected_pdf_page_numbers("a.pdf", 2, selection, 5)
    monkeypatch.setattr(pdf_ocr, "_pdf_page_metadata", lambda *_args: (3, True))
    with pytest.raises(PdfOcrDocumentError, match="encrypted_pdf"):
        pdf_ocr._selected_pdf_page_numbers("a.pdf", 3, selection, 5)


@pytest.mark.parametrize(
    ("effect", "reason"),
    [
        (subprocess.TimeoutExpired("pdfinfo", 1), "pdf_page_discovery_timeout"),
        (OSError("missing"), "pdfinfo_unavailable"),
    ],
)
def test_pdf_ocr_page_discovery_process_failures(
    monkeypatch: pytest.MonkeyPatch,
    effect: Exception,
    reason: str,
) -> None:
    monkeypatch.setattr(pdf_ocr.shutil, "which", lambda _name: "/usr/bin/pdfinfo")
    monkeypatch.setattr(pdf_ocr.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(effect))

    with pytest.raises(PdfOcrDocumentError, match=reason):
        pdf_ocr._pdf_page_metadata("a.pdf", 1)


def test_pdf_ocr_page_discovery_parses_bounded_poppler_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pdf_ocr.shutil, "which", lambda _name: "/usr/bin/pdfinfo")
    monkeypatch.setattr(
        pdf_ocr.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="Pages: 7\nEncrypted: no\n", stderr=""),
    )

    assert pdf_ocr._pdf_page_metadata("a.pdf", 3) == (7, False)

    monkeypatch.setattr(
        pdf_ocr.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="Incorrect password"),
    )
    with pytest.raises(PdfOcrDocumentError, match="encrypted_pdf"):
        pdf_ocr._pdf_page_metadata("a.pdf", 3)


@pytest.mark.parametrize(
    ("effect", "reason"),
    [
        (subprocess.TimeoutExpired("pdftoppm", 1), "pdf_rasterization_timeout"),
        (OSError("missing"), "pdftoppm_unavailable"),
    ],
)
def test_pdf_ocr_pdftoppm_errors(
    monkeypatch: pytest.MonkeyPatch,
    effect: Exception,
    reason: str,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise effect

    monkeypatch.setattr(pdf_ocr.subprocess, "run", fail)
    with pytest.raises(PdfOcrDocumentError, match=reason):
        pdf_ocr._run_pdftoppm(["pdftoppm"], 1)

    monkeypatch.setattr(
        pdf_ocr.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )
    with pytest.raises(PdfOcrDocumentError, match="pdf_rasterization_failed"):
        pdf_ocr._run_pdftoppm(["pdftoppm"], 1)
    monkeypatch.setattr(
        pdf_ocr.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    pdf_ocr._run_pdftoppm(["pdftoppm"], 1)


def test_pdf_ocr_rasterized_page_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "page-2.png").write_bytes(b"png")

    class _Image:
        size = (640, 480)

    monkeypatch.setattr(
        pdf_ocr,
        "import_module",
        lambda _name: SimpleNamespace(open=lambda _path: nullcontext(_Image())),
    )
    assert pdf_ocr._rasterized_pages(tmp_path, [2]) == (RasterizedPdfPage(2, str(tmp_path / "page-2.png"), 640, 480),)
    with pytest.raises(PdfOcrDocumentError, match="pdf_rasterization_incomplete") as exc_info:
        pdf_ocr._rasterized_pages(tmp_path, [2, 3])
    assert exc_info.value.page == 3
    with pytest.raises(PdfOcrDocumentError, match="filename_invalid"):
        pdf_ocr._page_number(tmp_path / "bad.png")


def test_pdf_ocr_tesseract_data_and_error_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = RasterizedPdfPage(1, "page.png", 100, 200)
    data = {
        "text": ["", "Hello", "world"],
        "block_num": [1, 1, 1],
        "par_num": [1, 1, 1],
        "line_num": [1, 2, 2],
        "left": [0, 5, 30],
        "top": [0, 10, 10],
        "width": [0, 20, 20],
        "height": [0, 12, 12],
        "conf": [-1, "90", 80],
    }
    lines = pdf_ocr._lines_from_tesseract_data(data, page)
    assert [(line.text, line.confidence) for line in lines] == [("Hello world", 0.85)]
    assert pdf_ocr._line_from_words([("x", 1, 2, 0, 0, -1)], page).confidence == 0.0

    with pytest.raises(PdfOcrDocumentError, match="ocr_output_invalid"):
        pdf_ocr._lines_from_tesseract_data([], page)
    for raw, helper in [
        ({"value": "bad"}, lambda value: pdf_ocr._data_column(value, "value")),
        ({"value": []}, lambda value: pdf_ocr._column_int(value, "value", 1)),
        ({"value": [True]}, lambda value: pdf_ocr._column_int(value, "value", 0)),
        ({"value": ["x"]}, lambda value: pdf_ocr._column_int(value, "value", 0)),
        ({"value": []}, lambda value: pdf_ocr._column_float(value, "value", 1)),
        ({"value": [object()]}, lambda value: pdf_ocr._column_float(value, "value", 0)),
        ({"value": ["x"]}, lambda value: pdf_ocr._column_float(value, "value", 0)),
    ]:
        with pytest.raises(PdfOcrDocumentError, match="ocr_output_invalid"):
            helper(raw)

    assert pdf_ocr._tesseract_error(TimeoutError("slow"), 4).kind == "timeout"
    assert pdf_ocr._tesseract_error(RuntimeError("binary not found"), 4).kind == "unavailable"
    assert pdf_ocr._tesseract_error(RuntimeError("bad output"), 4).reason == "ocr_failed"

    class _Image:
        pass

    fake_image_module = SimpleNamespace(open=lambda _path: nullcontext(_Image()))
    fake_tesseract = SimpleNamespace(
        Output=SimpleNamespace(DICT="dict"),
        image_to_data=lambda *_args, **_kwargs: data,
    )
    monkeypatch.setattr(
        pdf_ocr,
        "import_module",
        lambda name: fake_tesseract if name == "pytesseract" else fake_image_module,
    )
    assert pdf_ocr._tesseract_page_lines(page, "eng", 2)[0].text == "Hello world"

    fake_tesseract.image_to_data = lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("slow"))
    with pytest.raises(PdfOcrDocumentError, match="ocr_timeout"):
        pdf_ocr._tesseract_page_lines(page, "eng", 2)


def test_pdf_ocr_parameter_time_and_tool_version_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(PdfOcrDocumentError):
        pdf_ocr._normalized_parameters(_request("a.pdf", pageSelection={"start": 0}))
    monkeypatch.setattr(pdf_ocr.time, "monotonic", lambda: 10.0)
    with pytest.raises(PdfOcrDocumentError, match="pdf_ocr_timeout"):
        pdf_ocr._remaining_seconds(10.5)

    monkeypatch.setattr(pdf_ocr.shutil, "which", lambda _name: None)
    assert pdf_ocr._tool_version("tesseract") == "unavailable"
    monkeypatch.setattr(pdf_ocr.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        pdf_ocr.subprocess,
        "run",
        lambda command, **_kwargs: SimpleNamespace(
            stdout="",
            stderr="pdftoppm 1.0\n" if "-v" in command else "",
        ),
    )
    assert pdf_ocr._tool_version("pdftoppm") == "pdftoppm 1.0"
    assert pdf_ocr._tool_version("tesseract") == "unknown"

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise OSError("missing")

    monkeypatch.setattr(pdf_ocr.subprocess, "run", unavailable)
    assert pdf_ocr._tool_version("tesseract") == "unavailable"
