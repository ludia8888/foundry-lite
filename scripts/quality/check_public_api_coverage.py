from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class PublicCallable:
    file: Path
    name: str
    start: int
    end: int


def _is_public(name: str) -> bool:
    return not name.startswith("_")


def collect_public_callables(path: Path) -> list[PublicCallable]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return _public_module_functions(path, tree) + _public_class_methods(path, tree)


def _public_module_functions(path: Path, tree: ast.AST) -> list[PublicCallable]:
    if not isinstance(tree, ast.Module):
        return []
    return [
        _callable_record(path, node.name, node)
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _is_public(node.name)
    ]


def _public_class_methods(path: Path, tree: ast.AST) -> list[PublicCallable]:
    methods: list[PublicCallable] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and _is_public(node.name):
            methods.extend(_public_methods_for_class(path, node))
    return methods


def _public_methods_for_class(path: Path, node: ast.ClassDef) -> list[PublicCallable]:
    return [
        _callable_record(path, f"{node.name}.{child.name}", child)
        for child in node.body
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) and _is_public(child.name)
    ]


def _callable_record(
    path: Path,
    name: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> PublicCallable:
    return PublicCallable(path, name, node.lineno, node.end_lineno or node.lineno)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("coverage_json", type=Path)
    parser.add_argument("--threshold", type=float, default=95.0)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "quality" / "public_api_coverage.json")
    args = parser.parse_args(argv)

    coverage = json.loads(args.coverage_json.read_text(encoding="utf-8"))
    files = coverage["files"]
    callables: list[PublicCallable] = []
    for path in sorted((ROOT / "libs" / "foundry_lite").rglob("*.py")):
        if "__pycache__" not in path.parts:
            callables.extend(collect_public_callables(path))

    covered: list[dict[str, object]] = []
    missed: list[dict[str, object]] = []
    for item in callables:
        key = str(item.file.relative_to(ROOT))
        executed = set(files.get(key, {}).get("executed_lines", []))
        body_lines = set(range(item.start, item.end + 1))
        record = {"file": key, "name": item.name, "start": item.start, "end": item.end}
        if executed & body_lines:
            covered.append(record)
        else:
            missed.append(record)

    percent = 100.0 if not callables else len(covered) / len(callables) * 100
    report = {
        "threshold": args.threshold,
        "percent": round(percent, 2),
        "covered": covered,
        "missed": missed,
        "total": len(callables),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    if percent < args.threshold:
        print(f"Public callable smoke coverage {percent:.2f}% is below {args.threshold:.2f}%")
        for missed_item in missed:
            print(f"- {missed_item['file']}:{missed_item['start']} {missed_item['name']}")
        return 1
    print(f"Public callable smoke coverage {percent:.2f}% >= {args.threshold:.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
