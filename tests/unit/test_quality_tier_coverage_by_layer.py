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


def test_a_layer_is_held_to_its_floor_rather_than_the_target() -> None:
    """A floor exists so a layer carrying debt still has a line it cannot cross."""
    coverage = _complete_coverage()
    # 92% sits above api's recorded floor of 91 and below the 95 target.
    coverage["files"]["apps/api/foundry_lite_api/main.py"] = _file_payload(covered_lines=92, statements=100)

    layers = gate.collect_layer_coverage(coverage, threshold=95)

    assert layers["api"].gate_pass is True
    assert layers["api"].threshold == gate.LAYER_FLOORS["api"]


def test_a_layer_without_a_floor_is_still_held_to_the_target() -> None:
    """Only layers listed as carrying debt get the allowance; the rest keep the standard."""
    coverage = _complete_coverage()
    coverage["files"]["apps/cli/foundry_lite_cli/main.py"] = _file_payload(covered_lines=93, statements=100)

    layers = gate.collect_layer_coverage(coverage, threshold=95)

    assert "cli" not in gate.LAYER_FLOORS
    assert layers["cli"].threshold == 95
    assert layers["cli"].gate_pass is False


def test_every_floor_sits_below_the_target() -> None:
    """A floor at or above the target is either pointless or a silent raise of the standard."""
    assert all(floor < 95 for floor in gate.LAYER_FLOORS.values())


def _api_is_ready_to_raise(*, covered_lines: int, statements: int) -> bool:
    coverage = _complete_coverage()
    coverage["files"]["apps/api/foundry_lite_api/main.py"] = _file_payload(
        covered_lines=covered_lines, statements=statements
    )
    candidates = gate.ratchet_candidates(gate.collect_layer_coverage(coverage, threshold=95))
    return "api" in {layer.layer for layer in candidates}


def test_a_layer_that_outgrew_its_floor_is_reported_as_ready_to_raise() -> None:
    """Without this the ratchet only ever loosens: floors would trail their layers forever."""
    assert _api_is_ready_to_raise(covered_lines=99, statements=100)


def test_a_layer_resting_just_above_its_floor_is_not_reported() -> None:
    """91.2% against a floor of 91 is drift, not progress; suggesting a raise there is noise."""
    assert not _api_is_ready_to_raise(covered_lines=912, statements=1000)


def test_reporting_a_failure_survives_an_output_path_outside_the_repo(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    """The gate used to raise ValueError while printing the report path, losing the failure.

    `Path.relative_to` throws when the output is not under the repo root, which is exactly the
    case when the gate is re-run against a coverage artifact downloaded from CI -- so the crash
    landed on whoever was diagnosing a red lane.
    """
    coverage = _complete_coverage()
    coverage["files"]["apps/api/foundry_lite_api/main.py"] = _file_payload(covered_lines=1, statements=100)
    layers = gate.collect_layer_coverage(coverage, threshold=95)

    gate._print_failure(layers, tmp_path / "outside.json")

    assert "Release gate blocked" in capsys.readouterr().out


def test_tier_coverage_gate_writes_report(tmp_path: Path) -> None:
    layers = gate.collect_layer_coverage(_complete_coverage(), threshold=95)
    output = tmp_path / "quality" / "tier_coverage_by_layer.json"

    gate.write_report(output, layers, threshold=95)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["gate_pass"] is True
    assert report["layers"]["application"]["percent"] == 95.0
