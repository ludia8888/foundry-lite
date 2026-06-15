"""Enforces guideline §4.2: Strategy/Specification rules need direct tests.

Foundry-lite uses small evaluator/specification functions for object filters
and action preconditions. Those rules must be testable without booting the API,
the CLI, or the full FoundryLite facade. This gate scans application code
for strategy/specification-shaped modules and requires a direct unit/property
test import for each one.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_DIR = ROOT / "libs" / "foundry_lite" / "application"
DEFAULT_TEST_DIRS = (ROOT / "tests",)
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "strategy_specification_tests.json"

CLASS_SUFFIXES = ("Strategy", "Specification")
TYPE_ALIAS_SUFFIXES = ("Evaluator", "Strategy", "Specification")
RULE_FUNCTION_PREFIXES = ("matches_", "evaluate_", "validate_", "precondition_")
RULE_TABLE_SUFFIXES = ("OPERATIONS", "EVALUATORS", "STRATEGIES", "SPECIFICATIONS")


@dataclass(frozen=True)
class StrategySpecCandidate:
    path: str
    module: str
    symbols: list[str]
    reasons: list[str]


@dataclass(frozen=True)
class StrategySpecFinding:
    code: str
    path: str
    module: str
    symbols: list[str]
    message: str


def _repo_relative(path: Path, *, root: Path = ROOT) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _python_files(source_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in source_dir.rglob("*.py")
        if path.is_file() and path.name != "__init__.py" and "__pycache__" not in path.parts
    )


def _module_name(path: Path, *, root: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = rel.parts[1:] if rel.parts and rel.parts[0] == "libs" else rel.parts
    return ".".join(parts)


def _target_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Tuple | ast.List):
        names: list[str] = []
        for child in target.elts:
            names.extend(_target_names(child))
        return names
    return []


def _assignment_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    if isinstance(node, ast.Assign):
        names: list[str] = []
        for target in node.targets:
            names.extend(_target_names(target))
        return names
    return _target_names(node.target)


def _is_rule_table(name: str, value: ast.expr | None) -> bool:
    return isinstance(value, ast.Dict) and name.endswith(RULE_TABLE_SUFFIXES)


def _class_candidate(node: ast.ClassDef) -> tuple[str, str] | None:
    if node.name.endswith(CLASS_SUFFIXES):
        return node.name, "strategy_or_specification_class"
    return None


def _function_candidate(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, str] | None:
    if node.name.startswith(RULE_FUNCTION_PREFIXES):
        return node.name, "rule_function"
    return None


def _type_alias_candidate(node: ast.TypeAlias) -> tuple[str, str] | None:
    if isinstance(node.name, ast.Name) and node.name.id.endswith(TYPE_ALIAS_SUFFIXES):
        return node.name.id, "strategy_or_specification_type_alias"
    return None


def _assignment_candidates(node: ast.Assign | ast.AnnAssign) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for name in _assignment_names(node):
        if name.endswith(TYPE_ALIAS_SUFFIXES):
            candidates.append((name, "strategy_or_specification_type_alias"))
        elif _is_rule_table(name, node.value):
            candidates.append((name, "evaluator_registry"))
    return candidates


def _node_candidates(node: ast.stmt) -> list[tuple[str, str]]:
    if isinstance(node, ast.ClassDef):
        candidate = _class_candidate(node)
        return [candidate] if candidate is not None else []
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        candidate = _function_candidate(node)
        return [candidate] if candidate is not None else []
    if isinstance(node, ast.TypeAlias):
        candidate = _type_alias_candidate(node)
        return [candidate] if candidate is not None else []
    if isinstance(node, ast.Assign | ast.AnnAssign):
        return _assignment_candidates(node)
    return []


def _candidate_symbols(tree: ast.Module) -> tuple[list[str], list[str]]:
    symbols: list[str] = []
    reasons: list[str] = []
    for node in tree.body:
        for symbol, reason in _node_candidates(node):
            symbols.append(symbol)
            reasons.append(reason)
    return sorted(set(symbols)), sorted(set(reasons))


def collect_candidates(
    *,
    source_dir: Path = DEFAULT_SOURCE_DIR,
    root: Path = ROOT,
) -> list[StrategySpecCandidate]:
    candidates: list[StrategySpecCandidate] = []
    for path in _python_files(source_dir):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        symbols, reasons = _candidate_symbols(tree)
        if not symbols:
            continue
        candidates.append(
            StrategySpecCandidate(
                path=_repo_relative(path, root=root),
                module=_module_name(path, root=root),
                symbols=symbols,
                reasons=reasons,
            )
        )
    return candidates


def _test_files(test_dirs: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for test_dir in test_dirs:
        if test_dir.is_file() and test_dir.name.startswith("test_") and test_dir.suffix == ".py":
            files.append(test_dir)
        elif test_dir.is_dir():
            files.extend(sorted(path for path in test_dir.rglob("test_*.py") if path.is_file()))
    return sorted(files)


def _has_test_function(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_") for node in tree.body
    )


def _plain_import_matches(node: ast.Import, module: str) -> bool:
    return any(alias.name == module for alias in node.names)


def _from_import_matches(node: ast.ImportFrom, candidate: StrategySpecCandidate) -> bool:
    if node.module == candidate.module:
        return True
    parent_module = candidate.module.rsplit(".", maxsplit=1)[0]
    module_basename = candidate.module.rsplit(".", maxsplit=1)[-1]
    imported_names = {alias.name for alias in node.names}
    return node.module == parent_module and (
        module_basename in imported_names or bool(set(candidate.symbols).intersection(imported_names))
    )


def _import_node_matches(node: ast.AST, candidate: StrategySpecCandidate) -> bool:
    if isinstance(node, ast.Import):
        return _plain_import_matches(node, candidate.module)
    if isinstance(node, ast.ImportFrom):
        return _from_import_matches(node, candidate)
    return False


def _imports_candidate(tree: ast.Module, candidate: StrategySpecCandidate) -> bool:
    return any(_import_node_matches(node, candidate) for node in ast.walk(tree))


def _candidate_has_direct_test(candidate: StrategySpecCandidate, test_files: list[Path]) -> bool:
    for test_file in test_files:
        tree = ast.parse(test_file.read_text(encoding="utf-8"), filename=str(test_file))
        if _has_test_function(tree) and _imports_candidate(tree, candidate):
            return True
    return False


def collect_findings(
    *,
    source_dir: Path = DEFAULT_SOURCE_DIR,
    test_dirs: tuple[Path, ...] = DEFAULT_TEST_DIRS,
    root: Path = ROOT,
) -> list[StrategySpecFinding]:
    test_files = _test_files(test_dirs)
    findings: list[StrategySpecFinding] = []
    for candidate in collect_candidates(source_dir=source_dir, root=root):
        if _candidate_has_direct_test(candidate, test_files):
            continue
        findings.append(
            StrategySpecFinding(
                code="strategy_specification_missing_direct_test",
                path=candidate.path,
                module=candidate.module,
                symbols=candidate.symbols,
                message="Strategy/Specification rule module has no direct unit/property test import",
            )
        )
    return findings


def write_report(
    output: Path,
    findings: list[StrategySpecFinding],
    *,
    source_dir: Path = DEFAULT_SOURCE_DIR,
    test_dirs: tuple[Path, ...] = DEFAULT_TEST_DIRS,
    root: Path = ROOT,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    candidates = collect_candidates(source_dir=source_dir, root=root)
    report = {
        "gate": "strategy_specification_tests",
        "baseline": 0,
        "candidate_count": len(candidates),
        "test_file_count": len(_test_files(test_dirs)),
        "count": len(findings),
        "candidates": [asdict(candidate) for candidate in candidates],
        "violations": [asdict(finding) for finding in findings],
        "gate_pass": not findings,
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[StrategySpecFinding], output: Path) -> None:
    print("Release gate blocked: Strategy/Specification rules need direct tests.")
    for finding in findings:
        symbols = ", ".join(finding.symbols)
        print(f"- {finding.path}: import {finding.module} or symbols ({symbols}) directly from a test module")
    print(f"Report: {_repo_relative(output)}")


def _resolve(path: Path, *, root: Path) -> Path:
    return path if path.is_absolute() else root / path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate direct tests for Strategy/Specification rule modules.")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--test-dir", type=Path, action="append", dest="test_dirs")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    source_dir = _resolve(args.source_dir, root=ROOT)
    test_dirs = tuple(_resolve(path, root=ROOT) for path in (args.test_dirs or DEFAULT_TEST_DIRS))
    findings = collect_findings(source_dir=source_dir, test_dirs=test_dirs)
    write_report(args.output, findings, source_dir=source_dir, test_dirs=test_dirs)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Strategy/Specification testability gate passed: rule modules have direct tests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
