from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_infra_tricky_matrix as gate


def test_infra_tricky_matrix_gate_passes_current_docs_and_ci() -> None:
    assert gate.collect_findings() == []


def test_infra_tricky_matrix_requires_active_stack_composition(tmp_path: Path) -> None:
    _write_minimum_tree(tmp_path)
    matrix_path = tmp_path / "docs" / "infra-tricky-matrix.json"
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    matrix["compositionProofs"] = []
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "missing_required_composition_stack" for finding in findings)


def test_infra_tricky_matrix_requires_pulled_item_to_be_checked(tmp_path: Path) -> None:
    _write_minimum_tree(tmp_path, checklist_item_checked=False)

    findings = _collect(tmp_path)

    assert any(finding.code == "tricky_item_not_checked" and "A1" in finding.reference for finding in findings)


def test_infra_tricky_matrix_requires_matrix_tests_to_collect(tmp_path: Path) -> None:
    _write_minimum_tree(tmp_path)

    findings = _collect(tmp_path, collected_test_names={"test_other"})

    assert any(finding.code == "matrix_test_not_collected" for finding in findings)


def _collect(root: Path, *, collected_test_names: set[str] | None = None) -> list[gate.InfraTrickyMatrixFinding]:
    return gate.collect_findings(
        root,
        matrix_path=root / "docs" / "infra-tricky-matrix.json",
        checklist_path=root / "docs" / "foundry_lite_tricky_failure_modes_checklist.md",
        infra_doc_path=root / "docs" / "infra-ratchet.md",
        package_json_path=root / "package.json",
        ci_script_path=root / "scripts" / "ci_gate.sh",
        collected_test_names=collected_test_names or {"test_storage_contract", "test_storage_failure"},
    )


def _write_minimum_tree(root: Path, *, checklist_item_checked: bool = True) -> None:
    docs = root / "docs"
    scripts = root / "scripts"
    docs.mkdir(parents=True)
    scripts.mkdir(parents=True)
    status = "x" if checklist_item_checked else " "
    (docs / "foundry_lite_tricky_failure_modes_checklist.md").write_text(
        f"- [{status}] A1. storage pulled item (`test_storage_failure`)\n",
        encoding="utf-8",
    )
    (docs / "infra-ratchet.md").write_text(
        "| Order | Infrastructure | Status | Why | Cannot advance until |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 1 | StorageAdapter | active-covered | storage | `quality:storage` |\n",
        encoding="utf-8",
    )
    (root / "package.json").write_text(
        '{"scripts":{"quality:storage":"uv run pytest tests/integration/test_storage.py -q"}}',
        encoding="utf-8",
    )
    (scripts / "ci_gate.sh").write_text("pnpm --silent quality:storage\n", encoding="utf-8")
    (docs / "infra-tricky-matrix.json").write_text(json.dumps(_minimum_matrix()), encoding="utf-8")


def _minimum_matrix() -> dict[str, object]:
    proofs = {proof_class: ["test_storage_contract"] for proof_class in gate.REQUIRED_FAMILY_PROOF_CLASSES}
    composition_proofs = {
        proof_class: ["test_storage_failure"] for proof_class in gate.REQUIRED_COMPOSITION_PROOF_CLASSES
    }
    return {
        "schemaVersion": 1,
        "activeStack": ["storage"],
        "crossCuttingFlows": ["flow"],
        "families": [
            {
                "id": "storage",
                "name": "StorageAdapter",
                "status": "active-covered",
                "qualityScript": "quality:storage",
                "ciCommand": "pnpm --silent quality:storage",
                "testPaths": [],
                "proofs": proofs,
                "trickyItems": [{"id": "A1", "tests": ["test_storage_failure"]}],
            }
        ],
        "crossCuttingFlowProofs": [
            {
                "id": "flow",
                "name": "Flow",
                "status": "active-covered",
                "qualityScripts": ["quality:storage"],
                "ciCommands": ["pnpm --silent quality:storage"],
                "proofs": proofs,
                "trickyItems": [{"id": "A1", "tests": ["test_storage_failure"]}],
            }
        ],
        "compositionProofs": [
            {
                "id": "storage+flow",
                "members": ["storage", "flow"],
                "qualityScript": "quality:storage",
                "ciCommand": "pnpm --silent quality:storage",
                "proofs": composition_proofs,
                "trickyItems": [{"id": "A1", "tests": ["test_storage_failure"]}],
            }
        ],
    }
