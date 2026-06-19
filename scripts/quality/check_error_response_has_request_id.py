"""Enforces guideline §6.3/§8.3: API error responses carry request_id.

Operators can only connect a user-facing API error to traces, logs, and audit
events when the response includes the same request_id. This gate keeps FastAPI
HTTPException construction and the shared _handle_error helper from dropping
that correlation key.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHS = (ROOT / "apps" / "api",)
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "error_response_request_id.json"
REQUEST_ID_KEYS = {"request_id", "requestId"}


@dataclass(frozen=True)
class ErrorResponseFinding:
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
    return sorted(files)


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _keyword_value(call: ast.Call, keyword_name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == keyword_name:
            return keyword.value
    return None


def _is_http_exception_call(call: ast.Call) -> bool:
    call_name = _call_name(call.func)
    return call_name == "HTTPException" or bool(call_name and call_name.endswith(".HTTPException"))


def _string_value(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _dict_has_request_id_key(node: ast.Dict) -> bool:
    for key in node.keys:
        if key is not None and _string_value(key) in REQUEST_ID_KEYS:
            return True
    return False


def _node_mentions_request_id(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in REQUEST_ID_KEYS:
            return True
        if isinstance(child, ast.Attribute) and child.attr in REQUEST_ID_KEYS:
            return True
        value = _string_value(child)
        if value is not None and any(key in value for key in REQUEST_ID_KEYS):
            return True
    return False


def _detail_has_request_id(detail: ast.expr | None) -> bool:
    if detail is None:
        return False
    if isinstance(detail, ast.Dict):
        return _dict_has_request_id_key(detail) or _node_mentions_request_id(detail)
    return _node_mentions_request_id(detail)


def _http_exception_findings(path: Path, tree: ast.Module) -> list[ErrorResponseFinding]:
    findings: list[ErrorResponseFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_http_exception_call(node):
            continue
        if _detail_has_request_id(_keyword_value(node, "detail")):
            continue
        findings.append(
            ErrorResponseFinding(
                code="http_exception_missing_request_id",
                path=_repo_relative(path),
                line=node.lineno,
                column=node.col_offset + 1,
                message="HTTPException detail must include request_id for trace/audit correlation",
            )
        )
    return findings


def _is_none(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _handle_error_has_request(call: ast.Call) -> bool:
    if len(call.args) >= 2 and not _is_none(call.args[1]):
        return True
    request_keyword = _keyword_value(call, "request")
    return request_keyword is not None and not _is_none(request_keyword)


def _handle_error_findings(path: Path, tree: ast.Module) -> list[ErrorResponseFinding]:
    findings: list[ErrorResponseFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node.func) != "_handle_error":
            continue
        if _handle_error_has_request(node):
            continue
        findings.append(
            ErrorResponseFinding(
                code="handle_error_missing_request",
                path=_repo_relative(path),
                line=node.lineno,
                column=node.col_offset + 1,
                message="_handle_error must receive the current request so response detail includes request_id",
            )
        )
    return findings


def _module_defines_fastapi_app(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and _call_name(node.value.func) == "FastAPI"
        ):
            return True
    return False


def _is_validation_exception_handler(decorator: ast.expr) -> bool:
    if not isinstance(decorator, ast.Call):
        return False
    decorator_name = _call_name(decorator.func)
    if decorator_name is None or not decorator_name.endswith("exception_handler"):
        return False
    return any(_call_name(arg) == "RequestValidationError" for arg in decorator.args)


def _validation_handler_findings(path: Path, tree: ast.Module) -> list[ErrorResponseFinding]:
    if not _module_defines_fastapi_app(tree):
        return []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(_is_validation_exception_handler(decorator) for decorator in node.decorator_list):
            continue
        if _node_mentions_request_id(node):
            return []
        return [
            ErrorResponseFinding(
                code="validation_handler_missing_request_id",
                path=_repo_relative(path),
                line=node.lineno,
                column=node.col_offset + 1,
                message="RequestValidationError handler must include request_id in validation error detail",
            )
        ]
    return [
        ErrorResponseFinding(
            code="validation_handler_missing",
            path=_repo_relative(path),
            line=1,
            column=1,
            message="FastAPI apps must register a RequestValidationError handler that preserves request_id",
        )
    ]


def collect_findings(paths: tuple[Path, ...] = DEFAULT_PATHS) -> list[ErrorResponseFinding]:
    findings: list[ErrorResponseFinding] = []
    for path in _python_files(paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        findings.extend(_http_exception_findings(path, tree))
        findings.extend(_handle_error_findings(path, tree))
        findings.extend(_validation_handler_findings(path, tree))
    return findings


def write_report(output: Path, findings: list[ErrorResponseFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "count": len(findings),
        "baseline": 0,
        "gate_pass": not findings,
        "violations": [asdict(finding) for finding in findings],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[ErrorResponseFinding]) -> None:
    print("Error response request_id gate failed:")
    for finding in findings:
        print(f"- {finding.path}:{finding.line}:{finding.column} {finding.code}: {finding.message}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate API error responses preserve request_id.")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    findings = collect_findings(tuple(args.paths) if args.paths else DEFAULT_PATHS)
    write_report(args.output, findings)
    if findings:
        _print_failure(findings)
        return 1
    print("Error response request_id gate passed: API errors keep request_id in response detail.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
