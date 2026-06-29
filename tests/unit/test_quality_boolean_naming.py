from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_boolean_naming as gate


def _write(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source.strip() + "\n", encoding="utf-8")
    return path


def test_boolean_naming_flags_unclear_boolean_argument_and_field(tmp_path: Path) -> None:
    _write(
        tmp_path / "bad.py",
        """
class Options:
    retry: bool = False


def run(explain: bool = False) -> None:
    del explain
""",
    )

    findings = gate.collect_findings((tmp_path,))

    assert [(finding.name, finding.owner) for finding in findings] == [
        ("retry", "Options"),
        ("explain", "run"),
    ]


def test_boolean_naming_accepts_question_and_state_names(tmp_path: Path) -> None:
    _write(
        tmp_path / "good.py",
        """
class Options:
    enabled: bool = True
    is_deleted: bool = False


def run(
    *,
    include_explain: bool = False,
    has_permission: bool = True,
    can_execute: bool = True,
) -> None:
    del include_explain, has_permission, can_execute
""",
    )

    assert gate.collect_findings((tmp_path,)) == []


def test_boolean_naming_accepts_camel_case_api_boolean_fields(tmp_path: Path) -> None:
    _write(
        tmp_path / "api_payload.py",
        """
from typing import TypedDict


class Payload(TypedDict):
    isIdempotentReplay: bool
    hasPermission: bool
    shouldRetry: bool
""",
    )

    assert gate.collect_findings((tmp_path,)) == []


def test_boolean_naming_accepts_optional_bool_annotations(tmp_path: Path) -> None:
    _write(
        tmp_path / "options.py",
        """
def configure(*, dry_run: bool | None = None, include_items: bool | None = None) -> None:
    del dry_run, include_items
""",
    )

    findings = gate.collect_findings((tmp_path,))

    assert [finding.name for finding in findings] == ["dry_run"]


def test_boolean_naming_current_repo_has_no_findings() -> None:
    assert gate.collect_findings() == []


def test_boolean_naming_writes_json_report(tmp_path: Path) -> None:
    _write(
        tmp_path / "bad.py",
        """
def run(verbose: bool = False) -> None:
    del verbose
""",
    )
    findings = gate.collect_findings((tmp_path,))
    output = tmp_path / "boolean_naming.json"

    gate.write_report(output, findings)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["count"] == 1
    assert report["baseline"] == 0
    assert report["gate_pass"] is False
    assert report["violations"][0]["name"] == "verbose"
