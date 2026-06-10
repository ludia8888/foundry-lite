from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SERVICE_ROOT = ROOT / "libs" / "foundry_lite" / "application" / "services"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "mixin_call_graph.json"


@dataclass(frozen=True)
class CrossMixinCall:
    caller_mixin: str
    callee_mixin: str
    method_name: str
    path: str
    line: int


def _is_self_attribute(node: ast.Attribute) -> bool:
    return isinstance(node.value, ast.Name) and node.value.id == "self"


def _service_files() -> list[Path]:
    return sorted(p for p in SERVICE_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _collect_mixin_methods(paths: list[Path]) -> dict[str, set[str]]:
    """Map mixin class name -> set of method names defined directly in that class."""
    mixin_methods: dict[str, set[str]] = {}
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            methods: set[str] = set()
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    methods.add(item.name)
            mixin_methods[node.name] = methods
    return mixin_methods


def _resolve_callee_mixin(attr: str, caller_mixin: str, mixin_methods: dict[str, set[str]]) -> str | None:
    for owner_mixin, methods in mixin_methods.items():
        if owner_mixin == caller_mixin:
            continue
        if attr in methods:
            return owner_mixin
    return None


def _calls_in_class(
    node: ast.ClassDef,
    *,
    path: Path,
    mixin_methods: dict[str, set[str]],
) -> list[CrossMixinCall]:
    local = mixin_methods.get(node.name, set())
    calls: list[CrossMixinCall] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Attribute):
            continue
        if not _is_self_attribute(child):
            continue
        attr = child.attr
        if attr in local:
            continue
        owner = _resolve_callee_mixin(attr, node.name, mixin_methods)
        if owner is None:
            continue
        calls.append(
            CrossMixinCall(
                caller_mixin=node.name,
                callee_mixin=owner,
                method_name=attr,
                path=str(path.relative_to(ROOT)),
                line=child.lineno,
            )
        )
    return calls


def _collect_cross_mixin_calls(
    paths: list[Path],
    mixin_methods: dict[str, set[str]],
) -> list[CrossMixinCall]:
    """For each self.X() access inside a mixin, if X is defined in another mixin,
    record a cross-mixin call edge."""
    calls: list[CrossMixinCall] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                calls.extend(_calls_in_class(node, path=path, mixin_methods=mixin_methods))
    return calls


def _adjacency(calls: list[CrossMixinCall]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for call in calls:
        graph[call.caller_mixin].add(call.callee_mixin)
    return graph


def _find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan's strongly-connected components; return non-trivial SCCs."""
    index_counter = [0]
    stack: list[str] = []
    on_stack: dict[str, bool] = defaultdict(bool)
    index: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    result: list[list[str]] = []

    def strongconnect(node: str) -> None:
        index[node] = index_counter[0]
        lowlinks[node] = index_counter[0]
        index_counter[0] += 1
        stack.append(node)
        on_stack[node] = True
        for successor in graph.get(node, set()):
            if successor not in index:
                strongconnect(successor)
                lowlinks[node] = min(lowlinks[node], lowlinks[successor])
            elif on_stack[successor]:
                lowlinks[node] = min(lowlinks[node], index[successor])
        if lowlinks[node] == index[node]:
            component: list[str] = []
            while True:
                successor = stack.pop()
                on_stack[successor] = False
                component.append(successor)
                if successor == node:
                    break
            if len(component) > 1:
                result.append(sorted(component))

    nodes = set(graph)
    for callees in graph.values():
        nodes.update(callees)
    for node in nodes:
        if node not in index:
            strongconnect(node)
    return result


def _topological_levels(graph: dict[str, set[str]]) -> dict[str, int]:
    """Return level of each node = longest path from any leaf to that node.

    Leaves (no outgoing edges) are at level 0. Roots (no incoming edges) get
    the highest level. Raises ValueError if a cycle is present (caller must
    check cycles first).
    """
    nodes: set[str] = set(graph)
    for callees in graph.values():
        nodes.update(callees)
    levels: dict[str, int] = {}

    def visit(node: str, stack: set[str]) -> int:
        if node in levels:
            return levels[node]
        if node in stack:
            raise ValueError(f"cycle through {node}")
        stack.add(node)
        callees = graph.get(node, set())
        if not callees:
            depth = 0
        else:
            depth = 1 + max(visit(c, stack) for c in callees)
        stack.remove(node)
        levels[node] = depth
        return depth

    for node in nodes:
        visit(node, set())
    return levels


def _fan_out_per_mixin(graph: dict[str, set[str]]) -> dict[str, int]:
    return {node: len(graph.get(node, set())) for node in set(graph) | {c for cs in graph.values() for c in cs}}


def _build_report(
    calls: list[CrossMixinCall],
    graph: dict[str, set[str]],
    cycles: list[list[str]],
    levels: dict[str, int],
    fan_out: dict[str, int],
) -> dict[str, Any]:
    return {
        "summary": {
            "mixin_count": len({c.caller_mixin for c in calls} | {c.callee_mixin for c in calls}),
            "cross_mixin_call_count": len(calls),
            "cycle_count": len(cycles),
            "max_depth": max(levels.values()) if levels else 0,
            "max_fan_out": max(fan_out.values()) if fan_out else 0,
        },
        "cycles": cycles,
        "levels": [{"mixin": m, "level": levels[m]} for m in sorted(levels, key=lambda x: (levels[x], x))],
        "fan_out": [
            {"mixin": m, "fan_out": fan_out[m], "callees": sorted(graph.get(m, set()))}
            for m in sorted(fan_out, key=lambda x: (-fan_out[x], x))
        ],
        "edges": [
            {
                "from": call.caller_mixin,
                "to": call.callee_mixin,
                "method": call.method_name,
                "path": call.path,
                "line": call.line,
            }
            for call in calls
        ],
    }


def _cycle_failures(cycles: list[list[str]]) -> list[str]:
    return [f"Mixin call cycle: {' -> '.join(cycle)} -> {cycle[0]}" for cycle in cycles]


def _depth_failure(levels: dict[str, int], max_depth: int) -> str | None:
    if not levels:
        return None
    actual = max(levels.values())
    if actual <= max_depth:
        return None
    deep = sorted(m for m, lvl in levels.items() if lvl == actual)
    return f"Mixin call depth {actual} exceeds --max-depth {max_depth}: {deep}"


def _fan_out_failures(fan_out: dict[str, int], max_fan_out: int) -> list[str]:
    failures: list[str] = []
    for mixin_name, count in fan_out.items():
        if count > max_fan_out:
            failures.append(
                f"Mixin {mixin_name} calls {count} other mixins; "
                f"--max-fan-out {max_fan_out}. Extract a collaborator or split the mixin."
            )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--max-depth",
        type=int,
        default=7,
        help="Maximum allowed cross-mixin call depth (root-to-leaf chain length).",
    )
    parser.add_argument(
        "--max-fan-out",
        type=int,
        default=10,
        help="Maximum number of distinct other mixins a single mixin may call.",
    )
    args = parser.parse_args(argv)

    paths = _service_files()
    mixin_methods = _collect_mixin_methods(paths)
    calls = _collect_cross_mixin_calls(paths, mixin_methods)
    graph = _adjacency(calls)
    cycles = _find_cycles(graph)
    levels: dict[str, int] = {} if cycles else _topological_levels(graph)
    fan_out = _fan_out_per_mixin(graph)

    report = _build_report(calls, graph, cycles, levels, fan_out)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    failures = _cycle_failures(cycles)
    if not cycles:
        depth_failure = _depth_failure(levels, args.max_depth)
        if depth_failure:
            failures.append(depth_failure)
    failures.extend(_fan_out_failures(fan_out, args.max_fan_out))

    if failures:
        print("Mixin call graph guard failed:")
        for message in failures:
            print(f"- {message}")
        print(f"Report: {args.output.relative_to(ROOT)}")
        return 1

    summary = report["summary"]
    print(
        "Mixin call graph guard OK: "
        f"{summary['mixin_count']} mixins, "
        f"{summary['cross_mixin_call_count']} cross-mixin calls, "
        f"cycles={summary['cycle_count']}, "
        f"max_depth={summary['max_depth']}/{args.max_depth}, "
        f"max_fan_out={summary['max_fan_out']}/{args.max_fan_out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
