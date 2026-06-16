"""Enforces AGENTS.md/docs drift rules: current implementation references must exist.

Foundry-lite separates future targets from current reality in
`docs/implementation-status.md`. This gate keeps that promise honest by scanning
current-state docs for inline code references and verifying that referenced
source files, scripts, Python classes, and explicitly qualified methods exist.
Future/negative wording such as "not implemented yet" or "remain unextracted"
is skipped so docs can still describe known gaps without pretending they are
done.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOCS = (ROOT / "AGENTS.md", ROOT / "docs" / "implementation-status.md")
DEFAULT_CODE_ROOTS = (ROOT / "libs", ROOT / "apps", ROOT / "scripts")
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "doc_drift.json"

CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
PYTHON_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PYTHON_CLASS_RE = re.compile(r"^[A-Z][A-Za-z0-9_]*$")
PYTHON_QUALIFIED_METHOD_RE = re.compile(r"^([A-Z][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)$")
SOURCE_EXTENSIONS = {
    ".cfg",
    ".json",
    ".md",
    ".py",
    ".ql",
    ".sh",
    ".toml",
    ".yml",
    ".yaml",
}
GENERATED_OR_RUNTIME_ROOTS = {
    ".foundry-lite-ci-smoke",
    ".foundry-lite-demo",
    ".foundry-lite-diagnostics",
    "artifacts",
    "mutants",
    "node_modules",
}
EXTERNAL_OR_VALUE_SYMBOLS = {
    "ABAC",
    "AST",
    "CEL",
    "CSV",
    "DB",
    "DLQ",
    "DTO",
    "FastAPI",
    "Foundry",
    "GitHub",
    "Header",
    "JSON",
    "JSONB",
    "JWT",
    "OIDC",
    "OpenLineage",
    "Elasticsearch",
    "PostgreSQL",
    "Protocol",
    "RBAC",
    "SARIF",
    "SDK",
    "SQLAlchemy",
    "Temporal",
    "Typer",
}
FUTURE_OR_NEGATIVE_MARKERS = {
    "not implemented",
    "not yet",
    "remain unextracted",
    "removed",
    "아직",
    "예정",
    "제외",
    "후보",
    "금지",
    "남은",
    "다시 넣지",
    "미구현",
    "삭제",
}


@dataclass(frozen=True)
class DocDriftFinding:
    code: str
    path: str
    line: int
    reference: str
    message: str


@dataclass(frozen=True)
class PythonSymbolIndex:
    classes: set[str]
    functions: set[str]
    class_methods: dict[str, set[str]]


def _repo_relative(path: Path, *, root: Path = ROOT) -> str:
    try:
        return str(path.relative_to(root))
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


def _all_source_paths(root: Path) -> list[Path]:
    ignored = {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", "node_modules", "artifacts"}
    paths: list[Path] = []
    for path in root.rglob("*"):
        if any(part in ignored for part in path.parts):
            continue
        if path.is_file() or path.is_dir():
            paths.append(path)
    return paths


def _build_python_symbol_index(code_roots: tuple[Path, ...]) -> PythonSymbolIndex:
    classes: set[str] = set()
    functions: set[str] = set()
    class_methods: dict[str, set[str]] = {}
    for path in _python_files(code_roots):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classes.add(node.name)
                methods = {
                    child.name for child in node.body if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                }
                class_methods.setdefault(node.name, set()).update(methods)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                functions.add(node.name)
    return PythonSymbolIndex(classes=classes, functions=functions, class_methods=class_methods)


def _line_is_future_or_negative(line: str) -> bool:
    lowered = line.lower()
    return any(marker in lowered for marker in FUTURE_OR_NEGATIVE_MARKERS)


def _is_glob_or_placeholder(reference: str) -> bool:
    return "*" in reference or "<" in reference or ">" in reference or "..." in reference


def _is_bare_extension(reference: str) -> bool:
    return bool(re.fullmatch(r"\.[A-Za-z0-9]+", reference))


def _is_runtime_reference(reference: str) -> bool:
    normalized = reference.strip().rstrip("/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    first_part = normalized.split("/", maxsplit=1)[0]
    return first_part in GENERATED_OR_RUNTIME_ROOTS


def _looks_like_path(reference: str) -> bool:
    if "/" in reference:
        return True
    suffix = Path(reference).suffix
    return suffix in SOURCE_EXTENSIONS or reference.startswith(".")


def _looks_like_script_basename(reference: str) -> bool:
    return reference.endswith((".py", ".sh", ".yml", ".yaml", ".toml", ".md")) or reference.startswith("check_")


def _path_exists_by_suffix(reference: str, source_paths: list[Path], *, root: Path) -> bool:
    normalized = reference.strip("/")
    direct = root / normalized
    if direct.exists():
        return True
    for path in source_paths:
        rel = _repo_relative(path, root=root)
        if rel == normalized or rel.endswith(f"/{normalized}"):
            return True
        if path.name == normalized:
            return True
    if reference.startswith("check_") and "." not in Path(reference).name:
        script_name = f"{reference}.py"
        return any(path.name == script_name for path in source_paths)
    return False


def _path_finding(
    doc_path: Path,
    line_number: int,
    reference: str,
    source_paths: list[Path],
    *,
    root: Path,
) -> DocDriftFinding | None:
    if _is_glob_or_placeholder(reference) or _is_bare_extension(reference) or _is_runtime_reference(reference):
        return None
    if not (_looks_like_path(reference) or _looks_like_script_basename(reference)):
        return None
    if _path_exists_by_suffix(reference, source_paths, root=root):
        return None
    return DocDriftFinding(
        code="doc_reference_missing_path",
        path=_repo_relative(doc_path, root=root),
        line=line_number,
        reference=reference,
        message="Document references a source path/script that does not exist in the current tree",
    )


def _qualified_method_finding(
    doc_path: Path,
    line_number: int,
    reference: str,
    symbols: PythonSymbolIndex,
    *,
    root: Path,
) -> DocDriftFinding | None:
    method_match = PYTHON_QUALIFIED_METHOD_RE.match(reference)
    if method_match is None:
        return None
    class_name, method_name = method_match.groups()
    if method_name.startswith("__"):
        return None
    if class_name not in symbols.classes:
        return DocDriftFinding(
            code="doc_reference_missing_class",
            path=_repo_relative(doc_path, root=root),
            line=line_number,
            reference=reference,
            message="Document references a Python class that does not exist in the current tree",
        )
    if method_name in symbols.class_methods.get(class_name, set()):
        return None
    return DocDriftFinding(
        code="doc_reference_missing_method",
        path=_repo_relative(doc_path, root=root),
        line=line_number,
        reference=reference,
        message="Document references a Python class method that does not exist in the current tree",
    )


def _simple_symbol_finding(
    doc_path: Path,
    line_number: int,
    reference: str,
    symbols: PythonSymbolIndex,
    *,
    root: Path,
) -> DocDriftFinding | None:
    if not PYTHON_CLASS_RE.match(reference):
        return None
    if reference in EXTERNAL_OR_VALUE_SYMBOLS or reference.isupper():
        return None
    if reference in symbols.classes or reference in symbols.functions:
        return None
    return DocDriftFinding(
        code="doc_reference_missing_symbol",
        path=_repo_relative(doc_path, root=root),
        line=line_number,
        reference=reference,
        message="Document references a Python symbol that does not exist in the current tree",
    )


def _symbol_finding(
    doc_path: Path,
    line_number: int,
    line: str,
    reference: str,
    symbols: PythonSymbolIndex,
    *,
    root: Path,
) -> DocDriftFinding | None:
    if _line_is_future_or_negative(line):
        return None
    method_finding = _qualified_method_finding(doc_path, line_number, reference, symbols, root=root)
    if method_finding is not None:
        return method_finding
    return _simple_symbol_finding(doc_path, line_number, reference, symbols, root=root)


def _findings_for_doc(
    doc_path: Path,
    source_paths: list[Path],
    symbols: PythonSymbolIndex,
    *,
    root: Path,
) -> list[DocDriftFinding]:
    findings: list[DocDriftFinding] = []
    for line_number, line in enumerate(doc_path.read_text(encoding="utf-8").splitlines(), start=1):
        for match in CODE_SPAN_RE.finditer(line):
            reference = match.group(1).strip()
            if not reference or " " in reference or "\t" in reference:
                continue
            path_finding = _path_finding(doc_path, line_number, reference, source_paths, root=root)
            if path_finding is not None:
                findings.append(path_finding)
                continue
            if _looks_like_path(reference):
                continue
            if not PYTHON_IDENTIFIER_RE.match(reference.replace(".", "_")):
                continue
            symbol_finding = _symbol_finding(doc_path, line_number, line, reference, symbols, root=root)
            if symbol_finding is not None:
                findings.append(symbol_finding)
    return findings


def collect_findings(
    docs: tuple[Path, ...] = DEFAULT_DOCS,
    *,
    code_roots: tuple[Path, ...] = DEFAULT_CODE_ROOTS,
    root: Path = ROOT,
) -> list[DocDriftFinding]:
    source_paths = _all_source_paths(root)
    symbols = _build_python_symbol_index(code_roots)
    findings: list[DocDriftFinding] = []
    for doc_path in docs:
        findings.extend(_findings_for_doc(doc_path, source_paths, symbols, root=root))
    return findings


def write_report(output: Path, findings: list[DocDriftFinding], *, docs: tuple[Path, ...]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "gate": "doc_drift",
        "baseline": {"max_violations": 0},
        "count": len(findings),
        "docs": [_repo_relative(path) for path in docs],
        "violations": [asdict(finding) for finding in findings],
        "gate_pass": not findings,
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[DocDriftFinding], output: Path) -> None:
    print("Release gate blocked: current-state docs reference missing code symbols or paths.")
    for finding in findings:
        print(f"- {finding.path}:{finding.line} {finding.code} `{finding.reference}`: {finding.message}")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate current-state documentation references against code.")
    parser.add_argument("docs", nargs="*", type=Path, default=list(DEFAULT_DOCS))
    parser.add_argument("--code-root", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    docs = tuple(path if path.is_absolute() else ROOT / path for path in args.docs)
    code_roots = tuple(path if path.is_absolute() else ROOT / path for path in args.code_root) or DEFAULT_CODE_ROOTS
    findings = collect_findings(docs, code_roots=code_roots)
    write_report(args.output, findings, docs=docs)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Document drift gate passed: current-state docs reference existing code paths and symbols.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
