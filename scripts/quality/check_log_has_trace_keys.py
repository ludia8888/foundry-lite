"""Enforces guideline §8.3/§12.1: logs carry trace correlation keys.

Logs without request_id, tenant_id, or run_id-family fields become isolated
messages that cannot be joined back to traces, audit rows, or API errors. This
gate blocks direct logger calls and structured log_event calls that omit those
keys.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHS = (ROOT / "libs", ROOT / "apps", ROOT / "scripts")
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "log_trace_keys.json"
SKIPPED_FILES = {ROOT / "libs" / "foundry_lite" / "observability" / "logging.py"}
LOGGER_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
TRACE_KEYS = {
    "request_id",
    "tenant_id",
    "run_id",
    "trace_id",
    "correlation_id",
    "sync_run_id",
    "transform_run_id",
    "index_run_id",
    "action_run_id",
    "materialization_run_id",
    "dataset_version_id",
    "transaction_id",
    "object_id",
}


@dataclass(frozen=True)
class LogTraceFinding:
    code: str
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
            files.extend(sorted(child for child in path.rglob("*.py") if child.is_file()))
    return sorted(path for path in files if path.resolve() not in SKIPPED_FILES and "__pycache__" not in path.parts)


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    return None


def _string_value(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _name_mentions_trace_key(value: str) -> bool:
    return any(key == value or key in value for key in TRACE_KEYS)


def _trace_candidate_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.keyword):
        return node.arg
    return _string_value(node)


def _ast_node_mentions_trace_key(node: ast.AST) -> bool:
    candidate = _trace_candidate_name(node)
    return candidate is not None and _name_mentions_trace_key(candidate)


def _node_mentions_trace_key(node: ast.AST | None) -> bool:
    if node is None:
        return False
    return any(_ast_node_mentions_trace_key(child) for child in ast.walk(node))


def _keyword_value(call: ast.Call, keyword_name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == keyword_name:
            return keyword.value
    return None


def _call_has_trace_key(call: ast.Call) -> bool:
    return _node_mentions_trace_key(call)


def _is_log_event_call(call: ast.Call) -> bool:
    call_name = _call_name(call.func)
    return call_name == "log_event" or bool(call_name and call_name.endswith(".log_event"))


def _receiver_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _receiver_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    return None


def _is_logger_method_call(call: ast.Call) -> bool:
    if not isinstance(call.func, ast.Attribute) or call.func.attr not in LOGGER_METHODS:
        return False
    receiver = _receiver_name(call.func.value)
    if receiver is None:
        return False
    return receiver == "logging" or receiver.endswith(".logging") or "logger" in receiver.lower()


def _log_event_findings(path: Path, call: ast.Call) -> list[LogTraceFinding]:
    if _call_has_trace_key(call):
        return []
    return [
        LogTraceFinding(
            code="log_event_missing_trace_key",
            path=_repo_relative(path),
            line=call.lineno,
            column=call.col_offset + 1,
            message="log_event calls must include request_id, tenant_id, or a run_id-family field",
        )
    ]


def _logger_call_findings(path: Path, call: ast.Call) -> list[LogTraceFinding]:
    if _node_mentions_trace_key(_keyword_value(call, "extra")) or _call_has_trace_key(call):
        return []
    call_name = _call_name(call.func) or "logger call"
    return [
        LogTraceFinding(
            code="logger_call_missing_trace_key",
            path=_repo_relative(path),
            line=call.lineno,
            column=call.col_offset + 1,
            message=f"{call_name} must include request_id, tenant_id, or a run_id-family field",
        )
    ]


def _findings_for_file(path: Path) -> list[LogTraceFinding]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    findings: list[LogTraceFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _is_log_event_call(node):
            findings.extend(_log_event_findings(path, node))
        elif _is_logger_method_call(node):
            findings.extend(_logger_call_findings(path, node))
    return findings


def collect_findings(paths: tuple[Path, ...] = DEFAULT_PATHS) -> list[LogTraceFinding]:
    findings: list[LogTraceFinding] = []
    for path in _python_files(paths):
        findings.extend(_findings_for_file(path))
    return findings


def write_report(output: Path, findings: list[LogTraceFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "count": len(findings),
        "baseline": 0,
        "gate_pass": not findings,
        "violations": [asdict(finding) for finding in findings],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[LogTraceFinding], output: Path) -> None:
    print("Log trace key gate failed:")
    for finding in findings:
        print(f"- {finding.path}:{finding.line}:{finding.column} {finding.code}: {finding.message}")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate logs preserve trace correlation keys.")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    findings = collect_findings(tuple(args.paths) if args.paths else DEFAULT_PATHS)
    write_report(args.output, findings)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Log trace key gate passed: structured logs keep trace correlation keys.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
