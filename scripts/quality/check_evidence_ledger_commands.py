"""Validate command evidence recorded in the sprint evidence ledger.

The evidence ledger is useful only when its commands still point at real
package scripts, files, and pytest nodes. This gate checks command-shaped inline
code spans in `docs/sprint-evidence-ledger.md` so stale proof commands fail in
CI instead of quietly becoming decorative text.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = ROOT / "docs" / "sprint-evidence-ledger.md"
DEFAULT_PACKAGE = ROOT / "package.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "evidence_ledger_commands.json"

CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
PNPM_SCRIPT_RE = re.compile(r"\bpnpm\s+(?:--silent\s+)?(?!exec\b|dlx\b)([A-Za-z0-9:_-]+)")
PYTHON_SCRIPT_RE = re.compile(r"\b(?:uv\s+run\s+python|python)\s+([^\s;|&]+\.py)\b")
NODE_SCRIPT_RE = re.compile(r"\bnode\s+([^\s;|&]+\.(?:mjs|js))\b")
BASH_SCRIPT_RE = re.compile(r"\bbash\s+(?:-[A-Za-z]+\s+)?([^\s;|&]+)")
TEST_REF_RE = re.compile(r"\btests/[^\s;|&`'\")]+")
JsonObject = dict[str, Any]


@dataclass(frozen=True)
class EvidenceCommandFinding:
    code: str
    path: str
    line: int
    reference: str
    message: str


def _repo_relative(path: Path, *, root: Path = ROOT) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _load_json(path: Path) -> JsonObject:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{_repo_relative(path)} must be a JSON object")
    return payload


def collect_findings(
    root: Path = ROOT,
    *,
    ledger_path: Path = DEFAULT_LEDGER,
    package_path: Path = DEFAULT_PACKAGE,
) -> list[EvidenceCommandFinding]:
    ledger = _resolve_path(root, ledger_path)
    package = _resolve_path(root, package_path)
    missing = _missing_file_findings(root, ledger, package)
    if missing:
        return missing

    package_scripts = _package_scripts(package)
    test_names = _test_names_by_path(root)
    findings: list[EvidenceCommandFinding] = []
    for line_number, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), start=1):
        for command in _command_spans(line):
            findings.extend(_command_findings(ledger, line_number, command, package_scripts, test_names, root=root))
    return findings


def _resolve_path(root: Path, path: Path) -> Path:
    if not path.is_absolute():
        return root / path
    try:
        return root / path.relative_to(ROOT)
    except ValueError:
        return path


def _missing_file_findings(root: Path, *paths: Path) -> list[EvidenceCommandFinding]:
    findings: list[EvidenceCommandFinding] = []
    for path in paths:
        if path.exists():
            continue
        findings.append(
            _finding(
                "missing_required_file",
                path,
                1,
                _repo_relative(path, root=root),
                "Evidence ledger command gate cannot run without this file.",
                root=root,
            )
        )
    return findings


def _package_scripts(package_path: Path) -> set[str]:
    package = _load_json(package_path)
    scripts = package.get("scripts")
    if not isinstance(scripts, dict):
        return set()
    return {key for key in scripts if isinstance(key, str)}


def _command_spans(line: str) -> list[str]:
    commands: list[str] = []
    for match in CODE_SPAN_RE.finditer(line):
        value = match.group(1).strip()
        if _looks_like_command(value):
            commands.append(value)
    return commands


def _looks_like_command(value: str) -> bool:
    return any(marker in value for marker in ("pnpm ", "uv run ", "node ", "bash ", "pytest "))


def _command_findings(
    ledger_path: Path,
    line_number: int,
    command: str,
    package_scripts: set[str],
    test_names: dict[str, set[str]],
    *,
    root: Path,
) -> list[EvidenceCommandFinding]:
    findings: list[EvidenceCommandFinding] = []
    findings.extend(_pnpm_findings(ledger_path, line_number, command, package_scripts, root=root))
    findings.extend(_script_file_findings(ledger_path, line_number, command, root=root))
    findings.extend(_test_ref_findings(ledger_path, line_number, command, test_names, root=root))
    return findings


def _pnpm_findings(
    ledger_path: Path,
    line_number: int,
    command: str,
    package_scripts: set[str],
    *,
    root: Path,
) -> list[EvidenceCommandFinding]:
    findings: list[EvidenceCommandFinding] = []
    for match in PNPM_SCRIPT_RE.finditer(command):
        script_name = match.group(1)
        if script_name in package_scripts:
            continue
        findings.append(
            _finding(
                "missing_package_script",
                ledger_path,
                line_number,
                script_name,
                "Evidence ledger references a pnpm script that is not in package.json.",
                root=root,
            )
        )
    return findings


def _script_file_findings(
    ledger_path: Path,
    line_number: int,
    command: str,
    *,
    root: Path,
) -> list[EvidenceCommandFinding]:
    findings: list[EvidenceCommandFinding] = []
    for pattern, code, message in (
        (PYTHON_SCRIPT_RE, "missing_python_script", "Evidence ledger references a Python script that does not exist."),
        (NODE_SCRIPT_RE, "missing_node_script", "Evidence ledger references a Node script that does not exist."),
        (BASH_SCRIPT_RE, "missing_bash_script", "Evidence ledger references a bash script that does not exist."),
    ):
        for match in pattern.finditer(command):
            reference = _clean_shell_token(match.group(1))
            if _repo_path_exists(root, reference):
                continue
            findings.append(_finding(code, ledger_path, line_number, reference, message, root=root))
    return findings


def _test_ref_findings(
    ledger_path: Path,
    line_number: int,
    command: str,
    test_names: dict[str, set[str]],
    *,
    root: Path,
) -> list[EvidenceCommandFinding]:
    findings: list[EvidenceCommandFinding] = []
    for match in TEST_REF_RE.finditer(command):
        reference = _clean_shell_token(match.group(0))
        test_path, test_name = _split_test_ref(reference)
        if test_path not in test_names:
            findings.append(
                _finding(
                    "missing_test_path",
                    ledger_path,
                    line_number,
                    reference,
                    "Evidence ledger references a test path that does not exist.",
                    root=root,
                )
            )
            continue
        if not test_name or test_name in test_names[test_path]:
            continue
        findings.append(
            _finding(
                "missing_test_node",
                ledger_path,
                line_number,
                reference,
                "Evidence ledger references a pytest node that is not collectable from the file AST.",
                root=root,
            )
        )
    return findings


def _test_names_by_path(root: Path) -> dict[str, set[str]]:
    tests_root = root / "tests"
    names: dict[str, set[str]] = {}
    if not tests_root.exists():
        return names
    for path in sorted(tests_root.rglob("*")):
        if path.is_dir():
            names[_repo_relative(path, root=root)] = set()
            continue
        if not path.is_file():
            continue
        rel = _repo_relative(path, root=root)
        names[rel] = _python_test_names(path) if path.suffix == ".py" else set()
    return names


def _python_test_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    class_stack: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            class_stack.append(node.name)
            self.generic_visit(node)
            class_stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            _add_test_name(node.name)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            _add_test_name(node.name)

    def _add_test_name(name: str) -> None:
        if not name.startswith("test_"):
            return
        names.add(name)
        if class_stack:
            names.add(f"{class_stack[-1]}::{name}")

    Visitor().visit(tree)
    return names


def _split_test_ref(reference: str) -> tuple[str, str]:
    path, *node_parts = reference.split("::")
    test_name = node_parts[-1].split("[", maxsplit=1)[0] if node_parts else ""
    if len(node_parts) > 1:
        test_name = f"{node_parts[-2]}::{test_name}"
    return path, test_name


def _clean_shell_token(token: str) -> str:
    return token.strip().strip("'\"").rstrip(".,")


def _repo_path_exists(root: Path, reference: str) -> bool:
    if not reference or "$" in reference:
        return True
    return (root / reference).exists()


def _finding(
    code: str,
    path: Path,
    line: int,
    reference: str,
    message: str,
    *,
    root: Path,
) -> EvidenceCommandFinding:
    return EvidenceCommandFinding(
        code=code,
        path=_repo_relative(path, root=root),
        line=line,
        reference=reference,
        message=message,
    )


def write_report(output: Path, findings: list[EvidenceCommandFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "FAIL" if findings else "PASS",
        "count": len(findings),
        "findings": [asdict(finding) for finding in findings],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[EvidenceCommandFinding], output: Path) -> None:
    print("Evidence ledger command gate failed:")
    for finding in findings:
        print(f"- {finding.code} {finding.path}:{finding.line} [{finding.reference}]: {finding.message}")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check sprint evidence ledger command references.")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    findings = collect_findings(ledger_path=args.ledger, package_path=args.package)
    write_report(args.output, findings)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Evidence ledger command gate passed: proof commands point at existing scripts and tests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
