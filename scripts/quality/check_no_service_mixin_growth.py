"""Enforces guideline §4.2: no new service mixin inheritance debt.

Existing mixins are allowlisted as known refactor debt. New service or facade
mixin classes must be converted into explicit use-case services instead.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIRS = (
    ROOT / "libs" / "foundry_lite" / "application" / "services",
    ROOT / "libs" / "foundry_lite" / "application" / "facades",
)
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "no_service_mixin_growth.json"
ALLOWED_MIXIN_CLASSES = frozenset(
    {
        "OperationsConsoleBackupMixin",
        "RuntimeObservabilityMixin",
    }
)


@dataclass(frozen=True)
class MixinFinding:
    code: str
    path: str
    line: int
    class_name: str
    message: str


def collect_findings(source_dirs: tuple[Path, ...] = SOURCE_DIRS) -> list[MixinFinding]:
    findings: list[MixinFinding] = []
    for path in _python_files(source_dirs):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                findings.extend(_class_findings(path, node))
    return findings


def _python_files(source_dirs: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for source_dir in source_dirs:
        files.extend(path for path in source_dir.rglob("*.py") if "__pycache__" not in path.parts)
    return sorted(files)


def _class_findings(path: Path, node: ast.ClassDef) -> list[MixinFinding]:
    names = [node.name, *(_base_name(base) for base in node.bases)]
    mixins = sorted(name for name in names if name.endswith("Mixin") and name not in ALLOWED_MIXIN_CLASSES)
    return [
        MixinFinding(
            code="new_service_mixin_debt",
            path=_display_path(path),
            line=node.lineno,
            class_name=node.name,
            message=f"mixin debt is not allowlisted: {name}",
        )
        for name in mixins
    ]


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _base_name(node.value)
    return ""


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _write_report(output: Path, findings: list[MixinFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "gate": "no_service_mixin_growth",
                "allowedMixins": sorted(ALLOWED_MIXIN_CLASSES),
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
            print(f"{finding.path}:{finding.line}: {finding.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
