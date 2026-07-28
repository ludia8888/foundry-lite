from __future__ import annotations

import json
from pathlib import Path

from scripts.quality import check_doc_drift as gate


def _write_doc(root: Path, text: str, relative_path: str = "docs/current.md") -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_python(root: Path, relative_path: str, text: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_package(root: Path, scripts: dict[str, str]) -> Path:
    path = root / "package.json"
    path.write_text(json.dumps({"scripts": scripts}), encoding="utf-8")
    return path


def test_doc_drift_flags_missing_script_reference(tmp_path: Path) -> None:
    doc = _write_doc(tmp_path, "Release gate is `scripts/quality/check_missing_gate.py`.\n")
    _write_python(tmp_path, "libs/example.py", "class ExistingService:\n    pass\n")

    findings = gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs",), root=tmp_path)

    assert findings == [
        gate.DocDriftFinding(
            code="doc_reference_missing_path",
            path="docs/current.md",
            line=1,
            reference="scripts/quality/check_missing_gate.py",
            message="Document references a source path/script that does not exist in the current tree",
        )
    ]


def test_doc_drift_flags_missing_current_python_symbol(tmp_path: Path) -> None:
    doc = _write_doc(
        tmp_path,
        "`MissingService` is now part of the application layer.\n",
        "docs/implementation-status.md",
    )
    _write_python(tmp_path, "libs/example.py", "class ExistingService:\n    pass\n")

    findings = gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs",), root=tmp_path)

    assert findings == [
        gate.DocDriftFinding(
            code="doc_reference_missing_symbol",
            path="docs/implementation-status.md",
            line=1,
            reference="MissingService",
            message="Document references a Python symbol that does not exist in the current tree",
        )
    ]


def test_doc_drift_accepts_existing_path_symbol_and_method(tmp_path: Path) -> None:
    doc = _write_doc(
        tmp_path,
        "\n".join(
            [
                "`libs/example.py` exists.",
                "`ExistingService` is current.",
                "`ExistingService.run` is current.",
                "`check_existing_gate.py` is wired.",
            ]
        ),
    )
    _write_python(
        tmp_path,
        "libs/example.py",
        "class ExistingService:\n    def run(self) -> None:\n        pass\n",
    )
    _write_python(tmp_path, "scripts/quality/check_existing_gate.py", "def main() -> int:\n    return 0\n")

    assert gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs", tmp_path / "scripts"), root=tmp_path) == []


def test_doc_drift_accepts_repo_root_inside_ignored_parent_name(tmp_path: Path) -> None:
    root = tmp_path / ".claude" / "worktrees" / "repo"
    doc = _write_doc(root, "`check_existing_gate.py` is wired.\n", "README.md")
    _write_python(root, "scripts/quality/check_existing_gate.py", "def main() -> int:\n    return 0\n")

    findings = gate.collect_findings(docs=(doc,), code_roots=(root / "scripts",), root=root)

    assert findings == []


def test_doc_drift_default_scan_ignores_playwright_failure_artifacts(tmp_path: Path) -> None:
    _write_doc(
        tmp_path,
        "Failed request `/api/pipelines/branches/${branch.id}/graph`.\n",
        "test-results/pipeline-builder/error-context.md",
    )
    _write_doc(tmp_path, "# Current documentation\n", "README.md")

    findings = gate.collect_findings(code_roots=(tmp_path / "libs",), root=tmp_path)

    assert findings == []
    assert all("test-results" not in path.parts for path in gate._default_docs(tmp_path))


def test_doc_drift_skips_python_symbols_in_non_current_docs(tmp_path: Path) -> None:
    doc = _write_doc(tmp_path, "`Order` and `Customer` are ontology examples.\n", "README.md")
    _write_python(tmp_path, "libs/example.py", "class ExistingService:\n    pass\n")

    assert gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs",), root=tmp_path) == []


def test_doc_drift_flags_missing_package_script_reference(tmp_path: Path) -> None:
    _write_package(tmp_path, {"quality:doc-drift": "python scripts/quality/check_doc_drift.py"})
    doc = _write_doc(tmp_path, "Current gate is `quality:missing-gate`.\n", "README.md")

    findings = gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs",), root=tmp_path)

    assert findings == [
        gate.DocDriftFinding(
            code="doc_reference_missing_package_script",
            path="README.md",
            line=1,
            reference="quality:missing-gate",
            message="Document references a package.json script that does not exist in the current tree",
        )
    ]


def test_doc_drift_flags_missing_pnpm_package_script_reference(tmp_path: Path) -> None:
    _write_package(tmp_path, {"quality:doc-drift": "python scripts/quality/check_doc_drift.py"})
    doc = _write_doc(tmp_path, "Run `pnpm --silent quality:missing-gate` before merge.\n", "README.md")

    findings = gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs",), root=tmp_path)

    assert findings == [
        gate.DocDriftFinding(
            code="doc_reference_missing_package_script",
            path="README.md",
            line=1,
            reference="quality:missing-gate",
            message="Document references a package.json script that does not exist in the current tree",
        )
    ]


def test_doc_drift_accepts_existing_package_script_reference(tmp_path: Path) -> None:
    _write_package(tmp_path, {"quality:doc-drift": "python scripts/quality/check_doc_drift.py"})
    doc = _write_doc(
        tmp_path,
        "Run `quality:doc-drift` and `pnpm --silent quality:doc-drift` before merge.\n",
        "README.md",
    )

    assert gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs",), root=tmp_path) == []


def test_doc_drift_skips_proposed_package_script_section(tmp_path: Path) -> None:
    _write_package(tmp_path, {"quality:doc-drift": "python scripts/quality/check_doc_drift.py"})
    doc = _write_doc(
        tmp_path,
        "## 제안 명령\n\n`quality:future-gate`\n\n`pnpm --silent quality:another-future-gate`\n",
        "docs/current.md",
    )

    assert gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs",), root=tmp_path) == []


def test_doc_drift_skips_future_package_script_reference(tmp_path: Path) -> None:
    _write_package(tmp_path, {"quality:doc-drift": "python scripts/quality/check_doc_drift.py"})
    doc = _write_doc(
        tmp_path,
        "`quality:future-gate`은 후속 slice에서 활성화한다.\n",
        "README.md",
    )

    assert gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs",), root=tmp_path) == []


def test_doc_drift_skips_package_script_wildcard_examples(tmp_path: Path) -> None:
    _write_package(tmp_path, {"quality:doc-drift": "python scripts/quality/check_doc_drift.py"})
    doc = _write_doc(
        tmp_path,
        "`pnpm --silent quality:*`, `pnpm ci:*`, and `pnpm --silent ...` are command families.\n",
        "README.md",
    )

    assert gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs",), root=tmp_path) == []


def test_doc_drift_does_not_treat_pnpm_workspace_as_package_script(tmp_path: Path) -> None:
    _write_package(tmp_path, {"quality:doc-drift": "python scripts/quality/check_doc_drift.py"})
    doc = _write_doc(tmp_path, "The monorepo uses pnpm workspace plus uv workspace.\n", "README.md")

    assert gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs",), root=tmp_path) == []


def test_doc_drift_validates_api_routes(tmp_path: Path) -> None:
    doc = _write_doc(tmp_path, "The route is `/api/datasets`.\n", "README.md")
    _write_python(
        tmp_path,
        "apps/api/foundry_lite_api/main.py",
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "@app.get('/api/datasets')\n"
        "def list_datasets():\n"
        "    return []\n",
    )

    assert gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "apps",), root=tmp_path) == []


def test_doc_drift_flags_missing_api_route(tmp_path: Path) -> None:
    doc = _write_doc(tmp_path, "The route is `/api/missing`.\n", "README.md")
    _write_python(
        tmp_path,
        "apps/api/foundry_lite_api/main.py",
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "@app.get('/api/datasets')\n"
        "def list_datasets():\n"
        "    return []\n",
    )

    findings = gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "apps",), root=tmp_path)

    assert findings == [
        gate.DocDriftFinding(
            code="doc_reference_missing_route",
            path="README.md",
            line=1,
            reference="/api/missing",
            message="Document references an API route that FastAPI does not expose",
        )
    ]


def test_doc_drift_validates_pytest_nodeids(tmp_path: Path) -> None:
    doc = _write_doc(tmp_path, "`tests/unit/test_example.py::test_exists` proves it.\n", "README.md")
    _write_python(tmp_path, "tests/unit/test_example.py", "def test_exists():\n    assert True\n")

    assert gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs",), root=tmp_path) == []


def test_doc_drift_flags_missing_pytest_nodeid_name(tmp_path: Path) -> None:
    doc = _write_doc(tmp_path, "`tests/unit/test_example.py::test_missing` proves it.\n", "README.md")
    _write_python(tmp_path, "tests/unit/test_example.py", "def test_exists():\n    assert True\n")

    findings = gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs",), root=tmp_path)

    assert findings == [
        gate.DocDriftFinding(
            code="doc_reference_missing_test_name",
            path="README.md",
            line=1,
            reference="tests/unit/test_example.py::test_missing",
            message="Document references a pytest node id whose test is not collectable from the file AST",
        )
    ]


def test_doc_drift_validates_markdown_links_and_anchors(tmp_path: Path) -> None:
    target = _write_doc(tmp_path, "# Current State\n\n## 문서 지도\n", "docs/target.md")
    doc = _write_doc(
        tmp_path,
        "# 문서 지도\n\n[target](docs/target.md#current-state) and [same](#문서-지도).\n",
        "README.md",
    )

    assert gate.collect_findings(docs=(doc, target), code_roots=(tmp_path / "libs",), root=tmp_path) == []


def test_doc_drift_flags_missing_markdown_link_target(tmp_path: Path) -> None:
    doc = _write_doc(tmp_path, "[missing](docs/missing.md)\n", "README.md")

    findings = gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs",), root=tmp_path)

    assert findings == [
        gate.DocDriftFinding(
            code="doc_markdown_link_missing_target",
            path="README.md",
            line=1,
            reference="docs/missing.md",
            message="Document has a Markdown link whose local target file does not exist",
        )
    ]


def test_doc_drift_flags_missing_markdown_heading_anchor(tmp_path: Path) -> None:
    target = _write_doc(tmp_path, "# Current State\n", "docs/target.md")
    doc = _write_doc(tmp_path, "[bad anchor](docs/target.md#missing-heading)\n", "README.md")

    findings = gate.collect_findings(docs=(doc, target), code_roots=(tmp_path / "libs",), root=tmp_path)

    assert findings == [
        gate.DocDriftFinding(
            code="doc_markdown_link_missing_anchor",
            path="README.md",
            line=1,
            reference="docs/target.md#missing-heading",
            message="Document has a Markdown link whose local heading anchor does not exist",
        )
    ]


def test_doc_drift_ignores_markdown_link_syntax_inside_code_spans(tmp_path: Path) -> None:
    doc = _write_doc(tmp_path, "`[label](docs/missing.md#anchor)` is documentation syntax.\n", "README.md")

    assert gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs",), root=tmp_path) == []


def test_doc_drift_skips_future_or_negative_gap_wording(tmp_path: Path) -> None:
    doc = _write_doc(
        tmp_path,
        "`WorkflowAdapter` remains unextracted, and `FoundryLite.__getattr__` has been removed.\n",
    )
    _write_python(tmp_path, "libs/foundry.py", "class FoundryLite:\n    pass\n")

    assert gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs",), root=tmp_path) == []


def test_doc_drift_skips_runtime_generated_roots_with_trailing_slash(tmp_path: Path) -> None:
    doc = _write_doc(
        tmp_path,
        "`pnpm demo:supply-chain` uses `.foundry-lite-demo/` for repeatable local state.\n",
    )
    _write_python(tmp_path, "libs/example.py", "class ExistingService:\n    pass\n")

    assert gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs",), root=tmp_path) == []


def test_doc_drift_writes_json_report(tmp_path: Path) -> None:
    doc = _write_doc(tmp_path, "`MissingService` is now implemented.\n", "docs/implementation-status.md")
    output = tmp_path / "artifacts" / "quality" / "doc_drift.json"
    findings = gate.collect_findings(docs=(doc,), code_roots=(tmp_path / "libs",), root=tmp_path)

    gate.write_report(output, findings, docs=(doc,))

    report = output.read_text(encoding="utf-8")
    assert '"gate_pass": false' in report
    assert '"doc_reference_missing_symbol"' in report
