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
FAN_OUT_BASELINE_MODULES = {
    "foundry_lite.application.ports",
    "foundry_lite.application.primitives",
    "foundry_lite.application.services.base",
    "foundry_lite.domain.context",
    "foundry_lite.domain.errors",
}


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
            matches: set[str] = set()
            for candidate in names:
                if imported == candidate or imported.startswith(f"{candidate}."):
                    matches.add(candidate)
            linked.update(
                candidate
                for candidate in matches
                if not any(other != candidate and other.startswith(f"{candidate}.") for other in matches)
            )
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


def _default_aggregation_roots() -> list[str]:
    return [
        "foundry_lite.application.ports",
        "foundry_lite.infrastructure.repositories",
        "foundry_lite.infrastructure.adapters",
        # Composition roots wire every bounded context together; their fan-out grows
        # by one per new context (e.g. the Media/Content Plane) and is coupling by
        # design, not by accident — bounded by the higher aggregation budget, not exempt.
        "foundry_lite.application.core_services",
        "foundry_lite.application.dependencies",
        "foundry_lite.application.facades",
        "foundry_lite.infrastructure.local_runtime",
        # The schema package facade re-exports every domain table module so the
        # historical import surface stays stable; its fan-out grows by one per
        # new table domain, by design.
        "foundry_lite.infrastructure.schema",
        # The CoreService DI base type-annotates every injectable dependency, and a
        # bounded-context facade aggregates that context's services + DTOs — both are
        # aggregation points whose fan-out grows with the platform, by design.
        "foundry_lite.application.services.base",
        "foundry_lite.application.facades.media_workspace",
        # The MediaServices group composes every media bounded-context service; its
        # fan-out grows by one per new media capability (e.g. M9 access patterns), by design.
        "foundry_lite.application.services.media_service",
        # These are explicit operator/composition roots. They assemble many bounded
        # services or proof adapters but do not own domain rules; router purity and
        # service dependency gates still prevent hidden DB/service coupling.
        "apps.api.foundry_lite_api.main",
        "foundry_lite.application.facades.operations_console",
        # Pipeline DAG control/worker entrypoints intentionally compose the
        # immutable plan, evidence, retry, fencing, and execution boundaries.
        # Their collaborator fan-out remains separately capped at 10 by the
        # service-call graph gate.
        "foundry_lite.application.services.pipeline_async_run_service",
        "foundry_lite.application.services.pipeline_distributed_node_service",
        "foundry_lite.application.services.pipeline_preview_service",
        # Same family: the control worker composes every durable recovery scan
        # (dispatch, cancellation, schedule terminal, stale execution) and must also
        # enumerate tenants and bind tenant_context so each scan runs inside its RLS
        # context. Its fan-out grows by one per new recovery concern; its collaborator
        # fan-out stays capped at 10 by the service-call graph gate.
        "foundry_lite.application.services.pipeline_control_worker_service",
        "scripts.operations.run_live_media_byte_proof",
        "scripts.operations.run_palantir_live_simulation",
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-fan-out", type=int, default=10)
    parser.add_argument(
        "--max-aggregation-fan-out",
        type=int,
        # 35: explicit composition roots grow by one per new bounded capability; connector/source
        # onboarding add their registry and management ports to CoreDependencies, which is
        # intentional coupling at the dependency injection root rather than a hidden service
        # dependency. OSDK application/client/OAuth/session ports are also platform roots.
        #
        # The `infrastructure.adapters` aggregation root grows by one per new adapter; L13
        # added `LocalCompletionAdapter` (query-side HyDE/distillation completion seam) and AIP
        # P0b adds the `FakeLanguageModel` + `ProviderCompatibleLanguageModel` governed-gateway
        # seams — coupling by design at an explicit aggregation point, not accidental fan-out.
        #
        # 37: the Pipeline DAG orchestrator port/adapter adds one bounded
        # capability to each explicit package facade.
        default=37,
        help="Higher fan-out budget for explicit aggregation roots (ports/repositories __init__).",
    )
    parser.add_argument("--aggregation-root", action="append", default=_default_aggregation_roots())
    return parser


def _fan_out_limit(name: str, aggregation_roots: set[str], max_fan_out: int, max_aggregation_fan_out: int) -> int:
    if name in aggregation_roots:
        return max_aggregation_fan_out
    return max_fan_out


def _over_coupled_modules(
    edges: dict[str, list[str]],
    aggregation_roots: set[str],
    max_fan_out: int,
    max_aggregation_fan_out: int,
) -> list[str]:
    over_coupled: list[str] = []
    for name, targets in edges.items():
        counted_targets = [target for target in targets if target not in FAN_OUT_BASELINE_MODULES]
        limit = _fan_out_limit(name, aggregation_roots, max_fan_out, max_aggregation_fan_out)
        if len(counted_targets) > limit:
            over_coupled.append(name)
    return over_coupled


def _print_failures(
    output: Path, edges: dict[str, list[str]], cycles: list[list[str]], violations: list[str], over_coupled: list[str]
) -> None:
    print(f"Dependency graph report: {output / 'dependency_graph.md'}")
    for violation in violations:
        print(f"Layer violation: {violation}")
    for cycle in cycles:
        print(f"Cycle: {' -> '.join(cycle)}")
    for name in over_coupled:
        print(f"High fan-out: {name} imports {len(edges[name])} internal modules")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    modules = collect_modules()
    edges = internal_edges(modules)
    write_reports(args.output, modules, edges)

    cycles = find_cycles(edges)
    violations = layer_violations(edges)
    aggregation_roots = set(args.aggregation_root)
    over_coupled = _over_coupled_modules(
        edges,
        aggregation_roots,
        args.max_fan_out,
        args.max_aggregation_fan_out,
    )
    if cycles or violations or over_coupled:
        _print_failures(args.output, edges, cycles, violations, over_coupled)
        return 1
    print(f"Dependency graph OK: {args.output / 'dependency_graph.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
