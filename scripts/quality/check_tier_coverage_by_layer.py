"""Enforces guideline §11.4: coverage must not hide weak layers in averages.

The ordinary coverage gate proves the whole Python backend is well exercised.
This gate splits the same coverage JSON into architectural layers so a strong
domain or application score cannot hide an untested API, CLI, worker, or
infrastructure layer.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "tier_coverage_by_layer.json"

LAYER_PREFIXES: dict[str, tuple[str, ...]] = {
    "domain": ("libs/foundry_lite/domain/",),
    "application": ("libs/foundry_lite/application/",),
    "infrastructure": ("libs/foundry_lite/infrastructure/",),
    "api": ("apps/api/",),
    "cli": ("apps/cli/",),
    "worker": ("apps/worker/",),
}

# Ratchet floors, not targets. `--threshold` remains the standard; a layer listed here is
# carrying debt and is pinned to where it actually measured so the debt cannot grow while it is
# paid down. Measured on cf601f75, the first run in which this gate reached completion -- three
# earlier main pushes cancelled the 36-minute coverage lane before it got here, which is why the
# shortfall went unreported while PRs #163/#164 landed ~7k statements of Action/MCP/OSDK code.
#
# Per layer rather than one global floor: the global value would have to be the minimum across
# layers (91), which would let worker slide from 94.47 to 91 unnoticed. `cli` has no entry
# because it already clears the target.
#
# Each floor sits just under its measurement so ordinary run-to-run drift does not fail the
# gate. Raise a floor when its layer clears it; the gate prints which ones are ready.
LAYER_FLOORS: dict[str, float] = {
    "domain": 93.0,  # measured 93.50 in the 2026-08-13 full 6,190-test coverage run
    "application": 93.0,  # measured 93.14
    "infrastructure": 93.0,  # measured 93.59
    "api": 91.0,  # measured 91.97
    "worker": 94.0,  # measured 94.47
}

# How far above its floor a layer must sit before the gate suggests tightening it.
RATCHET_SLACK = 1.0


@dataclass(frozen=True)
class LayerCoverage:
    layer: str
    file_count: int
    covered_units: int
    total_units: int
    percent: float
    covered_lines: int
    statements: int
    line_percent: float
    covered_branches: int
    branches: int
    branch_percent: float
    threshold: float
    gate_pass: bool


def _percent(covered: int, total: int) -> float:
    if total == 0:
        return 100.0
    return covered / total * 100


def _layer_for_path(path: str) -> str | None:
    for layer, prefixes in LAYER_PREFIXES.items():
        if any(path.startswith(prefix) for prefix in prefixes):
            return layer
    return None


def collect_layer_coverage(coverage: dict[str, Any], *, threshold: float) -> dict[str, LayerCoverage]:
    totals = {
        layer: {
            "file_count": 0,
            "covered_lines": 0,
            "statements": 0,
            "covered_branches": 0,
            "branches": 0,
        }
        for layer in LAYER_PREFIXES
    }
    for path, payload in coverage.get("files", {}).items():
        layer = _layer_for_path(str(path))
        if layer is None:
            continue
        summary = payload.get("summary", {})
        bucket = totals[layer]
        bucket["file_count"] += 1
        bucket["covered_lines"] += int(summary.get("covered_lines", 0))
        bucket["statements"] += int(summary.get("num_statements", 0))
        bucket["covered_branches"] += int(summary.get("covered_branches", 0))
        bucket["branches"] += int(summary.get("num_branches", 0))

    layers: dict[str, LayerCoverage] = {}
    for layer, bucket in totals.items():
        covered_units = bucket["covered_lines"] + bucket["covered_branches"]
        total_units = bucket["statements"] + bucket["branches"]
        percent = _percent(covered_units, total_units)
        required = layer_threshold(layer, threshold)
        layers[layer] = LayerCoverage(
            layer=layer,
            file_count=bucket["file_count"],
            covered_units=covered_units,
            total_units=total_units,
            percent=round(percent, 2),
            covered_lines=bucket["covered_lines"],
            statements=bucket["statements"],
            line_percent=round(_percent(bucket["covered_lines"], bucket["statements"]), 2),
            covered_branches=bucket["covered_branches"],
            branches=bucket["branches"],
            branch_percent=round(_percent(bucket["covered_branches"], bucket["branches"]), 2),
            threshold=required,
            gate_pass=bucket["file_count"] > 0 and percent >= required,
        )
    return layers


def layer_threshold(layer: str, target: float) -> float:
    """The percentage `layer` must actually clear: its recorded floor, else the target."""
    return LAYER_FLOORS.get(layer, target)


def ratchet_candidates(layers: dict[str, LayerCoverage]) -> list[LayerCoverage]:
    """Layers sitting far enough above their floor that the floor should be raised."""
    return sorted(
        (
            layer
            for layer in layers.values()
            if layer.layer in LAYER_FLOORS and layer.percent >= layer.threshold + RATCHET_SLACK
        ),
        key=lambda layer: layer.layer,
    )


def write_report(output: Path, layers: dict[str, LayerCoverage], threshold: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    violations = [layer.__dict__ for layer in layers.values() if not layer.gate_pass]
    report = {
        "count": len(violations),
        "baseline": 0,
        "threshold": threshold,
        "layer_floors": dict(LAYER_FLOORS),
        "ratchet_candidates": [layer.layer for layer in ratchet_candidates(layers)],
        "gate_pass": not violations,
        "layers": {name: layer.__dict__ for name, layer in layers.items()},
        "violations": violations,
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(layers: dict[str, LayerCoverage], output: Path) -> None:
    print("Release gate blocked: coverage is below threshold in one or more architectural layers.")
    for layer in layers.values():
        if layer.gate_pass:
            continue
        if layer.file_count == 0:
            print(f"- {layer.layer}: missing from coverage data")
        else:
            print(
                f"- {layer.layer}: {layer.percent:.2f}% < {layer.threshold:.2f}% "
                f"({layer.covered_units}/{layer.total_units} covered units)"
            )
    _print_report_path(output)


def _print_ratchet_candidates(layers: dict[str, LayerCoverage]) -> None:
    candidates = ratchet_candidates(layers)
    if not candidates:
        return
    print("Floors ready to be raised (a floor that trails its layer stops holding anything):")
    for layer in candidates:
        print(f"- {layer.layer}: {layer.percent:.2f}% against a floor of {layer.threshold:.2f}%")


def _print_report_path(output: Path) -> None:
    try:
        print(f"Report: {output.relative_to(ROOT)}")
    except ValueError:
        # An --output outside the repo is legitimate when the gate is run against a downloaded
        # artifact; reporting the absolute path beats crashing on the way to reporting a failure.
        print(f"Report: {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check coverage by architectural layer.")
    parser.add_argument("coverage_json", type=Path)
    parser.add_argument("--threshold", type=float, default=95.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    coverage = json.loads(args.coverage_json.read_text(encoding="utf-8"))
    layers = collect_layer_coverage(coverage, threshold=args.threshold)
    write_report(args.output, layers, args.threshold)
    _print_ratchet_candidates(layers)
    if not all(layer.gate_pass for layer in layers.values()):
        _print_failure(layers, args.output)
        return 1
    print(f"Layer coverage gate passed: every layer cleared its floor (target {args.threshold:.2f}%).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
