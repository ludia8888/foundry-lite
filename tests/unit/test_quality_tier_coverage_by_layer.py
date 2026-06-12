from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_tier_coverage_by_layer as gate


def _file_payload(*, covered_lines: int, statements: int, covered_branches: int = 0, branches: int = 0) -> dict:
    return {
        "summary": {
            "covered_lines": covered_lines,
            "num_statements": statements,
            "covered_branches": covered_branches,
            "num_branches": branches,
        }
    }


def _complete_coverage() -> dict:
    return {
        "files": {
            "libs/foundry_lite/domain/model.py": _file_payload(covered_lines=10, statements=10),
            "libs/foundry_lite/application/service.py": _file_payload(
                covered_lines=95,
                statements=100,
                covered_branches=19,
                branches=20,
            ),
            "libs/foundry_lite/infrastructure/repo.py": _file_payload(covered_lines=20, statements=20),
            "apps/api/foundry_lite_api/main.py": _file_payload(covered_lines=20, statements=20),
            "apps/cli/foundry_lite_cli/main.py": _file_payload(covered_lines=20, statements=20),
            "apps/worker/foundry_lite_worker/__init__.py": _file_payload(covered_lines=0, statements=0),
        }
    }


def test_tier_coverage_gate_passes_when_all_layers_meet_threshold() -> None:
    layers = gate.collect_layer_coverage(_complete_coverage(), threshold=95)

    assert all(layer.gate_pass for layer in layers.values())
    assert layers["application"].percent == 95.0
    assert layers["worker"].file_count == 1


def test_tier_coverage_gate_flags_low_layer() -> None:
    coverage = _complete_coverage()
    coverage["files"]["apps/api/foundry_lite_api/main.py"] = _file_payload(covered_lines=18, statements=20)

    layers = gate.collect_layer_coverage(coverage, threshold=95)

    assert layers["api"].gate_pass is False
    assert layers["api"].percent == 90.0


def test_tier_coverage_gate_flags_missing_layer() -> None:
    coverage = _complete_coverage()
    coverage["files"].pop("apps/cli/foundry_lite_cli/main.py")

    layers = gate.collect_layer_coverage(coverage, threshold=95)

    assert layers["cli"].gate_pass is False
    assert layers["cli"].file_count == 0


def test_tier_coverage_gate_writes_report(tmp_path: Path) -> None:
    layers = gate.collect_layer_coverage(_complete_coverage(), threshold=95)
    output = tmp_path / "quality" / "tier_coverage_by_layer.json"

    gate.write_report(output, layers, threshold=95)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["gate_pass"] is True
    assert report["layers"]["application"]["percent"] == 95.0
