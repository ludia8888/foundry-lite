"""Enforces guideline §4.2: domain rule modules need direct unit tests.

Domain rules are the business invariants that services orchestrate. A new
domain rule file must have at least one direct test import so the rule can be
understood without booting the API, facade, or infrastructure.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOMAIN_ROOT = ROOT / "libs" / "foundry_lite" / "domain"
TEST_ROOT = ROOT / "tests"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "domain_rule_tests.json"
EXCLUDED_FILES = {"__init__.py", "context.py", "errors.py"}


@dataclass(frozen=True)
class DomainRuleCandidate:
    path: str
    module: str


@dataclass(frozen=True)
class DomainRuleFinding:
    code: str
    path: str
    module: str
    message: str


def collect_candidates(domain_root: Path = DOMAIN_ROOT) -> list[DomainRuleCandidate]:
    candidates: list[DomainRuleCandidate] = []
    for path in sorted(domain_root.rglob("*.py")):
        if path.name in EXCLUDED_FILES or "__pycache__" in path.parts:
            continue
        candidates.append(DomainRuleCandidate(path=_display_path(path), module=_module_name(path, domain_root)))
    return candidates


def collect_findings(
    domain_root: Path = DOMAIN_ROOT,
    test_root: Path = TEST_ROOT,
) -> list[DomainRuleFinding]:
    test_trees = _test_trees(test_root)
    findings: list[DomainRuleFinding] = []
    for candidate in collect_candidates(domain_root):
        if not any(_imports_module(tree, candidate.module) for tree in test_trees):
            findings.append(
                DomainRuleFinding(
                    code="missing_domain_rule_test",
                    path=candidate.path,
                    module=candidate.module,
                    message="domain rule module has no direct unit test import",
                )
            )
    return findings


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _module_name(path: Path, domain_root: Path) -> str:
    rel = path.relative_to(domain_root).with_suffix("")
    return ".".join(("foundry_lite", "domain", *rel.parts))


def _test_trees(test_root: Path) -> list[ast.Module]:
    return [
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in sorted(test_root.rglob("test_*.py"))
        if path.is_file()
    ]


def _imports_module(tree: ast.Module, module: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(alias.name == module for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == module:
            return True
    return False


def _write_report(output: Path, findings: list[DomainRuleFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "gate": "domain_rule_tests",
                "violations": len(findings),
                "findings": [asdict(finding) for finding in findings],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    findings = collect_findings()
    _write_report(args.output, findings)
    if findings:
        for finding in findings:
            print(f"{finding.path}: {finding.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
