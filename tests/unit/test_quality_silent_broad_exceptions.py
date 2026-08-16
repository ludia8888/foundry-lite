from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_silent_broad_exceptions as gate


def _write_file(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "service.py"
    path.write_text(source, encoding="utf-8")
    return path


def test_silent_broad_exception_gate_flags_normal_sentinels_and_loop_skips(tmp_path: Path) -> None:
    path = _write_file(
        tmp_path,
        """
def none_value():
    try:
        work()
    except Exception:
        return None

def false_value():
    try:
        work()
    except BaseException:
        return False

def skipped(items):
    for item in items:
        try:
            work(item)
        except:
            continue
""",
    )

    findings = gate.collect_findings((path,))

    assert len(findings) == 3
    assert {finding.code for finding in findings} == {"silent_broad_exception"}


def test_silent_broad_exception_gate_flags_nested_empty_fallback(tmp_path: Path) -> None:
    path = _write_file(
        tmp_path,
        """
def execute(enabled):
    try:
        work()
    except Exception:
        record_failure()
        if enabled:
            return {}
        raise
""",
    )

    assert [finding.code for finding in gate.collect_findings((path,))] == ["silent_broad_exception"]


def test_silent_broad_exception_gate_allows_explicit_failure_semantics(tmp_path: Path) -> None:
    path = _write_file(
        tmp_path,
        """
def reraises():
    try:
        work()
    except Exception:
        raise

def failure_payload(exc):
    try:
        work()
    except Exception as exc:
        return {"status": "failed", "error": runtime_error_payload(exc)}

def failure_incident(exc):
    try:
        work()
    except Exception as exc:
        return [build_failure_incident(exc)]

def deferred(exc):
    try:
        work()
    except Exception as exc:
        record_runtime_cleanup_failure(primary, cleanup_error=exc)
        return None

def propagated_by_handle(exc):
    errors = []
    try:
        work()
    except Exception as exc:
        errors.append(exc)
        return
""",
    )

    assert gate.collect_findings((path,)) == []


def test_silent_broad_exception_gate_does_not_accept_unrelated_side_effect(tmp_path: Path) -> None:
    path = _write_file(
        tmp_path,
        """
def execute():
    try:
        work()
    except Exception:
        record_metric("attempt")
        return None
""",
    )

    assert [finding.code for finding in gate.collect_findings((path,))] == ["silent_broad_exception"]


def test_silent_broad_exception_gate_rejects_misleading_error_named_helper(tmp_path: Path) -> None:
    path = _write_file(
        tmp_path,
        """
def execute():
    try:
        work()
    except Exception as exc:
        ignore_error(exc)
        return None
""",
    )

    assert [finding.code for finding in gate.collect_findings((path,))] == ["silent_broad_exception"]


def test_silent_broad_exception_gate_does_not_attribute_nested_handler_evidence_to_outer_handler(
    tmp_path: Path,
) -> None:
    path = _write_file(
        tmp_path,
        """
def execute():
    try:
        work()
    except Exception:
        try:
            cleanup()
        except Exception as cleanup_error:
            record_runtime_cleanup_failure(primary, cleanup_error=cleanup_error)
        return None
""",
    )

    assert [finding.code for finding in gate.collect_findings((path,))] == ["silent_broad_exception"]


def test_silent_broad_exception_gate_does_not_flag_typed_parse_fallback(tmp_path: Path) -> None:
    path = _write_file(
        tmp_path,
        """
def parse(value):
    try:
        return int(value)
    except ValueError:
        return None
""",
    )

    assert gate.collect_findings((path,)) == []


def test_silent_broad_exception_gate_writes_machine_readable_report(tmp_path: Path) -> None:
    path = _write_file(tmp_path, "def unsafe():\n    try:\n        work()\n    except Exception:\n        pass\n")
    output = tmp_path / "report.json"
    findings = gate.collect_findings((path,))

    gate.write_report(output, findings)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["gate_pass"] is False
    assert report["violations"][0]["code"] == "silent_broad_exception"
