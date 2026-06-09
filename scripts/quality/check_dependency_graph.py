from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict, cast

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = [ROOT / "libs", ROOT / "apps", ROOT / "scripts"]
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality"


@dataclass(frozen=True)
class ModuleInfo:
    name: str
    path: Path
    imports: tuple[str, ...]


class CouplingModule(TypedDict):
    module: str
    fan_in: int
    fan_out: int
    depends_on: list[str]


def module_name(path: Path) -> str:
    relative = path.relative_to(ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[0] == "libs":
        parts = parts[1:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imported_module_names(tree: ast.AST) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def collect_modules() -> dict[str, ModuleInfo]:
    modules: dict[str, ModuleInfo] = {}
    for root in SOURCE_ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if any(part in {".venv", "__pycache__"} for part in path.parts):
                continue
            name = module_name(path)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            modules[name] = ModuleInfo(name=name, path=path, imports=tuple(sorted(imported_module_names(tree))))
    return modules


def internal_edges(modules: dict[str, ModuleInfo]) -> dict[str, list[str]]:
    names = set(modules)
    edges: dict[str, list[str]] = {}
    for name, info in modules.items():
        linked: set[str] = set()
        for imported in info.imports:
            for candidate in names:
                if imported == candidate or imported.startswith(f"{candidate}."):
                    linked.add(candidate)
        edges[name] = sorted(linked - {name})
    return edges


def find_cycles(edges: dict[str, list[str]]) -> list[list[str]]:
    cycles: list[list[str]] = []
    stack: list[str] = []
    state: dict[str, str] = {}

    def visit(node: str) -> None:
        state[node] = "visiting"
        stack.append(node)
        for child in edges[node]:
            if state.get(child) == "visiting":
                start = stack.index(child)
                cycles.append(stack[start:] + [child])
            elif state.get(child) is None:
                visit(child)
        stack.pop()
        state[node] = "done"

    for node in edges:
        if state.get(node) is None:
            visit(node)
    return cycles


def layer_violations(edges: dict[str, list[str]]) -> list[str]:
    rules = {
        "foundry_lite.domain": (
            "foundry_lite.application",
            "foundry_lite.infrastructure",
            "foundry_lite.interfaces",
            "foundry_lite.observability",
            "foundry_lite.security",
            "apps.",
        ),
        "foundry_lite.infrastructure": ("apps.",),
        "foundry_lite.security": ("apps.", "foundry_lite.application", "foundry_lite.infrastructure"),
        "apps.api": ("apps.cli",),
        "apps.cli": ("apps.api",),
    }
    violations: list[str] = []
    for source, targets in edges.items():
        for layer, forbidden_prefixes in rules.items():
            if not source.startswith(layer):
                continue
            for target in targets:
                if any(target.startswith(prefix) for prefix in forbidden_prefixes):
                    violations.append(f"{source} -> {target}")
    return violations


def coupling_report(edges: dict[str, list[str]]) -> dict[str, Any]:
    fan_in: dict[str, int] = defaultdict(int)
    for targets in edges.values():
        for target in targets:
            fan_in[target] += 1
    modules: list[CouplingModule] = []
    for name, fan_out_targets in sorted(edges.items()):
        modules.append(
            {
                "module": name,
                "fan_in": fan_in[name],
                "fan_out": len(fan_out_targets),
                "depends_on": fan_out_targets,
            }
        )
    return {
        "module_count": len(edges),
        "edge_count": sum(len(targets) for targets in edges.values()),
        "modules": modules,
    }


def write_reports(output: Path, modules: dict[str, ModuleInfo], edges: dict[str, list[str]]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    cycles = find_cycles(edges)
    violations = layer_violations(edges)
    coupling = coupling_report(edges)
    report = {
        "modules": {name: str(info.path.relative_to(ROOT)) for name, info in sorted(modules.items())},
        "edges": edges,
        "cycles": cycles,
        "layer_violations": violations,
        "coupling": coupling,
    }
    (output / "dependency_graph.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        "# Dependency Graph Report",
        "",
        f"- modules: {coupling['module_count']}",
        f"- internal edges: {coupling['edge_count']}",
        f"- cycles: {len(cycles)}",
        f"- layer violations: {len(violations)}",
        "",
        "## Highest Fan-out",
        "",
    ]
    coupling_modules = cast(list[CouplingModule], coupling["modules"])
    for item in sorted(coupling_modules, key=lambda module: module["fan_out"], reverse=True)[:15]:
        lines.append(f"- `{item['module']}` fan_out={item['fan_out']} fan_in={item['fan_in']}")
    lines.extend(["", "## Layer Violations", ""])
    lines.extend(f"- `{violation}`" for violation in violations) if violations else lines.append("- none")
    lines.extend(["", "## Cycles", ""])
    lines.extend(f"- `{' -> '.join(cycle)}`" for cycle in cycles) if cycles else lines.append("- none")
    (output / "dependency_graph.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-fan-out", type=int, default=20)
    args = parser.parse_args(argv)

    modules = collect_modules()
    edges = internal_edges(modules)
    write_reports(args.output, modules, edges)

    cycles = find_cycles(edges)
    violations = layer_violations(edges)
    over_coupled = [name for name, targets in edges.items() if len(targets) > args.max_fan_out]
    if cycles or violations or over_coupled:
        print(f"Dependency graph report: {args.output / 'dependency_graph.md'}")
        for violation in violations:
            print(f"Layer violation: {violation}")
        for cycle in cycles:
            print(f"Cycle: {' -> '.join(cycle)}")
        for name in over_coupled:
            print(f"High fan-out: {name} imports {len(edges[name])} internal modules")
        return 1
    print(f"Dependency graph OK: {args.output / 'dependency_graph.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
