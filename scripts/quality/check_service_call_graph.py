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
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "service_call_graph.json"
NON_LEAF_SERVICE_CLASSES = {"CoreService", "CoreServices", "DatasetServices", "ObjectServices"}


@dataclass(frozen=True)
class CrossServiceCall:
    caller_service: str
    callee_service: str
    method_name: str
    path: str
    line: int


def _is_self_attribute(node: ast.Attribute) -> bool:
    return isinstance(node.value, ast.Name) and node.value.id == "self"


def _service_files() -> list[Path]:
    return sorted(p for p in SERVICE_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _is_leaf_service_class(node: ast.ClassDef) -> bool:
    return node.name.endswith("Service") and node.name not in NON_LEAF_SERVICE_CLASSES


def _collect_service_methods(paths: list[Path]) -> dict[str, set[str]]:
    """Map service class name -> set of method names defined directly in that class."""
    service_methods: dict[str, set[str]] = {}
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not _is_leaf_service_class(node):
                continue
            methods: set[str] = set()
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    methods.add(item.name)
            service_methods[node.name] = methods
    return service_methods


def _service_collaborator_dict(node: ast.AST) -> ast.Dict | None:
    value: ast.AST | None = None
    if isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == "SERVICE_COLLABORATORS" for target in node.targets
    ):
        value = node.value
    if (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "SERVICE_COLLABORATORS"
    ):
        value = node.value
    return value if isinstance(value, ast.Dict) else None


def _string_dict_items(node: ast.Dict) -> dict[str, str]:
    items: dict[str, str] = {}
    for key, value in zip(node.keys, node.values, strict=True):
        if (
            isinstance(key, ast.Constant)
            and isinstance(key.value, str)
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            items[key.value] = value.value
    return items


def _collect_collaborator_service_classes(paths: list[Path]) -> dict[str, str]:
    collaborators: dict[str, str] = {}
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            collaborator_dict = _service_collaborator_dict(node)
            if collaborator_dict is not None:
                collaborators.update(_string_dict_items(collaborator_dict))
    return collaborators


def _resolve_callee_service(attr: str, caller_service: str, service_methods: dict[str, set[str]]) -> str | None:
    for owner_service, methods in service_methods.items():
        if owner_service == caller_service:
            continue
        if attr in methods:
            return owner_service
    return None


def _cross_service_call(
    *,
    caller_service: str,
    callee_service: str,
    method_name: str,
    path: Path,
    line: int,
) -> CrossServiceCall:
    return CrossServiceCall(
        caller_service=caller_service,
        callee_service=callee_service,
        method_name=method_name,
        path=str(path.relative_to(ROOT)),
        line=line,
    )


def _explicit_collaborator_call(
    call: ast.Call,
    *,
    caller_service: str,
    path: Path,
    collaborator_service_classes: dict[str, str],
) -> CrossServiceCall | None:
    if not isinstance(call.func, ast.Attribute):
        return None
    owner = call.func.value
    if not isinstance(owner, ast.Attribute) or not _is_self_attribute(owner):
        return None
    callee_service = collaborator_service_classes.get(owner.attr)
    if callee_service is None or callee_service == caller_service:
        return None
    return _cross_service_call(
        caller_service=caller_service,
        callee_service=callee_service,
        method_name=call.func.attr,
        path=path,
        line=call.lineno,
    )


def _legacy_flat_method_call(
    call: ast.Call,
    *,
    caller_service: str,
    path: Path,
    local_methods: set[str],
    service_methods: dict[str, set[str]],
) -> CrossServiceCall | None:
    if not isinstance(call.func, ast.Attribute) or not _is_self_attribute(call.func):
        return None
    method_name = call.func.attr
    if method_name in local_methods:
        return None
    owner_service = _resolve_callee_service(method_name, caller_service, service_methods)
    if owner_service is None:
        return None
    return _cross_service_call(
        caller_service=caller_service,
        callee_service=owner_service,
        method_name=method_name,
        path=path,
        line=call.lineno,
    )


def _calls_in_class(
    node: ast.ClassDef,
    *,
    path: Path,
    service_methods: dict[str, set[str]],
    collaborator_service_classes: dict[str, str],
) -> list[CrossServiceCall]:
    local = service_methods.get(node.name, set())
    calls: list[CrossServiceCall] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        explicit_call = _explicit_collaborator_call(
            child,
            caller_service=node.name,
            path=path,
            collaborator_service_classes=collaborator_service_classes,
        )
        if explicit_call is not None:
            calls.append(explicit_call)
            continue
        legacy_call = _legacy_flat_method_call(
            child,
            caller_service=node.name,
            path=path,
            local_methods=local,
            service_methods=service_methods,
        )
        if legacy_call is not None:
            calls.append(legacy_call)
    return calls


def _collect_cross_service_calls(
    paths: list[Path],
    service_methods: dict[str, set[str]],
    collaborator_service_classes: dict[str, str],
) -> list[CrossServiceCall]:
    """Record explicit self.<collaborator>.<method>() service calls."""
    calls: list[CrossServiceCall] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and _is_leaf_service_class(node):
                calls.extend(
                    _calls_in_class(
                        node,
                        path=path,
                        service_methods=service_methods,
                        collaborator_service_classes=collaborator_service_classes,
                    )
                )
    return calls


def _adjacency(calls: list[CrossServiceCall]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for call in calls:
        graph[call.caller_service].add(call.callee_service)
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


def _fan_out_per_service(graph: dict[str, set[str]]) -> dict[str, int]:
    return {node: len(graph.get(node, set())) for node in set(graph) | {c for cs in graph.values() for c in cs}}


def _build_report(
    calls: list[CrossServiceCall],
    graph: dict[str, set[str]],
    cycles: list[list[str]],
    levels: dict[str, int],
    fan_out: dict[str, int],
) -> dict[str, Any]:
    return {
        "summary": {
            "service_count": len({c.caller_service for c in calls} | {c.callee_service for c in calls}),
            "cross_service_call_count": len(calls),
            "cycle_count": len(cycles),
            "max_depth": max(levels.values()) if levels else 0,
            "max_fan_out": max(fan_out.values()) if fan_out else 0,
        },
        "cycles": cycles,
        "levels": [{"service": m, "level": levels[m]} for m in sorted(levels, key=lambda x: (levels[x], x))],
        "fan_out": [
            {"service": m, "fan_out": fan_out[m], "callees": sorted(graph.get(m, set()))}
            for m in sorted(fan_out, key=lambda x: (-fan_out[x], x))
        ],
        "edges": [
            {
                "from": call.caller_service,
                "to": call.callee_service,
                "method": call.method_name,
                "path": call.path,
                "line": call.line,
            }
            for call in calls
        ],
    }


def _cycle_failures(cycles: list[list[str]]) -> list[str]:
    return [f"Service call cycle: {' -> '.join(cycle)} -> {cycle[0]}" for cycle in cycles]


def _depth_failure(levels: dict[str, int], max_depth: int) -> str | None:
    if not levels:
        return None
    actual = max(levels.values())
    if actual <= max_depth:
        return None
    deep = sorted(m for m, lvl in levels.items() if lvl == actual)
    return f"Service call depth {actual} exceeds --max-depth {max_depth}: {deep}"


def _fan_out_failures(fan_out: dict[str, int], max_fan_out: int) -> list[str]:
    failures: list[str] = []
    for service_name, count in fan_out.items():
        if count > max_fan_out:
            failures.append(
                f"Service {service_name} calls {count} other services; "
                f"--max-fan-out {max_fan_out}. Extract a collaborator or split the service."
            )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--max-depth",
        type=int,
        default=7,
        help="Maximum allowed cross-service call depth (root-to-leaf chain length).",
    )
    parser.add_argument(
        "--max-fan-out",
        type=int,
        default=10,
        help="Maximum number of distinct other services a single service may call.",
    )
    args = parser.parse_args(argv)

    paths = _service_files()
    service_methods = _collect_service_methods(paths)
    collaborator_service_classes = _collect_collaborator_service_classes(paths)
    if not collaborator_service_classes:
        print("Service call graph guard failed: SERVICE_COLLABORATORS registry is empty.")
        return 1
    calls = _collect_cross_service_calls(paths, service_methods, collaborator_service_classes)
    graph = _adjacency(calls)
    cycles = _find_cycles(graph)
    levels: dict[str, int] = {} if cycles else _topological_levels(graph)
    fan_out = _fan_out_per_service(graph)

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
        print("Service call graph guard failed:")
        for message in failures:
            print(f"- {message}")
        print(f"Report: {args.output.relative_to(ROOT)}")
        return 1

    summary = report["summary"]
    print(
        "Service call graph guard OK: "
        f"{summary['service_count']} services, "
        f"{summary['cross_service_call_count']} cross-service calls, "
        f"cycles={summary['cycle_count']}, "
        f"max_depth={summary['max_depth']}/{args.max_depth}, "
        f"max_fan_out={summary['max_fan_out']}/{args.max_fan_out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
