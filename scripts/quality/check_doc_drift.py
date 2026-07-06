"""Enforces documentation drift rules: current implementation references must exist.

Foundry-lite separates future targets from current reality in
`docs/implementation-status.md`. This gate keeps that promise honest by scanning
repo documentation for inline code references and verifying that referenced
source files, scripts, API routes, pytest node ids, Markdown link targets,
Markdown heading anchors, Python classes, and
explicitly qualified methods exist. Future/negative wording such as
"not implemented yet" or "remain unextracted" is skipped so docs can still
describe known gaps without pretending they are done.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
SYMBOL_CHECK_DOCS = {"AGENTS.md", "docs/implementation-status.md"}
DEFAULT_CODE_ROOTS = (ROOT / "libs", ROOT / "apps", ROOT / "scripts")
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "doc_drift.json"

CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
PNPM_DOC_SCRIPT_RE = re.compile(
    r"\bpnpm\s+(?:--silent\s+)?"
    r"(?!install\b|add\b|remove\b|exec\b|dlx\b|run\b|workspace\b)"
    r"([A-Za-z0-9][A-Za-z0-9:_-]*)"
)
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")
MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
HTML_ANCHOR_RE = re.compile(r"<a\s+[^>]*(?:id|name)=[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)
PYTHON_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PYTHON_CLASS_RE = re.compile(r"^[A-Z][A-Za-z0-9_]*$")
PYTHON_QUALIFIED_METHOD_RE = re.compile(r"^([A-Z][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)$")
PACKAGE_SCRIPT_NAME_RE = re.compile(
    r"^(?:"
    r"(?:quality|ci|db|demo|diagnostics|sdk|test|web|worker):[A-Za-z0-9:_-]+"
    r"|dev|diagnostics|lint|test"
    r")$"
)
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
REPO_PATH_PREFIXES = {
    ".github",
    "apps",
    "docs",
    "examples",
    "infra",
    "libs",
    "migrations",
    "packages",
    "scripts",
    "tests",
}
GENERATED_OR_RUNTIME_ROOTS = {
    ".foundry-lite-ci-smoke",
    ".foundry-lite-demo",
    ".foundry-lite-diagnostics",
    "artifacts",
    "mutants",
    "node_modules",
}
DOC_SCAN_IGNORED_ROOTS = {
    ".claude",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "artifacts",
    "htmlcov",
    "mutants",
    "node_modules",
    "__pycache__",
}
EXTERNAL_OR_VALUE_SYMBOLS = {
    "ABAC",
    "AST",
    "AnnAssign",
    "Any",
    "CEL",
    "CSV",
    "DB",
    "DLQ",
    "DTO",
    "FastAPI",
    "Foundry",
    "GitHub",
    "Header",
    "HTTPException",
    "JSON",
    "JSONB",
    "JWT",
    "MemoryError",
    "None",
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
    "ValueError",
}
FUTURE_OR_NEGATIVE_MARKERS = {
    "not implemented",
    "not yet",
    "remain unextracted",
    "deferred",
    "future",
    "removed",
    "아직",
    "예정",
    "제외",
    "후보",
    "금지",
    "남은",
    "미래",
    "다시 넣지",
    "미구현",
    "삭제",
}
PROPOSED_COMMAND_HEADING_MARKERS = {
    "future",
    "future command",
    "proposed",
    "proposed command",
    "제안",
    "제안 명령",
}
COMMAND_FUTURE_MARKERS = {
    "future",
    "future work",
    "future scope",
    "planned",
    "proposed",
    "not active",
    "아직",
    "예정",
    "제안",
    "후속",
    "미래",
    "활성화한다",
}
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


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


@dataclass(frozen=True)
class DocumentationReferenceIndex:
    source_paths: list[Path]
    routes: set[str]
    test_names_by_path: dict[str, set[str]]
    symbols: PythonSymbolIndex
    package_scripts: set[str] | None


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


def _default_docs(root: Path) -> tuple[Path, ...]:
    docs: list[Path] = []
    for path in root.rglob("*.md"):
        if _is_ignored_doc_path(path, root=root):
            continue
        docs.append(path)
    docs_root = root / "docs"
    if docs_root.exists():
        docs.extend(sorted(path for path in docs_root.glob("*.json") if path.is_file()))
    return tuple(sorted(set(docs)))


def _is_ignored_doc_path(path: Path, *, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return any(part in DOC_SCAN_IGNORED_ROOTS or part.startswith(".foundry-lite") for part in parts)


def _all_source_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for path in root.rglob("*"):
        if any(part in DOC_SCAN_IGNORED_ROOTS for part in path.parts):
            continue
        if path.is_file() or path.is_dir():
            paths.append(path)
    return paths


def _build_reference_index(root: Path, code_roots: tuple[Path, ...]) -> DocumentationReferenceIndex:
    return DocumentationReferenceIndex(
        source_paths=_all_source_paths(root),
        routes=_fastapi_route_paths(root),
        test_names_by_path=_test_names_by_path(root),
        symbols=_build_python_symbol_index(code_roots),
        package_scripts=_package_scripts(root),
    )


def _package_scripts(root: Path) -> set[str] | None:
    package_path = root / "package.json"
    if not package_path.exists():
        return None
    payload = json.loads(package_path.read_text(encoding="utf-8"))
    scripts = payload.get("scripts")
    if not isinstance(scripts, dict):
        return set()
    return {name for name in scripts if isinstance(name, str)}


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


def _fastapi_route_paths(root: Path) -> set[str]:
    routes: set[str] = set()
    api_root = root / "apps" / "api"
    if not api_root.exists():
        return routes
    for path in sorted(api_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                routes.update(_routes_for_function(node))
    return routes


def _routes_for_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    routes: set[str] = set()
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
            continue
        if decorator.func.attr not in HTTP_METHODS or not decorator.args:
            continue
        first_arg = decorator.args[0]
        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
            routes.add(first_arg.value)
    return routes


def _test_names_by_path(root: Path) -> dict[str, set[str]]:
    tests_root = root / "tests"
    if not tests_root.exists():
        return {}
    names_by_path: dict[str, set[str]] = {}
    for path in sorted(tests_root.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names_by_path[_repo_relative(path, root=root)] = _test_names_from_tree(tree)
    return names_by_path


def _test_names_from_tree(tree: ast.AST) -> set[str]:
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


def _is_route_reference(reference: str) -> bool:
    return reference.startswith(("/api/", "/healthz", "/metrics"))


def _is_test_nodeid(reference: str) -> bool:
    return reference.startswith("tests/") and "::" in reference


def _is_non_repo_value(reference: str) -> bool:
    if "://" in reference or "=" in reference:
        return True
    if "(" in reference or ")" in reference:
        return True
    if re.fullmatch(r"\d+/\d+", reference):
        return True
    if reference.startswith("/") and not _is_route_reference(reference):
        return True
    return ":" in reference and not _is_test_nodeid(reference)


def _looks_like_path(reference: str) -> bool:
    if ("{" in reference or "}" in reference) and not _is_route_reference(reference):
        return False
    if _is_non_repo_value(reference):
        return False
    if "/" in reference:
        first_part = reference.lstrip("./").split("/", maxsplit=1)[0]
        return first_part in REPO_PATH_PREFIXES
    suffix = Path(reference).suffix
    return suffix in SOURCE_EXTENSIONS or reference.startswith(".")


def _looks_like_script_basename(reference: str) -> bool:
    if _is_non_repo_value(reference) or "{" in reference or "}" in reference:
        return False
    return reference.endswith((".py", ".sh", ".yml", ".yaml", ".toml", ".md")) or reference.startswith("check_")


@lru_cache(maxsize=8)
def _path_suffix_index(source_paths: tuple[Path, ...], root: Path) -> frozenset[str]:
    # Every "/"-aligned suffix of every repo-relative path, so reference
    # lookups are O(1) instead of rescanning all source paths per reference.
    suffixes: set[str] = set()
    for path in source_paths:
        parts = _repo_relative(path, root=root).split("/")
        for start in range(len(parts)):
            suffixes.add("/".join(parts[start:]))
    return frozenset(suffixes)


def _path_exists_by_suffix(reference: str, source_paths: list[Path], *, root: Path) -> bool:
    normalized = reference.strip("/")
    direct = root / normalized
    if direct.exists():
        return True
    suffixes = _path_suffix_index(tuple(source_paths), root)
    if normalized in suffixes:
        return True
    if reference.startswith("check_") and "." not in Path(reference).name:
        return f"{reference}.py" in suffixes
    return False


def _markdown_link_findings(
    doc_path: Path,
    line_number: int,
    line: str,
    anchor_cache: dict[Path, set[str]],
    *,
    root: Path,
) -> list[DocDriftFinding]:
    findings: list[DocDriftFinding] = []
    for raw_target in _markdown_link_targets(line):
        finding = _markdown_link_finding(doc_path, line_number, raw_target, anchor_cache, root=root)
        if finding is not None:
            findings.append(finding)
    return findings


def _markdown_link_targets(line: str) -> list[str]:
    targets: list[str] = []
    link_scan_line = CODE_SPAN_RE.sub("", line)
    for match in MARKDOWN_LINK_RE.finditer(link_scan_line):
        target = _normalise_markdown_target(match.group(1))
        if target is not None:
            targets.append(target)
    return targets


def _normalise_markdown_target(raw_target: str) -> str | None:
    target = raw_target.strip()
    if target.startswith("<") and ">" in target:
        target = target[1 : target.index(">")]
    else:
        target = target.split(maxsplit=1)[0]
    if not target or _is_external_markdown_target(target):
        return None
    return target


def _is_external_markdown_target(target: str) -> bool:
    if "://" in target:
        return True
    return target.startswith(("mailto:", "tel:", "javascript:"))


def _markdown_link_finding(
    doc_path: Path,
    line_number: int,
    target: str,
    anchor_cache: dict[Path, set[str]],
    *,
    root: Path,
) -> DocDriftFinding | None:
    path_part, anchor = _split_markdown_target(target)
    if path_part and _is_runtime_reference(path_part):
        return None
    target_path = _resolve_markdown_link_path(doc_path, path_part, root=root)
    reference = target if path_part else f"{_repo_relative(doc_path, root=root)}{target}"
    if target_path is None or not target_path.exists():
        return DocDriftFinding(
            code="doc_markdown_link_missing_target",
            path=_repo_relative(doc_path, root=root),
            line=line_number,
            reference=reference,
            message="Document has a Markdown link whose local target file does not exist",
        )
    return _markdown_anchor_finding(doc_path, line_number, reference, target_path, anchor, anchor_cache, root=root)


def _split_markdown_target(target: str) -> tuple[str, str]:
    path_part, separator, anchor = target.partition("#")
    return unquote(path_part), unquote(anchor) if separator else ""


def _resolve_markdown_link_path(doc_path: Path, path_part: str, *, root: Path) -> Path | None:
    if not path_part:
        return doc_path
    target_path = (doc_path.parent / path_part).resolve()
    try:
        target_path.relative_to(root.resolve())
    except ValueError:
        return None
    return target_path


def _markdown_anchor_finding(
    doc_path: Path,
    line_number: int,
    reference: str,
    target_path: Path,
    anchor: str,
    anchor_cache: dict[Path, set[str]],
    *,
    root: Path,
) -> DocDriftFinding | None:
    if not anchor or target_path.suffix != ".md":
        return None
    anchors = anchor_cache.setdefault(target_path, _markdown_heading_anchors(target_path))
    if _normalise_anchor(anchor) in anchors:
        return None
    return DocDriftFinding(
        code="doc_markdown_link_missing_anchor",
        path=_repo_relative(doc_path, root=root),
        line=line_number,
        reference=reference,
        message="Document has a Markdown link whose local heading anchor does not exist",
    )


def _markdown_heading_anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        anchors.update(_html_anchor_ids(line))
        match = MARKDOWN_HEADING_RE.match(line)
        if match is None:
            continue
        base = _heading_anchor(match.group(2))
        if not base:
            continue
        count = counts.get(base, 0)
        anchors.add(base if count == 0 else f"{base}-{count}")
        counts[base] = count + 1
    return anchors


def _html_anchor_ids(line: str) -> set[str]:
    return {_normalise_anchor(match.group(1)) for match in HTML_ANCHOR_RE.finditer(line)}


def _heading_anchor(heading: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", heading)
    text = re.sub(r"\[([^\]]+)]\([^)]+\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.strip().rstrip("#").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"\s+", "-", text).strip("-")


def _normalise_anchor(anchor: str) -> str:
    return _heading_anchor(anchor.replace("-", " "))


def _route_finding(
    doc_path: Path,
    line_number: int,
    reference: str,
    known_routes: set[str],
    *,
    root: Path,
) -> DocDriftFinding | None:
    if not _is_route_reference(reference) or reference in known_routes:
        return None
    return DocDriftFinding(
        code="doc_reference_missing_route",
        path=_repo_relative(doc_path, root=root),
        line=line_number,
        reference=reference,
        message="Document references an API route that FastAPI does not expose",
    )


def _test_nodeid_finding(
    doc_path: Path,
    line_number: int,
    reference: str,
    test_names_by_path: dict[str, set[str]],
    *,
    root: Path,
) -> DocDriftFinding | None:
    if not _is_test_nodeid(reference):
        return None
    test_path, test_name = _split_test_nodeid(reference)
    if test_path not in test_names_by_path:
        return DocDriftFinding(
            code="doc_reference_missing_test_file",
            path=_repo_relative(doc_path, root=root),
            line=line_number,
            reference=reference,
            message="Document references a pytest node id whose file does not exist",
        )
    if test_name and test_name not in test_names_by_path[test_path]:
        return DocDriftFinding(
            code="doc_reference_missing_test_name",
            path=_repo_relative(doc_path, root=root),
            line=line_number,
            reference=reference,
            message="Document references a pytest node id whose test is not collectable from the file AST",
        )
    return None


def _split_test_nodeid(reference: str) -> tuple[str, str]:
    path, *node_parts = reference.split("::")
    test_name = node_parts[-1].split("[", maxsplit=1)[0] if node_parts else ""
    if len(node_parts) > 1:
        test_name = f"{node_parts[-2]}::{test_name}"
    return path, test_name


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


def _references_in_line(line: str) -> list[str]:
    references: list[str] = []
    for match in CODE_SPAN_RE.finditer(line):
        reference = match.group(1).strip()
        if _should_skip_reference(reference):
            continue
        references.append(reference)
    return references


def _should_skip_reference(reference: str) -> bool:
    if not reference or " " in reference or "\t" in reference:
        return True
    return _is_glob_or_placeholder(reference)


def _package_script_reference_findings(
    doc_path: Path,
    line_number: int,
    line: str,
    current_heading: str,
    package_scripts: set[str] | None,
    *,
    root: Path,
) -> list[DocDriftFinding]:
    if package_scripts is None or _should_skip_package_script_line(line, current_heading):
        return []
    findings: list[DocDriftFinding] = []
    seen: set[tuple[str, int, int]] = set()
    for script, start, end in _package_script_references_in_line(line):
        identity = (script, start, end)
        if identity in seen:
            continue
        seen.add(identity)
        if _package_script_reference_is_future(line, start, end):
            continue
        if script in package_scripts:
            continue
        findings.append(
            DocDriftFinding(
                code="doc_reference_missing_package_script",
                path=_repo_relative(doc_path, root=root),
                line=line_number,
                reference=script,
                message="Document references a package.json script that does not exist in the current tree",
            )
        )
    return findings


def _should_skip_package_script_line(line: str, current_heading: str) -> bool:
    lowered_heading = current_heading.lower()
    if any(marker in lowered_heading for marker in PROPOSED_COMMAND_HEADING_MARKERS):
        return True
    return _line_is_future_or_negative(line)


def _package_script_references_in_line(line: str) -> list[tuple[str, int, int]]:
    references: list[tuple[str, int, int]] = []
    for match in PNPM_DOC_SCRIPT_RE.finditer(line):
        script = match.group(1)
        if _is_package_script_placeholder(script):
            continue
        references.append((script, match.start(1), match.end(1)))
    for match in CODE_SPAN_RE.finditer(line):
        reference = match.group(1).strip()
        if _is_package_script_placeholder(reference):
            continue
        if PACKAGE_SCRIPT_NAME_RE.match(reference):
            references.append((reference, match.start(1), match.end(1)))
    return references


def _is_package_script_placeholder(script: str) -> bool:
    return "*" in script or script.endswith(":")


def _package_script_reference_is_future(line: str, start: int, end: int) -> bool:
    return any(marker in _package_script_reference_context(line, start, end) for marker in COMMAND_FUTURE_MARKERS)


def _package_script_reference_context(line: str, start: int, end: int) -> str:
    segment_start = _last_index_of_any(line, (".", ";", "。", "；", "|"), before=start) + 1
    segment_end = _first_index_of_any(line, (".", ";", "。", "；", "|"), after=end)
    if segment_end == -1:
        segment_end = len(line)
    return line[segment_start:segment_end].lower()


def _last_index_of_any(text: str, separators: tuple[str, ...], *, before: int) -> int:
    return max(text.rfind(separator, 0, before) for separator in separators)


def _first_index_of_any(text: str, separators: tuple[str, ...], *, after: int) -> int:
    indexes = [index for separator in separators if (index := text.find(separator, after)) != -1]
    return min(indexes) if indexes else -1


def _reference_finding(
    doc_path: Path,
    line_number: int,
    line: str,
    reference: str,
    index: DocumentationReferenceIndex,
    *,
    should_check_symbols: bool,
    root: Path,
) -> DocDriftFinding | None:
    route_finding = _route_finding(doc_path, line_number, reference, index.routes, root=root)
    if route_finding is not None or _is_route_reference(reference):
        return route_finding
    test_finding = _test_nodeid_finding(doc_path, line_number, reference, index.test_names_by_path, root=root)
    if test_finding is not None or _is_test_nodeid(reference):
        return test_finding
    path_finding = _path_finding(doc_path, line_number, reference, index.source_paths, root=root)
    if path_finding is not None or _looks_like_path(reference):
        return path_finding
    return _symbol_reference_finding(doc_path, line_number, line, reference, index, should_check_symbols, root=root)


def _symbol_reference_finding(
    doc_path: Path,
    line_number: int,
    line: str,
    reference: str,
    index: DocumentationReferenceIndex,
    should_check_symbols: bool,
    *,
    root: Path,
) -> DocDriftFinding | None:
    if not should_check_symbols:
        return None
    if not PYTHON_IDENTIFIER_RE.match(reference.replace(".", "_")):
        return None
    return _symbol_finding(doc_path, line_number, line, reference, index.symbols, root=root)


def _findings_for_doc(
    doc_path: Path,
    index: DocumentationReferenceIndex,
    *,
    anchor_cache: dict[Path, set[str]],
    root: Path,
) -> list[DocDriftFinding]:
    findings: list[DocDriftFinding] = []
    should_check_symbols = _repo_relative(doc_path, root=root) in SYMBOL_CHECK_DOCS
    current_heading = ""
    for line_number, line in enumerate(doc_path.read_text(encoding="utf-8").splitlines(), start=1):
        heading_match = MARKDOWN_HEADING_RE.match(line)
        if heading_match is not None:
            current_heading = heading_match.group(2).strip()
        findings.extend(_markdown_link_findings(doc_path, line_number, line, anchor_cache, root=root))
        findings.extend(
            _package_script_reference_findings(
                doc_path,
                line_number,
                line,
                current_heading,
                index.package_scripts,
                root=root,
            )
        )
        if _line_is_future_or_negative(line) and not should_check_symbols:
            continue
        for reference in _references_in_line(line):
            path_finding = _reference_finding(
                doc_path,
                line_number,
                line,
                reference,
                index,
                should_check_symbols=should_check_symbols,
                root=root,
            )
            if path_finding is not None:
                findings.append(path_finding)
    return findings


def collect_findings(
    docs: tuple[Path, ...] | None = None,
    *,
    code_roots: tuple[Path, ...] = DEFAULT_CODE_ROOTS,
    root: Path = ROOT,
) -> list[DocDriftFinding]:
    docs_to_check = docs if docs is not None else _default_docs(root)
    index = _build_reference_index(root, code_roots)
    findings: list[DocDriftFinding] = []
    anchor_cache: dict[Path, set[str]] = {}
    for doc_path in docs_to_check:
        findings.extend(_findings_for_doc(doc_path, index, anchor_cache=anchor_cache, root=root))
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
    print(
        "Release gate blocked: documentation references missing code symbols, paths, "
        "package scripts, routes, tests, or links."
    )
    for finding in findings:
        print(f"- {finding.path}:{finding.line} {finding.code} `{finding.reference}`: {finding.message}")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate current-state documentation references against code.")
    parser.add_argument("docs", nargs="*", type=Path)
    parser.add_argument("--code-root", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    docs = tuple(path if path.is_absolute() else ROOT / path for path in args.docs) if args.docs else None
    code_roots = tuple(path if path.is_absolute() else ROOT / path for path in args.code_root) or DEFAULT_CODE_ROOTS
    findings = collect_findings(docs, code_roots=code_roots)
    docs_for_report = docs if docs is not None else _default_docs(ROOT)
    write_report(args.output, findings, docs=docs_for_report)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print(
        "Document drift gate passed: docs reference existing code paths, package scripts, routes, "
        "tests, symbols, and links."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
