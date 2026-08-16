from __future__ import annotations

import json
from pathlib import Path

from scripts.quality.check_infrastructure_swapability import (
    COMPOSITION,
    DEPLOYMENT_PORT,
    MATRIX,
    PROVIDER_NEUTRAL_MODULES,
    REGRESSION,
    REQUIRED_FAMILY_IDS,
    REQUIRED_TERMS,
    SOURCE_PORT,
    collect_findings,
    main,
)


def test_swapability_gate_accepts_provider_neutral_registry(tmp_path: Path) -> None:
    _valid_tree(tmp_path)

    assert collect_findings(tmp_path) == []


def test_swapability_gate_rejects_provider_literal_in_application(tmp_path: Path) -> None:
    _valid_tree(tmp_path)
    target = tmp_path / PROVIDER_NEUTRAL_MODULES[0]
    target.write_text(f'{target.read_text(encoding="utf-8")}\nprovider = {{"provider": "render"}}\n', encoding="utf-8")

    findings = collect_findings(tmp_path)

    assert any(item.code == "provider_lock_in" and item.term == '"provider": "render"' for item in findings)


def test_swapability_gate_writes_machine_readable_failure(tmp_path: Path) -> None:
    output = tmp_path / "report.json"

    assert main(["--root", str(tmp_path), "--output", str(output)]) == 1
    assert '"gate_pass": false' in output.read_text(encoding="utf-8")


def test_swapability_gate_rejects_missing_global_family(tmp_path: Path) -> None:
    _valid_tree(tmp_path)
    path = tmp_path / MATRIX
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["families"] = payload["families"][1:]
    path.write_text(json.dumps(payload), encoding="utf-8")

    findings = collect_findings(tmp_path)

    assert any(item.code == "missing_family" for item in findings)


def _valid_tree(root: Path) -> None:
    for relative in {DEPLOYMENT_PORT, SOURCE_PORT, COMPOSITION, REGRESSION, *PROVIDER_NEUTRAL_MODULES}:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        terms = REQUIRED_TERMS.get(relative, ())
        path.write_text("\n".join(terms), encoding="utf-8")
    _write_valid_matrix(root)


def _write_valid_matrix(root: Path) -> None:
    proof = root / "proof.py"
    proof.write_text("selector\n", encoding="utf-8")
    families = [
        {
            "id": family_id,
            "boundary": "proof.py",
            "compositionRoot": "proof.py",
            "selector": "selector",
            "implementations": [
                {"name": "local", "path": "proof.py"},
                {"name": "alternate", "path": "proof.py"},
            ],
            "contractTests": ["proof.py"],
            "isStateful": False,
            "swapLevel": "contract",
            "cutoverStatus": "not-applicable",
        }
        for family_id in sorted(REQUIRED_FAMILY_IDS)
    ]
    payload = {
        "version": 1,
        "statefulCutoverRequirements": [
            "migration",
            "reconciliation",
            "write_fencing",
            "rollback",
            "rpo_rto",
        ],
        "families": families,
    }
    path = root / MATRIX
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
