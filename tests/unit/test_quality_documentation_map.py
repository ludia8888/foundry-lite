from __future__ import annotations

import json
from pathlib import Path

from pytest import MonkeyPatch

from scripts.quality import check_documentation_map as gate


def test_documentation_map_gate_passes_current_repo() -> None:
    assert gate.collect_findings() == []


def test_documentation_map_requires_every_markdown_doc_role(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    (tmp_path / "docs" / "extra-runbook.md").write_text("# Extra\n", encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "missing_document_role" for finding in findings)
    assert any(finding.reference == "docs/extra-runbook.md" for finding in findings)


def test_documentation_map_ignores_playwright_error_context(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    error_context = tmp_path / "test-results" / "failed-browser-case" / "error-context.md"
    error_context.parent.mkdir(parents=True)
    error_context.write_text("# Generated browser failure context\n", encoding="utf-8")

    findings = _collect(tmp_path)

    assert not any(finding.reference.endswith("error-context.md") for finding in findings)


def test_documentation_map_rejects_stale_document_role(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path, extra_role="docs/missing-runbook.md")

    findings = _collect(tmp_path)

    assert any(finding.code == "stale_document_role" for finding in findings)
    assert any(finding.reference == "docs/missing-runbook.md" for finding in findings)


def test_documentation_map_requires_doc_drift_to_scan_every_document_role(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _write_documentation_tree(tmp_path)
    current_docs = gate._doc_drift_default_docs(tmp_path)
    monkeypatch.setattr(
        gate,
        "_doc_drift_default_docs",
        lambda root: tuple(path for path in current_docs if path.name != "implementation-status.md"),
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "doc_drift_missing_document" for finding in findings)
    assert any(finding.reference == "docs/implementation-status.md" for finding in findings)


def test_documentation_map_rejects_doc_drift_docs_missing_from_document_roles(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _write_documentation_tree(tmp_path)
    extra_doc = tmp_path / "docs" / "unmapped-doc.md"
    current_docs = gate._doc_drift_default_docs(tmp_path)
    monkeypatch.setattr(gate, "_doc_drift_default_docs", lambda root: (*current_docs, extra_doc))

    findings = _collect(tmp_path)

    assert any(finding.code == "doc_drift_unmapped_document" for finding in findings)
    assert any(finding.reference == "docs/unmapped-doc.md" for finding in findings)


def test_documentation_map_requires_core_readme_links(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace("docs/sprint-evidence-ledger.md", ""),
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "missing_readme_document_map_link" for finding in findings)
    assert any(finding.reference == "docs/sprint-evidence-ledger.md" for finding in findings)


def test_documentation_map_requires_substantive_readme_document_map_descriptions(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    readme = tmp_path / "README.md"
    row = _readme_row("docs/implementation-status.md")
    readme.write_text(
        readme.read_text(encoding="utf-8").replace(
            row,
            "| [docs/implementation-status.md](docs/implementation-status.md) | role |",
        ),
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "thin_readme_document_map_row" for finding in findings)
    assert any(finding.reference == "docs/implementation-status.md / 역할" for finding in findings)


def test_documentation_map_rejects_duplicate_readme_document_map_links(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    readme = tmp_path / "README.md"
    row = f"{_readme_row('docs/implementation-status.md')}\n"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace(row, f"{row}{row}"),
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "duplicate_readme_document_map_link" for finding in findings)
    assert any(finding.reference == "docs/implementation-status.md" for finding in findings)


def test_documentation_map_requires_readme_gate_briefing(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace(f"{_gate_row('check_frontend_backend_surface.py')}\n", ""),
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "missing_readme_gate_reference" for finding in findings)
    assert any(finding.reference == "check_frontend_backend_surface.py" for finding in findings)


def test_documentation_map_requires_readme_gate_refs_in_representative_table(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace(f"{_gate_row('quality:frontend-foundation')}\n", "")
        + "\nOutside the table we mention `quality:frontend-foundation` for context.\n",
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "missing_readme_gate_reference" for finding in findings)
    assert any(finding.reference == "quality:frontend-foundation" for finding in findings)


def test_documentation_map_requires_substantive_readme_gate_descriptions(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    readme = tmp_path / "README.md"
    row = _gate_row("quality:frontend-foundation")
    readme.write_text(
        readme.read_text(encoding="utf-8").replace(row, "| `quality:frontend-foundation` | gate |"),
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "thin_readme_gate_row" for finding in findings)
    assert any(finding.reference == "quality:frontend-foundation / 막는 문제" for finding in findings)


def test_documentation_map_rejects_duplicate_readme_gate_refs(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    readme = tmp_path / "README.md"
    row = f"{_gate_row('quality:frontend-foundation')}\n"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace(row, f"{row}{row}"),
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "duplicate_readme_gate_reference" for finding in findings)
    assert any(finding.reference == "quality:frontend-foundation" for finding in findings)


def test_documentation_map_requires_data_pattern_source_of_truth_rule(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    doc_map = tmp_path / "docs" / "documentation-map.md"
    doc_map.write_text(
        doc_map.read_text(encoding="utf-8").replace(
            f"{_source_row('docs/data-engineering-pattern-matrix.json')}\n",
            "",
        ),
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "missing_source_of_truth_rule" for finding in findings)
    assert any(finding.reference == "docs/data-engineering-pattern-matrix.json" for finding in findings)


def test_documentation_map_requires_core_operating_doc_context(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    doc_path = tmp_path / "docs" / "implementation-status.md"
    doc_path.write_text("# Implementation Status\n\nThis document lost its operating context.\n", encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "missing_operating_doc_context" for finding in findings)
    assert any(finding.path == "docs/implementation-status.md" for finding in findings)


def test_documentation_map_rejects_stale_source_of_truth_rule(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    doc_map = tmp_path / "docs" / "documentation-map.md"
    doc_map.write_text(
        doc_map.read_text(encoding="utf-8").replace(
            "## Update Order\n",
            "| `docs/retired-source.md` | Retired source document. | Should not remain in the map. |\n\n"
            "## Update Order\n",
        ),
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "stale_source_of_truth_rule" for finding in findings)
    assert any(finding.reference == "docs/retired-source.md" for finding in findings)


def test_documentation_map_rejects_duplicate_source_of_truth_rule(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    doc_map = tmp_path / "docs" / "documentation-map.md"
    row = f"{_source_row('docs/implementation-status.md')}\n"
    doc_map.write_text(
        doc_map.read_text(encoding="utf-8").replace(row, f"{row}{row}"),
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "duplicate_source_of_truth_rule" for finding in findings)
    assert any(finding.reference == "docs/implementation-status.md" for finding in findings)


def test_documentation_map_requires_substantive_source_of_truth_cells(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    doc_map = tmp_path / "docs" / "documentation-map.md"
    row = _source_row("docs/implementation-status.md")
    doc_map.write_text(
        doc_map.read_text(encoding="utf-8").replace(
            row,
            "| `docs/implementation-status.md` | governs | update trigger |",
        ),
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "thin_source_of_truth_row" for finding in findings)
    assert any(finding.reference == "docs/implementation-status.md / Governs" for finding in findings)


def test_documentation_map_requires_substantive_document_role_cells(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    doc_map = tmp_path / "docs" / "documentation-map.md"
    row = _role_row("docs/implementation-status.md")
    doc_map.write_text(
        doc_map.read_text(encoding="utf-8").replace(
            row,
            "| `docs/implementation-status.md` | source-of-truth | role | note |",
        ),
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "thin_document_role_row" for finding in findings)
    assert any(finding.reference == "docs/implementation-status.md / Role" for finding in findings)


def test_documentation_map_requires_mece_taxonomy_definitions(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    doc_map = tmp_path / "docs" / "documentation-map.md"
    doc_map.write_text(
        doc_map.read_text(encoding="utf-8").replace(f"{_taxonomy_row('machine-registry')}\n", ""),
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "missing_mece_bucket_definition" for finding in findings)
    assert any(finding.reference == "machine-registry" for finding in findings)


def test_documentation_map_rejects_stale_mece_taxonomy_bucket(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    doc_map = tmp_path / "docs" / "documentation-map.md"
    doc_map.write_text(
        doc_map.read_text(encoding="utf-8").replace(
            "## Document Roles\n",
            "| `archive` | Old archive bucket. | Retired bucket should not remain active. |\n\n## Document Roles\n",
        ),
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "stale_mece_bucket_definition" for finding in findings)
    assert any(finding.reference == "archive" for finding in findings)


def test_documentation_map_requires_substantive_mece_taxonomy_cells(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    doc_map = tmp_path / "docs" / "documentation-map.md"
    row = _taxonomy_row("machine-registry")
    doc_map.write_text(
        doc_map.read_text(encoding="utf-8").replace(
            row,
            "| `machine-registry` | role | note |",
        ),
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "thin_mece_taxonomy_row" for finding in findings)
    assert any(finding.reference == "machine-registry / Meaning" for finding in findings)


def test_documentation_map_requires_document_role_mece_bucket(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    doc_map = tmp_path / "docs" / "documentation-map.md"
    row = _role_row("docs/implementation-status.md")
    doc_map.write_text(
        doc_map.read_text(encoding="utf-8").replace(
            row,
            "| `docs/implementation-status.md` | Operating document for status. | Keeps status aligned. |",
        ),
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "missing_document_role_bucket" for finding in findings)
    assert any(finding.reference == "docs/implementation-status.md" for finding in findings)


def test_documentation_map_rejects_unknown_document_role_mece_bucket(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    doc_map = tmp_path / "docs" / "documentation-map.md"
    row = _role_row("docs/implementation-status.md")
    doc_map.write_text(
        doc_map.read_text(encoding="utf-8").replace(
            row,
            "| `docs/implementation-status.md` | random-bucket | Operating document for status. | "
            "Keeps status aligned. |",
        ),
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "invalid_document_role_bucket" for finding in findings)
    assert any(finding.reference == "docs/implementation-status.md / random-bucket" for finding in findings)


def test_documentation_map_requires_update_order_refs(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    doc_map = tmp_path / "docs" / "documentation-map.md"
    doc_map.write_text(
        doc_map.read_text(encoding="utf-8").replace("- `docs/sprint-evidence-ledger.md`\n", ""),
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "missing_update_order_reference" for finding in findings)
    assert any(finding.reference == "docs/sprint-evidence-ledger.md" for finding in findings)


def test_documentation_map_requires_readme_last_in_update_order(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    doc_map = tmp_path / "docs" / "documentation-map.md"
    doc_map.write_text(
        doc_map.read_text(encoding="utf-8").replace(
            "## Update Order\n\n- `docs/sprint-evidence-ledger.md`\n- `docs/implementation-status.md`\n",
            "## Update Order\n\n- `README.md`\n- `docs/sprint-evidence-ledger.md`\n- `docs/implementation-status.md`\n",
        ),
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "readme_not_last_update_order" for finding in findings)
    assert any(finding.reference == "docs/sprint-evidence-ledger.md" for finding in findings)


def test_documentation_map_requires_product_surface_cross_check_commands(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    doc_map = tmp_path / "docs" / "documentation-map.md"
    doc_map.write_text(
        doc_map.read_text(encoding="utf-8").replace("pnpm --silent quality:frontend-foundation\n", ""),
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "missing_cross_check_command" for finding in findings)
    assert any(finding.reference == "pnpm --silent quality:frontend-foundation" for finding in findings)


def test_documentation_map_rejects_duplicate_cross_check_command(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    doc_map = tmp_path / "docs" / "documentation-map.md"
    command = "pnpm --silent quality:doc-drift\n"
    doc_map.write_text(doc_map.read_text(encoding="utf-8").replace(command, f"{command}{command}"), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "duplicate_cross_check_command" for finding in findings)
    assert any(finding.reference == "pnpm --silent quality:doc-drift" for finding in findings)


def test_documentation_map_rejects_unregistered_cross_check_command(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    doc_map = tmp_path / "docs" / "documentation-map.md"
    doc_map.write_text(
        doc_map.read_text(encoding="utf-8").replace(
            "pnpm --silent ci:gate:static\n",
            "pnpm --silent ci:gate:static\npnpm --silent quality:unregistered-doc-command\n",
        ),
        encoding="utf-8",
    )

    findings = _collect(tmp_path)

    assert any(finding.code == "stale_cross_check_command" for finding in findings)
    assert any(finding.reference == "pnpm --silent quality:unregistered-doc-command" for finding in findings)


def test_documentation_map_validates_cross_check_package_scripts(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    package_path = tmp_path / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    del package["scripts"]["quality:documentation-map"]
    package_path.write_text(json.dumps(package), encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "cross_check_command_missing_package_script" for finding in findings)


def test_documentation_map_validates_cross_check_node_target(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    (tmp_path / "tests" / "sdk" / "request_contract.mjs").unlink()

    findings = _collect(tmp_path)

    assert any(finding.code == "cross_check_command_missing_file" for finding in findings)
    assert any(
        finding.reference == "node --experimental-strip-types tests/sdk/request_contract.mjs" for finding in findings
    )


def test_documentation_map_requires_agents_gate_references(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")

    findings = _collect(tmp_path)

    assert any(finding.code == "missing_agents_gate_reference" for finding in findings)
    assert any(finding.reference == "scripts/quality/check_documentation_map.py" for finding in findings)
    assert any(finding.reference == "scripts/quality/check_evidence_ledger_commands.py" for finding in findings)
    assert any(finding.reference == "scripts/quality/check_data_platform_sprint_status.py" for finding in findings)
    assert any(finding.reference == "scripts/quality/check_proof_matrix.py" for finding in findings)
    assert any(finding.reference == "scripts/quality/check_source_of_truth_contract.py" for finding in findings)
    assert any(finding.reference == "scripts/quality/check_operator_evidence_contract.py" for finding in findings)


def test_documentation_map_writes_json_report(tmp_path: Path) -> None:
    _write_documentation_tree(tmp_path)
    output = tmp_path / "artifacts" / "quality" / "documentation_map.json"

    findings = _collect(tmp_path)
    gate.write_report(output, findings)

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "PASS"
    assert report["allowedMeceBuckets"]
    assert report["requiredCrossCheckCommands"]
    assert report["requiredOperatingDocContextMarkers"]
    assert report["requiredReadmeGateRefs"]
    assert report["requiredUpdateOrderRefs"]


def _collect(root: Path) -> list[gate.DocumentationMapFinding]:
    return gate.collect_findings(
        root,
        map_path=root / "docs" / "documentation-map.md",
        readme_path=root / "README.md",
        package_path=root / "package.json",
        agents_path=root / "AGENTS.md",
    )


def _write_documentation_tree(root: Path, *, extra_role: str | None = None) -> None:
    (root / ".github").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    (root / "tests" / "sdk").mkdir(parents=True)
    (root / ".github" / "pull_request_template.md").write_text("# PR\n", encoding="utf-8")
    (root / "AGENTS.md").write_text(
        "# Agents\n\n"
        "- `scripts/quality/check_doc_drift.py` checks documentation drift.\n"
        "- `scripts/quality/check_evidence_ledger_commands.py` checks evidence commands.\n"
        "- `scripts/quality/check_documentation_map.py` checks the documentation map.\n"
        "- `scripts/quality/check_data_platform_sprint_status.py` checks sprint status docs.\n"
        "- `scripts/quality/check_proof_matrix.py` checks proof classes.\n"
        "- `scripts/quality/check_source_of_truth_contract.py` checks source-of-truth contracts.\n"
        "- `scripts/quality/check_operator_evidence_contract.py` checks durable operator evidence.\n",
        encoding="utf-8",
    )
    (root / "tests" / "sdk" / "request_contract.mjs").write_text("console.log('ok')\n", encoding="utf-8")
    for path in _minimal_docs():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(_minimal_doc_text(path), encoding="utf-8")
    _write_map(root, extra_role=extra_role)
    _write_readme(root)
    _write_package(root)


def _minimal_docs() -> tuple[Path, ...]:
    return tuple(Path(path) for path in sorted(gate.REQUIRED_SOURCE_OF_TRUTH_DOCS - {"README.md"}))


def _minimal_doc_text(path: Path) -> str:
    if path.suffix == ".json":
        return f"# {path}\n"
    return f"# {path}\n\n**Status:** Test operating document.\n**Purpose:** Keeps fixture docs aligned.\n"


def _write_map(root: Path, *, extra_role: str | None) -> None:
    role_paths = {
        ".github/pull_request_template.md",
        "AGENTS.md",
        "README.md",
        *gate.REQUIRED_SOURCE_OF_TRUTH_DOCS,
    }
    if extra_role is not None:
        role_paths.add(extra_role)
    source_rows = "\n".join(_source_row(path) for path in sorted(gate.REQUIRED_SOURCE_OF_TRUTH_DOCS))
    taxonomy_rows = "\n".join(_taxonomy_row(bucket) for bucket in gate.ALLOWED_MECE_BUCKETS)
    role_rows = "\n".join(_role_row(path) for path in sorted(role_paths))
    commands = "\n".join(gate.REQUIRED_CROSS_CHECK_COMMANDS)
    (root / "docs" / "documentation-map.md").write_text(
        "# Foundry-lite Documentation Map\n\n"
        "**Status:** Test documentation operating map.\n"
        "**Audience:** Test maintainers checking documentation gate fixtures.\n\n"
        "## Source Of Truth Rules\n\n"
        "| Source of truth | Governs | When to update |\n"
        "|---|---|---|\n"
        f"{source_rows}\n\n"
        "## Update Order\n\n"
        f"{_update_order_refs()}\n\n"
        "## MECE Documentation Taxonomy\n\n"
        "| Bucket | Meaning | Merge/retire rule |\n"
        "|---|---|---|\n"
        f"{taxonomy_rows}\n\n"
        "## Document Roles\n\n"
        "| Document | MECE bucket | Role | Current refactor note |\n"
        "|---|---|---|---|\n"
        f"{role_rows}\n\n"
        "## Cross-Check Commands\n\n"
        "```bash\n"
        f"{commands}\n"
        "```\n",
        encoding="utf-8",
    )


def _source_row(path: str) -> str:
    return f"| `{path}` | Owns the current operating contract for {path}. | Update when evidence changes. |"


def _taxonomy_row(bucket: str) -> str:
    return (
        f"| `{bucket}` | Defines the documentation category for {bucket}. | "
        "Merge overlapping claims into the owning source document. |"
    )


def _role_row(path: str) -> str:
    return (
        f"| `{path}` | {_bucket_for_path(path)} | Operating document for {path}. | "
        "Keeps the documentation inventory aligned. |"
    )


def _bucket_for_path(path: str) -> str:
    if path == ".github/pull_request_template.md":
        return "template"
    if path in {"AGENTS.md", "README.md"}:
        return "entrypoint"
    if path.endswith(".json"):
        return "machine-registry"
    if path in {"docs/commit-point-risk-register.md", "docs/foundry_lite_tricky_failure_modes_checklist.md"}:
        return "risk-registry"
    if path == "foundry_lite_python_engineering_guidelines_ko.md":
        return "standard"
    if path == "deep-research-report.md":
        return "reference"
    if path.startswith("examples/"):
        return "example"
    return "source-of-truth"


def _update_order_refs() -> str:
    return "\n".join(f"- `{path}`" for path in gate.REQUIRED_UPDATE_ORDER_REFS)


def _write_readme(root: Path) -> None:
    rows = "\n".join(_readme_row(path) for path in sorted(gate.REQUIRED_README_LINKS))
    gate_rows = "\n".join(_gate_row(reference) for reference in sorted(gate.REQUIRED_README_GATE_REFS))
    (root / "README.md").write_text(
        f"# README\n\n"
        f"### 대표 gate\n\n"
        f"| Gate | 막는 문제 |\n|---|---|\n{gate_rows}\n\n"
        f"## 문서 지도\n\n| 문서 | 역할 |\n|---|---|\n{rows}\n\n"
        f"## Next\n",
        encoding="utf-8",
    )


def _readme_row(path: str) -> str:
    return f"| [{path}]({path}) | Explains the current operating role and evidence path for {path}. |"


def _gate_row(reference: str) -> str:
    return f"| `{reference}` | Blocks the documented release risk guarded by {reference}. |"


def _write_package(root: Path) -> None:
    script_names = {"ci:gate:static"}
    for command in gate.REQUIRED_CROSS_CHECK_COMMANDS:
        if command.startswith("pnpm "):
            script_names.add(next(part for part in command.split()[1:] if ":" in part))
    (root / "package.json").write_text(
        json.dumps({"scripts": {name: "echo ok" for name in sorted(script_names)}}),
        encoding="utf-8",
    )
