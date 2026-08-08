"""Validate the documentation operating map.

The documentation map is the repo-level index for source-of-truth documents,
update order, and cross-check commands. This gate keeps that map from becoming
another stale document by checking the inventory and the commands it publishes.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAP = ROOT / "docs" / "documentation-map.md"
DEFAULT_README = ROOT / "README.md"
DEFAULT_PACKAGE = ROOT / "package.json"
DEFAULT_AGENTS = ROOT / "AGENTS.md"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "documentation_map.json"

IGNORED_DOC_PARTS = {
    ".claude",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "artifacts",
    "htmlcov",
    "mutants",
    "node_modules",
    "test-results",
    "__pycache__",
}
REQUIRED_SOURCE_OF_TRUTH_DOCS = {
    "docs/documentation-map.md",
    "docs/implementation-status.md",
    "docs/sprint-evidence-ledger.md",
    "foundry_lite_development_plan_ko_sprintified.md",
    "foundry_lite_sprint_breakdown_ko.md",
    "README.md",
    "docs/data-engineering-pattern-matrix.json",
    "docs/data-platform-expansion-sprint-plan-ko.md",
    "docs/frontend-api-sdk-surface-matrix.json",
    "docs/frontend-backend-surface-contract.md",
    "docs/commit-point-risk-register.md",
    "docs/infra-ratchet.md",
    "docs/infra-tricky-matrix.json",
    "docs/foundry_lite_tricky_failure_modes_checklist.md",
    "docs/quality-gate-roadmap.md",
}
REQUIRED_README_LINKS = {
    "foundry_lite_development_plan_ko_sprintified.md",
    "foundry_lite_sprint_breakdown_ko.md",
    "docs/commit-point-risk-register.md",
    "docs/data-engineering-pattern-matrix.json",
    "docs/data-platform-expansion-sprint-plan-ko.md",
    "docs/documentation-map.md",
    "docs/foundry_lite_tricky_failure_modes_checklist.md",
    "docs/implementation-status.md",
    "docs/infra-ratchet.md",
    "docs/infra-tricky-matrix.json",
    "docs/sprint-evidence-ledger.md",
    "docs/frontend-api-sdk-surface-matrix.json",
    "docs/frontend-backend-surface-contract.md",
    "docs/quality-gate-roadmap.md",
}
REQUIRED_UPDATE_ORDER_REFS = (
    "docs/sprint-evidence-ledger.md",
    "docs/implementation-status.md",
    "docs/data-platform-expansion-sprint-plan-ko.md",
    "foundry_lite_sprint_breakdown_ko.md",
    "docs/frontend-backend-surface-contract.md",
    "docs/infra-ratchet.md",
    "docs/quality-gate-roadmap.md",
    "README.md",
)
REQUIRED_README_GATE_REFS = {
    "check_documentation_map.py",
    "check_frontend_backend_surface.py",
    "quality:documentation-map",
    "quality:frontend-backend-surface",
    "quality:frontend-foundation",
    "quality:operator-evidence",
    "quality:proof-matrix",
    "quality:sdk-request-contract",
    "quality:source-of-truth",
}
REQUIRED_CROSS_CHECK_COMMANDS = (
    "pnpm --silent quality:doc-drift",
    "pnpm --silent quality:evidence-ledger-commands",
    "pnpm --silent quality:documentation-map",
    "pnpm --silent quality:semantic-doc-consistency",
    "pnpm --silent quality:data-pattern-matrix",
    "pnpm --silent quality:data-platform-sprint-status",
    "pnpm --silent quality:proof-matrix",
    "pnpm --silent quality:source-of-truth",
    "pnpm --silent quality:operator-evidence",
    "pnpm --silent quality:frontend-backend-surface",
    "node --experimental-strip-types tests/sdk/request_contract.mjs",
    "pnpm --silent quality:sdk-generated",
    "pnpm --silent quality:frontend-foundation",
    "pnpm --silent quality:insight-review",
    "pnpm --silent ci:gate:static",
)
REQUIRED_AGENTS_GATE_REFS = {
    "scripts/quality/check_doc_drift.py",
    "scripts/quality/check_evidence_ledger_commands.py",
    "scripts/quality/check_documentation_map.py",
    "scripts/quality/check_data_platform_sprint_status.py",
    "scripts/quality/check_operator_evidence_contract.py",
    "scripts/quality/check_proof_matrix.py",
    "scripts/quality/check_source_of_truth_contract.py",
}
OPERATING_DOC_CONTEXT_MARKERS = (
    "**Status:**",
    "**문서 상태:**",
    "**작성일:**",
    "**Last updated:**",
    "**Purpose:**",
    "**목적:**",
    "**Scope:**",
    "**범위:**",
    "**문서 역할:**",
    "**Audience:**",
)
ALLOWED_MECE_BUCKETS = (
    "entrypoint",
    "example",
    "machine-registry",
    "reference",
    "risk-registry",
    "source-of-truth",
    "standard",
    "template",
)

CODE_SPAN_RE = re.compile(r"`([^`]+)`")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
JsonObject = dict[str, Any]
PLACEHOLDER_TABLE_CELLS = {
    "",
    "-",
    "n/a",
    "na",
    "none",
    "old",
    "placeholder",
    "role",
    "note",
    "todo",
    "tbd",
    "governs",
    "update trigger",
    "gate",
}


@dataclass(frozen=True)
class DocumentationMapFinding:
    code: str
    path: str
    reference: str
    message: str


@dataclass(frozen=True)
class MarkdownTableRow:
    cells: tuple[str, ...]
    line_number: int


def _repo_relative(path: Path, *, root: Path = ROOT) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_json(path: Path) -> JsonObject:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{_repo_relative(path)} must contain a JSON object")
    return payload


def _is_ignored_part(part: str) -> bool:
    return part in IGNORED_DOC_PARTS or part.startswith(".foundry-lite")


def _required_docs(root: Path) -> set[str]:
    docs: set[str] = set()
    docs_root = root / "docs"
    for current_dir, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not _is_ignored_part(name)]
        current_path = Path(current_dir)
        if any(_is_ignored_part(part) for part in current_path.relative_to(root).parts):
            continue
        for filename in filenames:
            path = current_path / filename
            if path.suffix == ".md":
                docs.add(_repo_relative(path, root=root))
            elif path.suffix == ".json" and current_path == docs_root:
                docs.add(_repo_relative(path, root=root))
    return docs


def _section_lines(text: str, heading: str) -> list[str]:
    lines = text.splitlines()
    bounds = _section_bounds(lines, heading)
    if bounds is None:
        return []
    start, end = bounds
    return lines[start:end]


def _section_start(lines: list[str], heading: str) -> int | None:
    bounds = _section_bounds(lines, heading)
    if bounds is None:
        return None
    return bounds[0]


def _section_bounds(lines: list[str], heading: str) -> tuple[int, int] | None:
    start: int | None = None
    level: int | None = None
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line.strip())
        if match is None or match.group(2) != heading:
            continue
        start = index + 1
        level = len(match.group(1))
        break
    if start is None or level is None:
        return None
    end = len(lines)
    for index in range(start, len(lines)):
        match = HEADING_RE.match(lines[index].strip())
        if match is not None and len(match.group(1)) <= level:
            end = index
            break
    return start, end


def _doc_refs_from_table_section(text: str, heading: str) -> list[str]:
    return _doc_refs_from_table_rows(_table_rows_from_section(text, heading))


def _doc_refs_from_table_rows(rows: list[MarkdownTableRow]) -> list[str]:
    refs: list[str] = []
    for row in rows:
        refs.extend(_doc_refs_from_text(row.cells[0]))
    return refs


def _table_rows_from_section(text: str, heading: str) -> list[MarkdownTableRow]:
    rows: list[MarkdownTableRow] = []
    lines = text.splitlines()
    bounds = _section_bounds(lines, heading)
    if bounds is None:
        return rows
    start, end = bounds
    for index, line in enumerate(lines[start:end], start=start + 1):
        if not line.lstrip().startswith("|"):
            continue
        cells = tuple(cell.strip() for cell in line.strip().strip("|").split("|"))
        header_cells = {"Bucket", "Document", "Source of truth", "문서", "Gate"}
        if not cells or cells[0].startswith("---") or cells[0] in header_cells:
            continue
        rows.append(MarkdownTableRow(cells=cells, line_number=index))
    return rows


def _doc_refs_from_text(text: str) -> list[str]:
    refs: list[str] = []
    for match in CODE_SPAN_RE.finditer(text):
        ref = _normalise_doc_ref(match.group(1))
        if ref is not None:
            refs.append(ref)
    for match in MARKDOWN_LINK_RE.finditer(text):
        ref = _normalise_doc_ref(match.group(1))
        if ref is not None:
            refs.append(ref)
    return refs


def _normalise_doc_ref(ref: str) -> str | None:
    path = ref.strip().strip("<>").split("#", maxsplit=1)[0]
    if not path or "://" in path or path.startswith("#"):
        return None
    if path.startswith("./"):
        path = path[2:]
    if path.endswith((".md", ".json")):
        return path
    return None


def _package_scripts(package_path: Path) -> set[str]:
    package = _load_json(package_path)
    scripts = package.get("scripts")
    if not isinstance(scripts, dict):
        return set()
    return {name for name in scripts if isinstance(name, str)}


def collect_findings(
    root: Path = ROOT,
    *,
    map_path: Path = DEFAULT_MAP,
    readme_path: Path = DEFAULT_README,
    package_path: Path = DEFAULT_PACKAGE,
    agents_path: Path = DEFAULT_AGENTS,
) -> list[DocumentationMapFinding]:
    paths = _resolved_paths(root, map_path, readme_path, package_path, agents_path)
    missing = _missing_required_file_findings(root, paths)
    if missing:
        return missing

    map_text = _read_text(paths["map"])
    readme_text = _read_text(paths["readme"])
    agents_text = _read_text(paths["agents"])
    scripts = _package_scripts(paths["package"])

    findings: list[DocumentationMapFinding] = []
    findings.extend(_role_inventory_findings(root, paths["map"], map_text))
    findings.extend(_doc_drift_inventory_findings(root, paths["map"]))
    findings.extend(_mece_taxonomy_findings(paths["map"], map_text, root=root))
    findings.extend(_source_of_truth_findings(paths["map"], map_text, root=root))
    findings.extend(_operating_doc_context_findings(root))
    findings.extend(_update_order_findings(paths["map"], map_text, root=root))
    findings.extend(_readme_link_findings(paths["readme"], readme_text, root=root))
    findings.extend(_readme_gate_ref_findings(paths["readme"], readme_text, root=root))
    findings.extend(_cross_check_command_findings(root, paths["map"], map_text, scripts))
    findings.extend(_agents_gate_ref_findings(paths["agents"], agents_text, root=root))
    return findings


def _resolved_paths(root: Path, *paths: Path) -> dict[str, Path]:
    keys = ("map", "readme", "package", "agents")
    return {key: path if path.is_absolute() else root / path for key, path in zip(keys, paths, strict=True)}


def _missing_required_file_findings(root: Path, paths: dict[str, Path]) -> list[DocumentationMapFinding]:
    findings: list[DocumentationMapFinding] = []
    for key, path in paths.items():
        if path.exists():
            continue
        findings.append(
            _finding(
                "missing_required_file",
                path,
                key,
                "Documentation map gate cannot run without this file.",
                root=root,
            )
        )
    return findings


def _role_inventory_findings(root: Path, map_path: Path, map_text: str) -> list[DocumentationMapFinding]:
    required = _required_docs(root)
    declared = _doc_refs_from_table_section(map_text, "Document Roles")
    declared_set = set(declared)
    findings = _missing_role_findings(map_path, required, declared_set, root=root)
    findings.extend(_stale_role_findings(map_path, required, declared_set, root=root))
    findings.extend(_duplicate_role_findings(map_path, declared, root=root))
    findings.extend(_document_role_bucket_findings(map_path, map_text, root=root))
    findings.extend(_document_role_quality_findings(map_path, map_text, root=root))
    return findings


def _missing_role_findings(
    map_path: Path, required: set[str], declared: set[str], *, root: Path
) -> list[DocumentationMapFinding]:
    return [
        _finding(
            "missing_document_role",
            map_path,
            path,
            "Every Markdown doc and top-level docs/*.json file must be listed in Document Roles.",
            root=root,
        )
        for path in sorted(required - declared)
    ]


def _stale_role_findings(
    map_path: Path, required: set[str], declared: set[str], *, root: Path
) -> list[DocumentationMapFinding]:
    return [
        _finding(
            "stale_document_role",
            map_path,
            path,
            "Document Roles references a doc that no longer exists in this repo.",
            root=root,
        )
        for path in sorted(declared - required)
    ]


def _duplicate_role_findings(map_path: Path, declared: list[str], *, root: Path) -> list[DocumentationMapFinding]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for path in declared:
        if path in seen:
            duplicates.add(path)
        seen.add(path)
    return [
        _finding("duplicate_document_role", map_path, path, "Document Roles contains a duplicate doc row.", root=root)
        for path in sorted(duplicates)
    ]


def _doc_drift_inventory_findings(root: Path, map_path: Path) -> list[DocumentationMapFinding]:
    documentation_map_docs = _required_docs(root)
    doc_drift_docs = {_repo_relative(path, root=root) for path in _doc_drift_default_docs(root)}
    findings = [
        _finding(
            "doc_drift_missing_document",
            map_path,
            path,
            "Document Roles includes a doc that the doc-drift gate does not scan.",
            root=root,
        )
        for path in sorted(documentation_map_docs - doc_drift_docs)
    ]
    findings.extend(
        _finding(
            "doc_drift_unmapped_document",
            map_path,
            path,
            "Doc-drift scans a doc that is not included in Document Roles.",
            root=root,
        )
        for path in sorted(doc_drift_docs - documentation_map_docs)
    )
    return findings


def _doc_drift_default_docs(root: Path) -> tuple[Path, ...]:
    module_path = ROOT / "scripts" / "quality" / "check_doc_drift.py"
    module_name = "_foundry_lite_check_doc_drift_for_docs_map"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load doc-drift gate from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    default_docs = cast(Callable[[Path], tuple[Path, ...]], module.__dict__["_default_docs"])
    return default_docs(root)


def _source_of_truth_findings(map_path: Path, map_text: str, *, root: Path) -> list[DocumentationMapFinding]:
    declared = _doc_refs_from_table_section(map_text, "Source Of Truth Rules")
    declared_set = set(declared)
    findings = [
        _finding(
            "missing_source_of_truth_rule",
            map_path,
            path,
            "Core operating docs must be listed in Source Of Truth Rules with an update trigger.",
            root=root,
        )
        for path in sorted(REQUIRED_SOURCE_OF_TRUTH_DOCS - declared_set)
    ]
    findings.extend(
        _finding(
            "stale_source_of_truth_rule",
            map_path,
            path,
            "Source Of Truth Rules references a doc that is not a current core operating source of truth.",
            root=root,
        )
        for path in sorted(declared_set - REQUIRED_SOURCE_OF_TRUTH_DOCS)
    )
    findings.extend(
        _finding(
            "duplicate_source_of_truth_rule",
            map_path,
            path,
            "Source Of Truth Rules contains a duplicate source-of-truth row.",
            root=root,
        )
        for path in _duplicate_refs(declared)
    )
    findings.extend(_source_of_truth_quality_findings(map_path, map_text, root=root))
    return findings


def _operating_doc_context_findings(root: Path) -> list[DocumentationMapFinding]:
    findings: list[DocumentationMapFinding] = []
    for path in sorted(REQUIRED_SOURCE_OF_TRUTH_DOCS - {"README.md"}):
        if not path.endswith(".md"):
            continue
        doc_path = root / path
        if not doc_path.exists():
            continue
        header = "\n".join(doc_path.read_text(encoding="utf-8").splitlines()[:24])
        if any(marker in header for marker in OPERATING_DOC_CONTEXT_MARKERS):
            continue
        findings.append(
            _finding(
                "missing_operating_doc_context",
                doc_path,
                "top matter",
                "Core operating docs must open with Status/Purpose/Scope/Audience-style context.",
                root=root,
            )
        )
    return findings


def _document_role_quality_findings(map_path: Path, map_text: str, *, root: Path) -> list[DocumentationMapFinding]:
    return _table_quality_findings(
        map_path,
        _table_rows_from_section(map_text, "Document Roles"),
        column_names=("Document", "MECE bucket", "Role", "Current refactor note"),
        code="thin_document_role_row",
        message="Document Roles rows must classify the document and explain its role/current refactor note.",
        root=root,
    )


def _document_role_bucket_findings(map_path: Path, map_text: str, *, root: Path) -> list[DocumentationMapFinding]:
    findings: list[DocumentationMapFinding] = []
    for row in _table_rows_from_section(map_text, "Document Roles"):
        reference = _row_reference(row)
        if len(row.cells) < 4:
            findings.append(
                _finding(
                    "missing_document_role_bucket",
                    map_path,
                    reference,
                    "Document Roles rows must classify each document with a MECE bucket.",
                    root=root,
                )
            )
            continue
        bucket = _normalise_bucket(row.cells[1])
        if bucket not in ALLOWED_MECE_BUCKETS:
            findings.append(
                _finding(
                    "invalid_document_role_bucket",
                    map_path,
                    f"{reference} / {row.cells[1]}",
                    "Document Roles MECE bucket must use the locked documentation taxonomy.",
                    root=root,
                )
            )
    return findings


def _mece_taxonomy_findings(map_path: Path, map_text: str, *, root: Path) -> list[DocumentationMapFinding]:
    rows = _table_rows_from_section(map_text, "MECE Documentation Taxonomy")
    if not rows:
        return [
            _finding(
                "missing_mece_taxonomy",
                map_path,
                "MECE Documentation Taxonomy",
                "docs/documentation-map.md must define the documentation MECE bucket taxonomy.",
                root=root,
            )
        ]
    bucket_refs = [_normalise_bucket(row.cells[0]) for row in rows if row.cells]
    bucket_set = set(bucket_refs)
    findings = [
        _finding(
            "missing_mece_bucket_definition",
            map_path,
            bucket,
            "MECE Documentation Taxonomy must define this locked bucket.",
            root=root,
        )
        for bucket in ALLOWED_MECE_BUCKETS
        if bucket not in bucket_set
    ]
    findings.extend(
        _finding(
            "stale_mece_bucket_definition",
            map_path,
            bucket,
            "MECE Documentation Taxonomy defines a bucket that the gate does not allow.",
            root=root,
        )
        for bucket in sorted(bucket_set - set(ALLOWED_MECE_BUCKETS))
    )
    findings.extend(
        _finding(
            "duplicate_mece_bucket_definition",
            map_path,
            bucket,
            "MECE Documentation Taxonomy defines the same bucket more than once.",
            root=root,
        )
        for bucket in _duplicate_refs(bucket_refs)
    )
    findings.extend(_mece_taxonomy_quality_findings(map_path, rows, root=root))
    return findings


def _mece_taxonomy_quality_findings(
    map_path: Path, rows: list[MarkdownTableRow], *, root: Path
) -> list[DocumentationMapFinding]:
    findings: list[DocumentationMapFinding] = []
    column_names = ("Bucket", "Meaning", "Merge/retire rule")
    message = "MECE Documentation Taxonomy rows must explain the bucket meaning and merge/retire rule."
    for row in rows:
        reference = _normalise_bucket(row.cells[0]) if row.cells else f"line {row.line_number}"
        if len(row.cells) < len(column_names):
            findings.append(_finding("thin_mece_taxonomy_row", map_path, reference, message, root=root))
            continue
        for column_index, column_name in enumerate(column_names[1:], start=1):
            if not _is_substantive_table_cell(row.cells[column_index]):
                findings.append(
                    _finding(
                        "thin_mece_taxonomy_row",
                        map_path,
                        f"{reference} / {column_name}",
                        message,
                        root=root,
                    )
                )
    return findings


def _source_of_truth_quality_findings(map_path: Path, map_text: str, *, root: Path) -> list[DocumentationMapFinding]:
    return _table_quality_findings(
        map_path,
        _table_rows_from_section(map_text, "Source Of Truth Rules"),
        column_names=("Source of truth", "Governs", "When to update"),
        code="thin_source_of_truth_row",
        message="Source Of Truth Rules rows must explain what the doc governs and when to update it.",
        root=root,
    )


def _table_quality_findings(
    map_path: Path,
    rows: list[MarkdownTableRow],
    *,
    column_names: tuple[str, ...],
    code: str,
    message: str,
    root: Path,
) -> list[DocumentationMapFinding]:
    findings: list[DocumentationMapFinding] = []
    for row in rows:
        reference = _row_reference(row)
        if len(row.cells) < len(column_names):
            findings.append(_finding(code, map_path, reference, message, root=root))
            continue
        for column_index, column_name in enumerate(column_names[1:], start=1):
            if not _is_substantive_table_cell(row.cells[column_index]):
                findings.append(
                    _finding(
                        code,
                        map_path,
                        f"{reference} / {column_name}",
                        message,
                        root=root,
                    )
                )
    return findings


def _row_reference(row: MarkdownTableRow) -> str:
    refs = _doc_refs_from_text(row.cells[0]) if row.cells else []
    if refs:
        return refs[0]
    return f"line {row.line_number}"


def _is_substantive_table_cell(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", value.strip()).lower()
    return normalized not in PLACEHOLDER_TABLE_CELLS and len(normalized) >= 8


def _normalise_bucket(value: str) -> str:
    return value.strip().strip("`").lower()


def _duplicate_refs(refs: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for ref in refs:
        if ref in seen:
            duplicates.add(ref)
        seen.add(ref)
    return sorted(duplicates)


def _update_order_findings(map_path: Path, map_text: str, *, root: Path) -> list[DocumentationMapFinding]:
    refs = _doc_refs_from_text("\n".join(_section_lines(map_text, "Update Order")))
    ref_set = set(refs)
    findings = [
        _finding(
            "missing_update_order_reference",
            map_path,
            ref,
            "Update Order must mention this document so maintainers update source docs before README.",
            root=root,
        )
        for ref in REQUIRED_UPDATE_ORDER_REFS
        if ref not in ref_set
    ]
    findings.extend(
        _finding(
            "duplicate_update_order_reference",
            map_path,
            ref,
            "Update Order references this document more than once.",
            root=root,
        )
        for ref in _duplicate_refs(refs)
    )
    findings.extend(_readme_update_order_findings(map_path, refs, root=root))
    return findings


def _readme_update_order_findings(map_path: Path, refs: list[str], *, root: Path) -> list[DocumentationMapFinding]:
    if "README.md" not in refs:
        return []
    readme_index = refs.index("README.md")
    return [
        _finding(
            "readme_not_last_update_order",
            map_path,
            ref,
            "README.md must be updated after the source-of-truth/evidence documents in Update Order.",
            root=root,
        )
        for ref in REQUIRED_UPDATE_ORDER_REFS
        if ref != "README.md" and ref in refs and refs.index(ref) > readme_index
    ]


def _readme_link_findings(readme_path: Path, readme_text: str, *, root: Path) -> list[DocumentationMapFinding]:
    rows = _table_rows_from_section(readme_text, "문서 지도")
    readme_link_refs = _doc_refs_from_table_rows(rows)
    readme_links = set(readme_link_refs)
    findings = [
        _finding(
            "missing_readme_document_map_link",
            readme_path,
            path,
            "README 문서 지도 must link to this core operating document.",
            root=root,
        )
        for path in sorted(REQUIRED_README_LINKS - readme_links)
    ]
    findings.extend(
        _finding(
            "duplicate_readme_document_map_link",
            readme_path,
            path,
            "README 문서 지도 references this document more than once.",
            root=root,
        )
        for path in _duplicate_refs(readme_link_refs)
    )
    findings.extend(_readme_document_map_quality_findings(readme_path, rows, root=root))
    return findings


def _readme_document_map_quality_findings(
    readme_path: Path, rows: list[MarkdownTableRow], *, root: Path
) -> list[DocumentationMapFinding]:
    findings: list[DocumentationMapFinding] = []
    for row in rows:
        refs = _doc_refs_from_text(row.cells[0]) if row.cells else []
        if not refs:
            continue
        reference = refs[0]
        if len(row.cells) < 2 or not _is_substantive_table_cell(row.cells[1]):
            findings.append(
                _finding(
                    "thin_readme_document_map_row",
                    readme_path,
                    f"{reference} / 역할",
                    "README 문서 지도 rows must explain why each document matters to a GitHub reader.",
                    root=root,
                )
            )
    return findings


def _readme_gate_ref_findings(readme_path: Path, readme_text: str, *, root: Path) -> list[DocumentationMapFinding]:
    rows = _table_rows_from_section(readme_text, "대표 gate")
    readme_gate_refs = _readme_gate_refs_from_table_rows(rows)
    readme_gate_ref_set = set(readme_gate_refs)
    findings = [
        _finding(
            "missing_readme_gate_reference",
            readme_path,
            reference,
            "README must brief this active documentation/API/SDK quality gate.",
            root=root,
        )
        for reference in sorted(REQUIRED_README_GATE_REFS)
        if reference not in readme_gate_ref_set
    ]
    findings.extend(
        _finding(
            "duplicate_readme_gate_reference",
            readme_path,
            reference,
            "README 대표 gate table references this active gate more than once.",
            root=root,
        )
        for reference in _duplicate_refs(readme_gate_refs)
    )
    findings.extend(_readme_gate_quality_findings(readme_path, rows, root=root))
    return findings


def _readme_gate_refs_from_table_rows(rows: list[MarkdownTableRow]) -> list[str]:
    refs: list[str] = []
    for row in rows:
        refs.extend(_readme_gate_refs_from_row(row))
    return refs


def _readme_gate_refs_from_row(row: MarkdownTableRow) -> list[str]:
    first_cell = row.cells[0] if row.cells else ""
    return [reference for reference in sorted(REQUIRED_README_GATE_REFS) if reference in first_cell]


def _readme_gate_quality_findings(
    readme_path: Path, rows: list[MarkdownTableRow], *, root: Path
) -> list[DocumentationMapFinding]:
    findings: list[DocumentationMapFinding] = []
    for row in rows:
        reference = _readme_gate_row_reference(row)
        if len(row.cells) < 2 or not _is_substantive_table_cell(row.cells[1]):
            findings.append(
                _finding(
                    "thin_readme_gate_row",
                    readme_path,
                    f"{reference} / 막는 문제",
                    "README 대표 gate rows must explain the root-cause risk blocked by each gate.",
                    root=root,
                )
            )
    return findings


def _readme_gate_row_reference(row: MarkdownTableRow) -> str:
    refs = _readme_gate_refs_from_row(row)
    if refs:
        return refs[0]
    if row.cells:
        match = CODE_SPAN_RE.search(row.cells[0])
        if match is not None:
            return match.group(1)
    return f"line {row.line_number}"


def _cross_check_command_findings(
    root: Path, map_path: Path, map_text: str, scripts: set[str]
) -> list[DocumentationMapFinding]:
    findings: list[DocumentationMapFinding] = []
    commands = _cross_check_commands(map_text)
    command_set = set(commands)
    for command in REQUIRED_CROSS_CHECK_COMMANDS:
        if command not in command_set:
            findings.append(_command_finding(map_path, command, "missing_cross_check_command", root=root))
            continue
        findings.extend(_command_target_findings(root, map_path, command, scripts))
    findings.extend(
        _command_finding(
            map_path,
            command,
            "stale_cross_check_command",
            message=(
                "Cross-check command list contains a command that is not part of the locked "
                "documentation refactor checklist."
            ),
            root=root,
        )
        for command in sorted(command_set - set(REQUIRED_CROSS_CHECK_COMMANDS))
    )
    findings.extend(
        _command_finding(
            map_path,
            command,
            "duplicate_cross_check_command",
            message="Cross-check command list contains a duplicate command.",
            root=root,
        )
        for command in _duplicate_refs(commands)
    )
    return findings


def _cross_check_commands(map_text: str) -> list[str]:
    commands: list[str] = []
    in_code_block = False
    for line in _section_lines(map_text, "Cross-Check Commands"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if not in_code_block or not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(("pnpm ", "node ")):
            commands.append(stripped)
    return commands


def _agents_gate_ref_findings(agents_path: Path, agents_text: str, *, root: Path) -> list[DocumentationMapFinding]:
    return [
        _finding(
            "missing_agents_gate_reference",
            agents_path,
            reference,
            "AGENTS.md must summarize active documentation gates so maintainers see them before editing.",
            root=root,
        )
        for reference in sorted(REQUIRED_AGENTS_GATE_REFS)
        if reference not in agents_text
    ]


def _command_target_findings(
    root: Path, map_path: Path, command: str, scripts: set[str]
) -> list[DocumentationMapFinding]:
    if command.startswith("pnpm "):
        return _pnpm_command_findings(map_path, command, scripts, root=root)
    if command.startswith("node "):
        return _node_command_findings(root, map_path, command)
    return []


def _pnpm_command_findings(
    map_path: Path, command: str, scripts: set[str], *, root: Path
) -> list[DocumentationMapFinding]:
    script_name = next((part for part in command.split()[1:] if ":" in part), "")
    if not script_name or script_name in scripts:
        return []
    return [
        _command_finding(
            map_path,
            command,
            "cross_check_command_missing_package_script",
            message=f"Cross-check command references missing package script {script_name}.",
            root=root,
        )
    ]


def _node_command_findings(root: Path, map_path: Path, command: str) -> list[DocumentationMapFinding]:
    # The script is the first non-flag argument, so a runtime flag such as
    # --experimental-strip-types does not read as a missing file.
    parts = [part for part in command.split()[1:] if not part.startswith("-")]
    if not parts or (root / parts[0]).exists():
        return []
    return [
        _command_finding(
            map_path,
            command,
            "cross_check_command_missing_file",
            message="Cross-check command references a file that does not exist.",
            root=root,
        )
    ]


def _command_finding(
    map_path: Path,
    command: str,
    code: str,
    *,
    message: str = "Cross-check command must be listed in docs/documentation-map.md.",
    root: Path,
) -> DocumentationMapFinding:
    return _finding(code, map_path, command, message, root=root)


def _finding(code: str, path: Path, reference: str, message: str, *, root: Path) -> DocumentationMapFinding:
    return DocumentationMapFinding(
        code=code,
        path=_repo_relative(path, root=root),
        reference=reference,
        message=message,
    )


def write_report(output: Path, findings: list[DocumentationMapFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "FAIL" if findings else "PASS",
        "count": len(findings),
        "findings": [asdict(finding) for finding in findings],
        "requiredSourceOfTruthDocs": sorted(REQUIRED_SOURCE_OF_TRUTH_DOCS),
        "requiredUpdateOrderRefs": list(REQUIRED_UPDATE_ORDER_REFS),
        "requiredReadmeLinks": sorted(REQUIRED_README_LINKS),
        "requiredReadmeGateRefs": sorted(REQUIRED_README_GATE_REFS),
        "requiredOperatingDocContextMarkers": list(OPERATING_DOC_CONTEXT_MARKERS),
        "allowedMeceBuckets": list(ALLOWED_MECE_BUCKETS),
        "requiredCrossCheckCommands": list(REQUIRED_CROSS_CHECK_COMMANDS),
        "requiredAgentsGateRefs": sorted(REQUIRED_AGENTS_GATE_REFS),
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[DocumentationMapFinding], output: Path) -> None:
    print("Documentation map gate failed:")
    for finding in findings:
        print(f"- {finding.code} {finding.path} [{finding.reference}]: {finding.message}")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check docs/documentation-map.md consistency.")
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--readme", type=Path, default=DEFAULT_README)
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--agents", type=Path, default=DEFAULT_AGENTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    findings = collect_findings(
        map_path=args.map,
        readme_path=args.readme,
        package_path=args.package,
        agents_path=args.agents,
    )
    write_report(args.output, findings)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print(
        "Documentation map gate passed: document roles, MECE buckets, source-of-truth rules, row descriptions, "
        "operating doc context, update order, README links/descriptions, "
        "README gate references/descriptions, cross-check commands, and AGENTS gate references are aligned."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
