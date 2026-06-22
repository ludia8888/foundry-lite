"""Require repository status transitions to go through the shared CAS primitive."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHS = (ROOT / "libs" / "foundry_lite" / "infrastructure" / "repositories",)
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "status_transition_cas.json"
ALLOWED_FILES = {ROOT / "libs" / "foundry_lite" / "infrastructure" / "repositories" / "status_cas.py"}


@dataclass(frozen=True)
class StatusTransitionCasFinding:
    path: str
    line: int
    column: int
    message: str


def _repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _python_files(paths: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix == ".py":
            files.append(path)
        elif path.is_dir():
            files.extend(child for child in path.rglob("*.py") if child.is_file())
    return sorted(path for path in files if "__pycache__" not in path.parts)


def _func_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _is_values_call(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Attribute) and node.func.attr == "values"


def _sets_status(node: ast.Call) -> bool:
    if any(keyword.arg == "status" for keyword in node.keywords):
        return True
    for arg in node.args:
        if not isinstance(arg, ast.Dict):
            continue
        for key in arg.keys:
            if isinstance(key, ast.Constant) and key.value == "status":
                return True
    return False


def _contains_update_call(node: ast.AST) -> bool:
    if isinstance(node, ast.Call):
        if _func_name(node.func) == "update":
            return True
        return _contains_update_call(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr == "update" or _contains_update_call(node.value)
    return False


def _finding_for_call(path: Path, node: ast.Call) -> StatusTransitionCasFinding | None:
    if path.resolve() in ALLOWED_FILES:
        return None
    if not _is_values_call(node) or not _sets_status(node):
        return None
    if not isinstance(node.func, ast.Attribute):
        return None
    if not _contains_update_call(node.func.value):
        return None
    return StatusTransitionCasFinding(
        path=_repo_relative(path),
        line=node.lineno,
        column=node.col_offset + 1,
        message="SQLAlchemy status UPDATE must use status_cas.py and application StatusTransition constants",
    )


def collect_findings(paths: tuple[Path, ...] = DEFAULT_PATHS) -> list[StatusTransitionCasFinding]:
    findings: list[StatusTransitionCasFinding] = []
    for path in _python_files(paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            finding = _finding_for_call(path, node)
            if finding is not None:
                findings.append(finding)
    return findings


def write_report(output: Path, findings: list[StatusTransitionCasFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "gate": "status_transition_cas",
        "baseline": {"max_violations": 0},
        "count": len(findings),
        "violations": [asdict(finding) for finding in findings],
        "gate_pass": not findings,
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[StatusTransitionCasFinding], output: Path) -> None:
    print("Status transition CAS gate failed:")
    for finding in findings:
        print(f"- {finding.path}:{finding.line}:{finding.column}: {finding.message}")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that repository status transitions use CAS helpers.")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    findings = collect_findings(tuple(args.paths) if args.paths else DEFAULT_PATHS)
    write_report(args.output, findings)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Status transition CAS gate passed: raw status UPDATEs are centralized.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
