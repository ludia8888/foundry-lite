"""S46 semantic documentation consistency gate.

This gate checks the most dangerous doc drift pattern for this repo: an
active-covered infra family is later described as simply "future" without a
scope qualifier. It also ensures implementation-status names every active
infra family with current evidence wording.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INFRA_MATRIX = ROOT / "docs" / "infra-tricky-matrix.json"
DEFAULT_PATTERN_MATRIX = ROOT / "docs" / "data-engineering-pattern-matrix.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "semantic_doc_consistency.json"
DEFAULT_IMPLEMENTATION_STATUS = ROOT / "docs" / "implementation-status.md"
DEFAULT_README = ROOT / "README.md"
DEFAULT_DOCS = (
    ROOT / "README.md",
    ROOT / "foundry_lite_development_plan_ko_sprintified.md",
    ROOT / "foundry_lite_sprint_breakdown_ko.md",
    ROOT / "docs" / "implementation-status.md",
    ROOT / "docs" / "data-platform-expansion-sprint-plan-ko.md",
    ROOT / "docs" / "quality-gate-roadmap.md",
)
RELEASE_TRUTH_DOCS = (
    "README.md",
    "docs/implementation-status.md",
    "docs/quality-gate-roadmap.md",
    "docs/governed-release-hosted-staging-runbook.md",
    "docs/sprint-evidence-ledger.md",
)
FORBIDDEN_RELEASE_TRUTH = (
    (
        re.compile(r"author self-claim allowed", re.IGNORECASE),
        "protected_author_self_review_claim",
        "Protected release documentation cannot say that the author may claim their own review.",
    ),
    (
        re.compile(r"the author may self-claim", re.IGNORECASE),
        "protected_author_self_review_claim",
        "Protected release documentation cannot say that the author may claim their own review.",
    ),
    (
        re.compile(
            r"^\s*-\s*PostgreSQL JSONB object store with production indexes and row-level security\.\s*$",
            re.IGNORECASE,
        ),
        "implemented_jsonb_described_as_target",
        "The implemented PostgreSQL JSONB/index/RLS contract cannot remain in the unimplemented target list.",
    ),
)

FUTURE_MARKERS = (
    "future",
    "deferred",
    "not yet",
    "remain future",
    "still future",
    "future scope",
    "future work",
    "아직",
    "미래",
    "향후",
)
FUTURE_SCOPE_QUALIFIERS = (
    "mvp core",
    "v1 core",
    "post-mvp",
    "beyond",
    "managed",
    "cloud",
    "production",
    "deployment",
    "packaging",
    "operations",
    "operator",
    "runbook",
    "cluster",
    "distributed",
    "maintenance",
    "catalog",
    "retention",
    "compaction",
    "product workflow",
    "workflow execution",
    "continuously running",
    "worker",
    "external",
    "s45",
    "s46",
    "s47",
    "s48",
    "s49",
    "s50",
    "s51",
    "s52",
    "s53",
    "s54",
    "s55",
    "s56",
    "s57",
    "s58",
    "s59",
    "s60",
    "s61",
    "s62",
    "s63",
    "s64",
    "완료 조건",
    "제외",
    "이후",
    "범위",
    "운영",
    "배포",
    "제품",
    "분산",
    "카탈로그",
    "유지",
)
CURRENT_EVIDENCE_MARKERS = (
    "active-covered",
    "ratchet",
    "proof",
    "covered",
    "available",
    "implemented",
    "exists",
    "adds",
    "now",
    "includes",
    "현재",
    "증거",
    "구현",
    "존재",
    "갖는다",
)
CURRENT_CLAIM_MARKERS = (
    "active-covered",
    "complete",
    "completed",
    "done",
    "implemented",
    "current",
    "available",
    "exists",
    "now",
    "완료",
    "구현 완료",
    "현재 구현",
    "존재",
)
NON_CURRENT_QUALIFIERS = (
    "future",
    "deferred",
    "target",
    "goal",
    "proposed",
    "partial",
    "remaining",
    "beyond",
    "not yet",
    "아직",
    "목표",
    "계획",
    "제안",
    "부분",
    "남은",
    "이후",
)
SPECIAL_ALIASES = {
    "s3-storage": ("S3", "MinIO"),
    "iceberg": ("Iceberg",),
    "spark": ("Spark",),
    "temporal": ("Temporal",),
    "elasticsearch": ("Elasticsearch",),
    "debezium-cdc": ("Debezium", "CDC"),
}
GENERIC_ALIAS_WORDS = {
    "adapter",
    "archive",
    "catalog",
    "computeadapter",
    "datasetstorageadapter",
    "deployment",
    "flow",
    "indexing",
    "managed",
    "object",
    "proof",
    "search",
    "storage",
    "workflowadapter",
}

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class ActiveEntry:
    entry_id: str
    name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class SemanticDocFinding:
    code: str
    path: str
    line: int
    reference: str
    message: str


def _repo_relative(path: Path, *, root: Path = ROOT) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _load_json(path: Path) -> JsonObject:
    return json.loads(path.read_text(encoding="utf-8"))


def _object_list(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def collect_findings(
    root: Path = ROOT,
    *,
    infra_matrix_path: Path = DEFAULT_INFRA_MATRIX,
    pattern_matrix_path: Path = DEFAULT_PATTERN_MATRIX,
    docs: tuple[Path, ...] = DEFAULT_DOCS,
    implementation_status_path: Path = DEFAULT_IMPLEMENTATION_STATUS,
    readme_path: Path = DEFAULT_README,
) -> list[SemanticDocFinding]:
    infra_matrix = infra_matrix_path if infra_matrix_path.is_absolute() else root / infra_matrix_path
    pattern_matrix = pattern_matrix_path if pattern_matrix_path.is_absolute() else root / pattern_matrix_path
    implementation_status = (
        implementation_status_path if implementation_status_path.is_absolute() else root / implementation_status_path
    )
    readme = readme_path if readme_path.is_absolute() else root / readme_path
    doc_paths = tuple(path if path.is_absolute() else root / path for path in docs)
    missing = _missing_file_findings(root, infra_matrix, pattern_matrix, implementation_status, readme, *doc_paths)
    if missing:
        return missing

    matrix = _load_json(infra_matrix)
    data_patterns = _load_json(pattern_matrix)
    active_entries = _active_entries(matrix)
    findings: list[SemanticDocFinding] = []
    findings.extend(_implementation_status_findings(active_entries, implementation_status, root=root))
    findings.extend(_readme_pattern_findings(data_patterns, readme, root=root))
    for doc_path in doc_paths:
        if doc_path != readme:
            findings.extend(_pattern_current_claim_findings(data_patterns, doc_path, root=root))
        findings.extend(_future_scope_findings(active_entries, doc_path, root=root))
    findings.extend(_release_truth_findings(root))
    return findings


def _release_truth_findings(root: Path) -> list[SemanticDocFinding]:
    findings: list[SemanticDocFinding] = []
    for relative_path in RELEASE_TRUTH_DOCS:
        path = root / relative_path
        if not path.exists():
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for pattern, code, message in FORBIDDEN_RELEASE_TRUTH:
                if pattern.search(line) is None:
                    continue
                findings.append(_finding(code, path, line_number, pattern.pattern, message, root=root))
    return findings


def _missing_file_findings(root: Path, *paths: Path) -> list[SemanticDocFinding]:
    findings: list[SemanticDocFinding] = []
    for path in paths:
        if path.exists():
            continue
        findings.append(
            _finding(
                "missing_required_file",
                path,
                1,
                _repo_relative(path, root=root),
                "Semantic doc consistency gate cannot run without this file.",
                root=root,
            )
        )
    return findings


def _active_entries(matrix: JsonObject) -> list[ActiveEntry]:
    active_ids = set(_string_list(matrix.get("activeStack")))
    active_ids.update(_string_list(matrix.get("crossCuttingFlows")))
    entries: list[ActiveEntry] = []
    for key in ("families", "crossCuttingFlowProofs"):
        for entry in _object_list(matrix.get(key)):
            entry_id = entry.get("id")
            name = entry.get("name")
            if not isinstance(entry_id, str) or not isinstance(name, str):
                continue
            if entry_id not in active_ids and entry.get("status") != "active-covered":
                continue
            entries.append(ActiveEntry(entry_id=entry_id, name=name, aliases=_aliases(entry_id, name)))
    return entries


def _aliases(entry_id: str, name: str) -> tuple[str, ...]:
    aliases = set(SPECIAL_ALIASES.get(entry_id, ()))
    for raw in re.split(r"[^A-Za-z0-9]+", f"{entry_id} {name}"):
        word = raw.strip()
        normalized = word.casefold()
        if len(word) < 4 or normalized in GENERIC_ALIAS_WORDS:
            continue
        aliases.add(word)
    return tuple(sorted(aliases, key=str.casefold))


def _implementation_status_findings(
    active_entries: list[ActiveEntry],
    implementation_status: Path,
    *,
    root: Path,
) -> list[SemanticDocFinding]:
    lines = implementation_status.read_text(encoding="utf-8").splitlines()
    findings: list[SemanticDocFinding] = []
    for entry in active_entries:
        if _has_current_evidence_line(lines, entry.aliases):
            continue
        findings.append(
            _finding(
                "active_family_missing_current_status",
                implementation_status,
                1,
                entry.entry_id,
                "Implementation status must mention every active-covered infra family with current evidence wording.",
                root=root,
            )
        )
    return findings


def _has_current_evidence_line(lines: list[str], aliases: tuple[str, ...]) -> bool:
    for line in lines:
        lowered = line.casefold()
        if not _contains_alias(lowered, aliases):
            continue
        if any(marker in lowered for marker in CURRENT_EVIDENCE_MARKERS):
            return True
    return False


def _future_scope_findings(
    active_entries: list[ActiveEntry],
    doc_path: Path,
    *,
    root: Path,
) -> list[SemanticDocFinding]:
    findings: list[SemanticDocFinding] = []
    for line_number, line in enumerate(doc_path.read_text(encoding="utf-8").splitlines(), start=1):
        lowered = line.casefold()
        if not _contains_future_marker(lowered) or _has_scope_qualifier(lowered):
            continue
        for entry in active_entries:
            if not _contains_alias(lowered, entry.aliases):
                continue
            findings.append(
                _finding(
                    "active_family_described_as_unqualified_future",
                    doc_path,
                    line_number,
                    entry.entry_id,
                    "Active-covered infra can be future only when the remaining scope is qualified.",
                    root=root,
                )
            )
    return findings


def _readme_pattern_findings(pattern_matrix: JsonObject, readme: Path, *, root: Path) -> list[SemanticDocFinding]:
    lines = readme.read_text(encoding="utf-8").splitlines()
    findings: list[SemanticDocFinding] = []
    for pattern in _object_list(pattern_matrix.get("patterns")):
        status = pattern.get("status")
        if status not in {"deferred", "partial"}:
            continue
        pattern_id = pattern.get("id")
        aliases = tuple(_string_list(pattern.get("readmeAliases")))
        if not isinstance(pattern_id, str) or not aliases:
            continue
        matching = _readme_alias_lines(lines, aliases)
        if not matching:
            findings.append(
                _finding(
                    "readme_pattern_missing",
                    readme,
                    1,
                    pattern_id,
                    "README must mention each partial/deferred pattern by a registered readmeAlias.",
                    root=root,
                )
            )
            continue
        for line_number, lowered in matching:
            findings.extend(
                _pattern_current_claim_line_findings(readme, line_number, lowered, pattern_id, status, root=root)
            )
    return findings


def _pattern_current_claim_findings(
    pattern_matrix: JsonObject, doc_path: Path, *, root: Path
) -> list[SemanticDocFinding]:
    findings: list[SemanticDocFinding] = []
    lines = doc_path.read_text(encoding="utf-8").splitlines()
    for pattern in _object_list(pattern_matrix.get("patterns")):
        status = pattern.get("status")
        pattern_id = pattern.get("id")
        aliases = tuple(_string_list(pattern.get("readmeAliases")))
        if status not in {"deferred", "partial"} or not isinstance(pattern_id, str) or not aliases:
            continue
        for line_number, lowered in _readme_alias_lines(lines, aliases):
            findings.extend(
                _pattern_current_claim_line_findings(doc_path, line_number, lowered, pattern_id, status, root=root)
            )
    return findings


def _pattern_current_claim_line_findings(
    doc_path: Path,
    line_number: int,
    lowered: str,
    pattern_id: str,
    status: object,
    *,
    root: Path,
) -> list[SemanticDocFinding]:
    if not _line_claims_current(lowered) or _line_has_non_current_qualifier(lowered):
        return []
    prefix = "readme" if doc_path.name == "README.md" else "doc"
    return [
        _finding(
            f"{prefix}_{status}_pattern_claimed_current",
            doc_path,
            line_number,
            pattern_id,
            "A non-enforced data pattern is described as current without a future/partial qualifier.",
            root=root,
        )
    ]


def _readme_alias_lines(lines: list[str], aliases: tuple[str, ...]) -> list[tuple[int, str]]:
    matches: list[tuple[int, str]] = []
    for line_number, line in enumerate(lines, start=1):
        lowered = line.casefold()
        if any(alias.casefold() in lowered for alias in aliases):
            matches.append((line_number, lowered))
    return matches


def _line_claims_current(lowered_line: str) -> bool:
    return any(marker.casefold() in lowered_line for marker in CURRENT_CLAIM_MARKERS)


def _line_has_non_current_qualifier(lowered_line: str) -> bool:
    return any(marker.casefold() in lowered_line for marker in NON_CURRENT_QUALIFIERS)


def _contains_alias(lowered_line: str, aliases: tuple[str, ...]) -> bool:
    return any(_alias_matches(lowered_line, alias.casefold()) for alias in aliases)


def _alias_matches(lowered_line: str, alias: str) -> bool:
    if len(alias) <= 3 or alias.isalnum():
        return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lowered_line) is not None
    return alias in lowered_line


def _contains_future_marker(lowered_line: str) -> bool:
    return any(marker.casefold() in lowered_line for marker in FUTURE_MARKERS)


def _has_scope_qualifier(lowered_line: str) -> bool:
    return any(qualifier.casefold() in lowered_line for qualifier in FUTURE_SCOPE_QUALIFIERS)


def _finding(
    code: str,
    path: Path,
    line: int,
    reference: str,
    message: str,
    *,
    root: Path,
) -> SemanticDocFinding:
    return SemanticDocFinding(
        code=code,
        path=_repo_relative(path, root=root),
        line=line,
        reference=reference,
        message=message,
    )


def write_report(output: Path, findings: list[SemanticDocFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "gate": "semantic_doc_consistency",
        "baseline": 0,
        "gate_pass": not findings,
        "violations": [asdict(finding) for finding in findings],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _print_failure(findings: list[SemanticDocFinding], output: Path) -> None:
    print("Semantic doc consistency gate failed:")
    for finding in findings:
        print(f"- {finding.path}:{finding.line}: {finding.code}: {finding.reference} - {finding.message}")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate semantic consistency across status docs.")
    parser.add_argument("--infra-matrix", type=Path, default=DEFAULT_INFRA_MATRIX)
    parser.add_argument("--pattern-matrix", type=Path, default=DEFAULT_PATTERN_MATRIX)
    parser.add_argument("--implementation-status", type=Path, default=DEFAULT_IMPLEMENTATION_STATUS)
    parser.add_argument("--readme", type=Path, default=DEFAULT_README)
    parser.add_argument("--docs", type=Path, nargs="*", default=list(DEFAULT_DOCS))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    findings = collect_findings(
        infra_matrix_path=args.infra_matrix,
        pattern_matrix_path=args.pattern_matrix,
        docs=tuple(args.docs),
        implementation_status_path=args.implementation_status,
        readme_path=args.readme,
    )
    write_report(args.output, findings)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Semantic doc consistency gate passed: active infra status wording is qualified and current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
