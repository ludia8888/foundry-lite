from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_exception_string_redaction as gate


def _write_file(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "service.py"
    path.write_text(source, encoding="utf-8")
    return path


def test_exception_string_redaction_gate_flags_raw_exception_payload(tmp_path: Path) -> None:
    path = _write_file(
        tmp_path,
        """
def execute():
    try:
        call_provider()
    except Exception as exc:
        return {"message": str(exc)}
""",
    )

    findings = gate.collect_findings((path,))

    assert [finding.code for finding in findings] == ["raw_exception_string"]


def test_exception_string_redaction_gate_flags_arbitrary_caught_name_and_f_string(tmp_path: Path) -> None:
    path = _write_file(
        tmp_path,
        """
def execute():
    try:
        call_provider()
    except Exception as provider_problem:
        return {"message": f"provider failed: {provider_problem}"}
""",
    )

    findings = gate.collect_findings((path,))

    assert [finding.code for finding in findings] == ["raw_exception_string"]


def test_exception_string_redaction_gate_flags_repr_format_percent_and_attributes(tmp_path: Path) -> None:
    path = _write_file(
        tmp_path,
        """
def execute(exc):
    first = repr(exc)
    second = "provider failed: {}".format(exc)
    third = "provider failed: %s" % exc
    fourth = exc.message
    fifth = exc.detail
    sixth = str(exc.args[0])
    return first, second, third, fourth, fifth, sixth
""",
    )

    findings = gate.collect_findings((path,))

    assert len(findings) == 6
    assert {finding.code for finding in findings} == {"raw_exception_string"}


def test_exception_string_redaction_gate_allows_control_only_exception_inspection(tmp_path: Path) -> None:
    path = _write_file(
        tmp_path,
        """
def is_waiting(exc):
    return exc.message in {"lease held"} and len(exc.args) > 0
""",
    )

    assert gate.collect_findings((path,)) == []


def test_exception_string_redaction_gate_accepts_shared_redaction_boundaries(tmp_path: Path) -> None:
    path = _write_file(
        tmp_path,
        """
def safe_text(exc):
    return scrub_error_text(str(exc))

def safe_payload(error):
    return runtime_error_payload(error)

def safe_formatted(exc):
    return scrub_error_text(f"provider failed: {exc}")

def safe_attributes(exc):
    return scrub_error_text(exc.message), scrub_error_text(str(exc.args[0]))
""",
    )

    assert gate.collect_findings((path,)) == []


def test_exception_string_redaction_gate_does_not_treat_unrelated_values_as_exceptions(tmp_path: Path) -> None:
    path = _write_file(
        tmp_path,
        'def render(value, error):\n    return str(value), f"oauth error={error}"\n',
    )

    assert gate.collect_findings((path,)) == []


def test_exception_string_redaction_gate_writes_machine_readable_report(tmp_path: Path) -> None:
    path = _write_file(tmp_path, "def unsafe(error):\n    return str(error)\n")
    output = tmp_path / "report.json"
    findings = gate.collect_findings((path,))

    gate.write_report(output, findings)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["gate_pass"] is False
    assert report["violations"][0]["code"] == "raw_exception_string"
