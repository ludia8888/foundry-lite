"""PDF raw-text processor adapter (Media/Content Plane M2).

Real pypdf extraction for the normal / encrypted / corrupt / page-limit paths; an injected
slow extractor proves the wall-clock timeout fails closed. Extraction is deterministic:
the same bytes + spec yield identical per-page text hashes (the idempotency anchor).
"""

from __future__ import annotations

import threading
from importlib import import_module
from pathlib import Path

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.media_processor import MediaProcessingRequest, ProcessorSpec
from foundry_lite.infrastructure.adapters.pdf_text_processor import PdfTextProcessorAdapter


def _make_pdf(pages: list[str]) -> bytes:
    objs: list[bytes] = [b"<</Type/Catalog/Pages 2 0 R>>"]
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    objs.append(("<</Type/Pages/Kids[" + kids + f"]/Count {len(pages)}>>").encode())
    font_obj = 3 + 2 * len(pages)
    for i, text in enumerate(pages):
        content = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
        objs.append(
            f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents {4 + 2 * i} 0 R"
            f"/Resources<</Font<</F1 {font_obj} 0 R>>>>>>".encode()
        )
        objs.append(b"<</Length %d>>stream\n" % len(content) + content + b"\nendstream")
    objs.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    out = b"%PDF-1.4\n"
    offsets: list[int] = []
    for number, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj".encode() + body + b"endobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)
    return out


def _encrypted_pdf(pages: list[str]) -> bytes:
    pypdf = import_module("pypdf")
    import io

    reader = pypdf.PdfReader(io.BytesIO(_make_pdf(pages)))
    writer = pypdf.PdfWriter()
    writer.append(reader)
    writer.encrypt("secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _write(tmp_path: Path, data: bytes) -> str:
    path = tmp_path / "source.pdf"
    path.write_bytes(data)
    return str(path)


def _request(source_path: str, *, max_pages: int = 100) -> MediaProcessingRequest:
    spec = ProcessorSpec(processor="pdf_text_v1", processor_version="1.0.0", parameters={"maxPages": max_pages})
    return MediaProcessingRequest(
        tenant_id="t",
        media_item_version_id="miv-1",
        blob_key="blob-1",
        spec=spec,
        processing_spec_hash="spec-1",
        source_path=source_path,
    )


def test_extracts_per_page_text_deterministically(tmp_path: Path) -> None:
    adapter = PdfTextProcessorAdapter()
    request = _request(_write(tmp_path, _make_pdf(["Hello M2 one", "Second page"])))

    first = adapter.process(request)
    second = adapter.process(request)

    assert first.derivative_kind == "pdf_text"
    assert len(first.units) == 2
    assert "Hello M2 one" in first.units[0].text
    assert first.units[0].page_number == 1
    # Deterministic: same bytes + spec => identical hashes (idempotency anchor).
    assert first.content_hash == second.content_hash
    assert [u.text_hash for u in first.units] == [u.text_hash for u in second.units]


def test_supports_only_pdf_text_processor() -> None:
    adapter = PdfTextProcessorAdapter()
    assert adapter.supports(_request("x")) is True
    other = ProcessorSpec(processor="ocr_v1", processor_version="1")
    assert adapter.supports(MediaProcessingRequest("t", "v", "b", other, "h", source_path="x")) is False


def test_failure_contract_declares_validation_and_timeout() -> None:
    contract = PdfTextProcessorAdapter().failure_contract()
    assert contract.adapter_profile == "pdf-pypdf"
    kinds = {mode.kind for mode in contract.modes}
    assert {"validation", "timeout"} <= kinds


def test_corrupt_pdf_is_validation_failure(tmp_path: Path) -> None:
    adapter = PdfTextProcessorAdapter()
    with pytest.raises(AdapterError) as excinfo:
        adapter.process(_request(_write(tmp_path, b"this is not a pdf at all")))
    assert excinfo.value.failure.kind == "validation"
    assert excinfo.value.failure.is_retryable is False


def test_encrypted_pdf_is_validation_failure(tmp_path: Path) -> None:
    adapter = PdfTextProcessorAdapter()
    with pytest.raises(AdapterError) as excinfo:
        adapter.process(_request(_write(tmp_path, _encrypted_pdf(["secret page"]))))
    assert excinfo.value.failure.kind == "validation"
    assert "encrypted" in str(excinfo.value.failure.details.get("reason"))


def test_page_limit_exceeded_is_validation_failure(tmp_path: Path) -> None:
    adapter = PdfTextProcessorAdapter()
    with pytest.raises(AdapterError) as excinfo:
        adapter.process(_request(_write(tmp_path, _make_pdf(["a", "b", "c"])), max_pages=2))
    assert excinfo.value.failure.kind == "validation"


def test_timeout_fails_closed(tmp_path: Path) -> None:
    release = threading.Event()

    def _blocking(_source: str, _max_pages: int) -> list[str]:
        release.wait(timeout=10)  # bounded safety; the test releases it immediately below
        return ["never"]

    # timeout_seconds=0: the worker has not finished, so result(timeout=0) fails closed at once.
    adapter = PdfTextProcessorAdapter(timeout_seconds=0, page_extractor=_blocking)
    try:
        with pytest.raises(AdapterError) as excinfo:
            adapter.process(_request(_write(tmp_path, _make_pdf(["x"]))))
        assert excinfo.value.failure.kind == "timeout"
        assert excinfo.value.failure.is_retryable is True
    finally:
        release.set()
