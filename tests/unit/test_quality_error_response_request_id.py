from __future__ import annotations

from pathlib import Path

from scripts.quality import check_error_response_has_request_id as gate


def _write_api_file(tmp_path: Path, source: str) -> Path:
    api_file = tmp_path / "apps" / "api" / "foundry_lite_api" / "main.py"
    api_file.parent.mkdir(parents=True)
    api_file.write_text(source, encoding="utf-8")
    return api_file


def test_error_response_gate_flags_http_exception_without_request_id(tmp_path: Path) -> None:
    api_file = _write_api_file(
        tmp_path,
        """
def handler():
    raise HTTPException(status_code=400, detail={"code": "BAD_REQUEST"})
""",
    )

    findings = gate.collect_findings((api_file,))

    assert [finding.code for finding in findings] == ["http_exception_missing_request_id"]


def test_error_response_gate_accepts_http_exception_detail_with_request_id(tmp_path: Path) -> None:
    api_file = _write_api_file(
        tmp_path,
        """
def handler(request_id):
    raise HTTPException(status_code=400, detail={"code": "BAD_REQUEST", "request_id": request_id})
""",
    )

    assert gate.collect_findings((api_file,)) == []


def test_error_response_gate_flags_handle_error_without_request(tmp_path: Path) -> None:
    api_file = _write_api_file(
        tmp_path,
        """
def handler(exc, request):
    raise _handle_error(exc) from exc
""",
    )

    findings = gate.collect_findings((api_file,))

    assert [finding.code for finding in findings] == ["handle_error_missing_request"]


def test_error_response_gate_accepts_handle_error_with_request(tmp_path: Path) -> None:
    api_file = _write_api_file(
        tmp_path,
        """
def handler(exc, request):
    raise _handle_error(exc, request) from exc
""",
    )

    assert gate.collect_findings((api_file,)) == []


def test_error_response_gate_flags_fastapi_validation_handler_missing(tmp_path: Path) -> None:
    api_file = _write_api_file(
        tmp_path,
        """
app = FastAPI()
""",
    )

    findings = gate.collect_findings((api_file,))

    assert [finding.code for finding in findings] == ["validation_handler_missing"]


def test_error_response_gate_accepts_validation_handler_with_request_id(tmp_path: Path) -> None:
    api_file = _write_api_file(
        tmp_path,
        """
app = FastAPI()

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    request_id = request.state.request_id
    return JSONResponse(detail={"request_id": request_id})
""",
    )

    assert gate.collect_findings((api_file,)) == []


def test_error_response_gate_writes_json_report(tmp_path: Path) -> None:
    api_file = _write_api_file(
        tmp_path,
        """
def handler():
    raise HTTPException(status_code=400)
""",
    )
    output = tmp_path / "quality" / "error_response_request_id.json"
    findings = gate.collect_findings((api_file,))

    gate.write_report(output, findings)

    report = output.read_text(encoding="utf-8")
    assert '"gate_pass": false' in report
    assert '"http_exception_missing_request_id"' in report
