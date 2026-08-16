from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_foundry_runtime_lifecycle as gate


def _source(tmp_path: Path, value: str) -> Path:
    path = tmp_path / "composition_root.py"
    path.write_text(value, encoding="utf-8")
    return path


def test_foundry_runtime_lifecycle_gate_flags_leaks_and_normal_path_only_close(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """
def leaked(dependencies):
    foundry = FoundryLite(dependencies=dependencies)
    return foundry.demo.run()

def close_only_on_success(dependencies):
    foundry = FoundryLite(dependencies=dependencies)
    result = foundry.demo.run()
    foundry.close()
    return result

def chained(dependencies):
    return FoundryLite(dependencies=dependencies).demo.run()
""",
    )

    findings = gate.collect_findings((path,))

    assert len(findings) == 3
    assert {finding.code for finding in findings} == {"foundry_runtime_not_closed_or_transferred"}


def test_foundry_runtime_lifecycle_gate_allows_finally_and_ownership_transfer(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """
def execute(dependencies):
    foundry = FoundryLite(dependencies=dependencies)
    try:
        return foundry.demo.run()
    finally:
        foundry.close()

def build(dependencies):
    return FoundryLite(dependencies=dependencies)

def build_wrapper(dependencies):
    foundry = FoundryLite(dependencies=dependencies)
    try:
        foundry.bootstrap()
    except Exception:
        foundry.close()
        raise
    return Runtime(foundry=foundry)

def cached_wrapper(dependencies):
    runtime = Runtime(foundry=FoundryLite(dependencies=dependencies))
    return runtime
""",
    )

    findings = gate.collect_findings((path,))
    assert len(findings) == 1
    assert findings[0].code == "foundry_runtime_not_closed_or_transferred"


def test_foundry_runtime_lifecycle_gate_does_not_count_nested_function_close(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """
def leaked(dependencies):
    foundry = FoundryLite(dependencies=dependencies)
    def unrelated():
        try:
            work()
        finally:
            foundry.close()
    return foundry.demo.run()
""",
    )

    assert [finding.code for finding in gate.collect_findings((path,))] == ["foundry_runtime_not_closed_or_transferred"]


def test_foundry_runtime_lifecycle_gate_requires_immediate_exception_guard(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """
def leaked_during_configuration(dependencies):
    foundry = FoundryLite(dependencies=dependencies)
    configure(foundry)
    try:
        return foundry.demo.run()
    finally:
        foundry.close()
""",
    )

    assert [finding.code for finding in gate.collect_findings((path,))] == ["foundry_runtime_not_closed_or_transferred"]


def test_foundry_runtime_lifecycle_gate_flags_unprotected_delayed_transfer(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """
def unsafe_builder(dependencies):
    foundry = FoundryLite(dependencies=dependencies)
    configure(foundry)
    return Runtime(foundry=foundry)
""",
    )

    assert [finding.code for finding in gate.collect_findings((path,))] == ["foundry_runtime_not_closed_or_transferred"]


def test_foundry_runtime_lifecycle_gate_writes_machine_readable_report(tmp_path: Path) -> None:
    path = _source(tmp_path, "foundry = FoundryLite(dependencies=dependencies)\n")
    output = tmp_path / "report.json"
    findings = gate.collect_findings((path,))

    gate.write_report(output, findings)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["baseline"] == 0
    assert report["gate_pass"] is False
    assert report["violations"][0]["code"] == "foundry_runtime_not_closed_or_transferred"
