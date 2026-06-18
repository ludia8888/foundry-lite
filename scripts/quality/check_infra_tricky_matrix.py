"""Enforce infra ratchet -> tricky checklist -> test -> CI traceability.

`check_checklist_evidence.py` proves that checked test names are collectable.
This gate adds the missing pull mechanism: every active infrastructure family
and active composition stack must name the tricky checklist items it claims to
cover, the proof classes that make it safe, the tests that prove those classes,
and the CI command that keeps those tests running.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MATRIX = ROOT / "docs" / "infra-tricky-matrix.json"
DEFAULT_CHECKLIST = ROOT / "docs" / "foundry_lite_tricky_failure_modes_checklist.md"
DEFAULT_INFRA_DOC = ROOT / "docs" / "infra-ratchet.md"
DEFAULT_PACKAGE_JSON = ROOT / "package.json"
DEFAULT_CI_SCRIPT = ROOT / "scripts" / "ci_gate.sh"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "infra_tricky_matrix.json"

REQUIRED_FAMILY_PROOF_CLASSES = {
    "adapter-contract",
    "normal-path",
    "failure-injection",
    "concurrency-race",
    "retry-idempotency",
    "partial-success",
    "recovery-cleanup",
    "operator-evidence",
    "docs-sync",
}
REQUIRED_COMPOSITION_PROOF_CLASSES = {
    "composition-compatibility",
    "normal-path",
    "failure-injection",
    "retry-idempotency",
    "partial-success",
    "recovery-cleanup",
    "operator-evidence",
}
CHECKED_LINE_RE = re.compile(r"^\s*[-*]\s*\[[xX]\]")
ITEM_BULLET_RE = re.compile(r"^\s*[-*]\s*\[(?P<status>[ xX])\]\s*(?P<id>[A-Z]+[0-9]+)\b")
SECTION_HEADING_RE = re.compile(r"^###\s+(?P<id>T[0-9]+-[0-9]+)\b")
TEST_NAME_RE = re.compile(r"\btest_[A-Za-z0-9_]+\b")

type JsonObject = dict[str, Any]


@dataclass(frozen=True)
class ChecklistItem:
    item_id: str
    line: int
    is_checked: bool
    block: str


@dataclass(frozen=True)
class InfraTrickyMatrixFinding:
    code: str
    path: str
    reference: str
    message: str


@dataclass(frozen=True)
class PytestCollection:
    test_names: set[str]
    error: str | None


@dataclass(frozen=True)
class MatrixFiles:
    matrix: Path
    checklist: Path
    infra_doc: Path
    package_json: Path
    ci_script: Path


def _repo_relative(path: Path, *, root: Path = ROOT) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _load_json(path: Path) -> JsonObject:
    return cast(JsonObject, json.loads(path.read_text(encoding="utf-8")))


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _object_list(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [cast(JsonObject, item) for item in value if isinstance(item, dict)]


def _proofs(entry: JsonObject) -> dict[str, list[str]]:
    raw = entry.get("proofs")
    if not isinstance(raw, dict):
        return {}
    return {key: _string_list(value) for key, value in raw.items() if isinstance(key, str)}


def _tricky_items(entry: JsonObject) -> list[JsonObject]:
    return _object_list(entry.get("trickyItems"))


def _quality_scripts(entry: JsonObject) -> list[str]:
    scripts = _string_list(entry.get("qualityScripts"))
    single = entry.get("qualityScript")
    if isinstance(single, str):
        scripts.append(single)
    return sorted(set(scripts))


def _ci_commands(entry: JsonObject) -> list[str]:
    commands = _string_list(entry.get("ciCommands"))
    single = entry.get("ciCommand")
    if isinstance(single, str):
        commands.append(single)
    return sorted(set(commands))


def _test_paths(entry: JsonObject) -> list[str]:
    return _string_list(entry.get("testPaths"))


def _entry_id(entry: JsonObject) -> str:
    raw = entry.get("id")
    return raw if isinstance(raw, str) else "<missing-id>"


def parse_checklist_items(checklist_path: Path) -> dict[str, ChecklistItem]:
    lines = checklist_path.read_text(encoding="utf-8").splitlines()
    items: dict[str, ChecklistItem] = {}
    line_count = len(lines)
    for index, line in enumerate(lines):
        heading = SECTION_HEADING_RE.match(line)
        if heading is not None:
            item_id = heading.group("id")
            end = _next_section_index(lines, index + 1)
            block = "\n".join(lines[index:end])
            items[item_id] = ChecklistItem(
                item_id=item_id,
                line=index + 1,
                is_checked=_section_has_checked_evidence(block),
                block=block,
            )
            continue
        bullet = ITEM_BULLET_RE.match(line)
        if bullet is None:
            continue
        item_id = bullet.group("id")
        items[item_id] = ChecklistItem(
            item_id=item_id,
            line=index + 1,
            is_checked=bullet.group("status").lower() == "x",
            block=line if index + 1 <= line_count else "",
        )
    return items


def collect_pytest_test_names(root: Path = ROOT) -> PytestCollection:
    command = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:tach", str(root / "tests")]
    # Fixed argv, shell disabled, and the only input is the repo-local tests root.
    completed = subprocess.run(command, cwd=root, check=False, capture_output=True, text=True)  # nosec B603
    output = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode != 0:
        return PytestCollection(test_names=set(), error=_compact_collection_error(output))
    return PytestCollection(test_names=_parse_collected_test_names(output), error=None)


def _parse_collected_test_names(output: str) -> set[str]:
    names: set[str] = set()
    for line in output.splitlines():
        for segment in line.strip().split("::"):
            base_name = segment.split("[", maxsplit=1)[0].strip()
            if TEST_NAME_RE.fullmatch(base_name):
                names.add(base_name)
    return names


def _compact_collection_error(output: str) -> str:
    compact = "\n".join(line.rstrip() for line in output.strip().splitlines() if line.strip())
    if len(compact) <= 4000:
        return compact
    return compact[-4000:]


def _next_section_index(lines: list[str], start: int) -> int:
    for index in range(start, len(lines)):
        if lines[index].startswith(("### ", "## ", "# ")):
            return index
    return len(lines)


def _section_has_checked_evidence(block: str) -> bool:
    return any(CHECKED_LINE_RE.search(line) is not None and "test_" in line for line in block.splitlines())


def _active_queue_names(infra_doc: Path) -> set[str]:
    names: set[str] = set()
    for line in infra_doc.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3 or cells[0] in {"Order", "---"}:
            continue
        if cells[2] == "active-covered":
            names.add(cells[1])
    return names


def collect_findings(
    root: Path = ROOT,
    *,
    matrix_path: Path = DEFAULT_MATRIX,
    checklist_path: Path = DEFAULT_CHECKLIST,
    infra_doc_path: Path = DEFAULT_INFRA_DOC,
    package_json_path: Path = DEFAULT_PACKAGE_JSON,
    ci_script_path: Path = DEFAULT_CI_SCRIPT,
    collected_test_names: set[str] | None = None,
) -> list[InfraTrickyMatrixFinding]:
    files = _matrix_files(root, matrix_path, checklist_path, infra_doc_path, package_json_path, ci_script_path)
    missing = _missing_required_files(
        root, files.matrix, files.checklist, files.infra_doc, files.package_json, files.ci_script
    )
    if missing:
        return missing

    collected_names, findings = _collected_names_and_findings(root, collected_test_names)
    findings.extend(_matrix_findings(root, files, collected_names))
    return findings


def _matrix_files(
    root: Path,
    matrix_path: Path,
    checklist_path: Path,
    infra_doc_path: Path,
    package_json_path: Path,
    ci_script_path: Path,
) -> MatrixFiles:
    return MatrixFiles(
        matrix=matrix_path if matrix_path.is_absolute() else root / matrix_path,
        checklist=checklist_path if checklist_path.is_absolute() else root / checklist_path,
        infra_doc=infra_doc_path if infra_doc_path.is_absolute() else root / infra_doc_path,
        package_json=package_json_path if package_json_path.is_absolute() else root / package_json_path,
        ci_script=ci_script_path if ci_script_path.is_absolute() else root / ci_script_path,
    )


def _collected_names_and_findings(
    root: Path, collected_test_names: set[str] | None
) -> tuple[set[str], list[InfraTrickyMatrixFinding]]:
    if collected_test_names is not None:
        return collected_test_names, []
    collection = collect_pytest_test_names(root)
    if collection.error is None:
        return collection.test_names, []
    return collection.test_names, [
        _finding("pytest_collection_failed", root / "tests", "pytest --collect-only", collection.error, root=root)
    ]


def _matrix_findings(root: Path, files: MatrixFiles, collected_names: set[str]) -> list[InfraTrickyMatrixFinding]:
    matrix = _load_json(files.matrix)
    checklist_items = parse_checklist_items(files.checklist)
    package_text = files.package_json.read_text(encoding="utf-8")
    ci_text = files.ci_script.read_text(encoding="utf-8")
    findings: list[InfraTrickyMatrixFinding] = []

    families = _object_list(matrix.get("families"))
    flows = _object_list(matrix.get("crossCuttingFlowProofs"))
    compositions = _object_list(matrix.get("compositionProofs"))
    active_stack = _string_list(matrix.get("activeStack"))
    cross_cutting_flows = _string_list(matrix.get("crossCuttingFlows"))
    family_ids = {_entry_id(entry) for entry in families}
    flow_ids = {_entry_id(entry) for entry in flows}

    findings.extend(_active_stack_findings(active_stack, family_ids, files.matrix, root=root))
    findings.extend(_cross_cutting_flow_findings(cross_cutting_flows, flow_ids, files.matrix, root=root))
    findings.extend(_queue_findings(families, files.infra_doc, root=root))
    for entry in [*families, *flows]:
        findings.extend(
            _entry_findings(
                entry,
                REQUIRED_FAMILY_PROOF_CLASSES,
                checklist_items,
                collected_names,
                package_text,
                ci_text,
                files.matrix,
                root=root,
            )
        )
    findings.extend(
        _composition_stack_findings(active_stack, cross_cutting_flows, compositions, files.matrix, root=root)
    )
    for entry in compositions:
        findings.extend(
            _entry_findings(
                entry,
                REQUIRED_COMPOSITION_PROOF_CLASSES,
                checklist_items,
                collected_names,
                package_text,
                ci_text,
                files.matrix,
                root=root,
            )
        )
    return findings


def _missing_required_files(root: Path, *paths: Path) -> list[InfraTrickyMatrixFinding]:
    findings: list[InfraTrickyMatrixFinding] = []
    for path in paths:
        if path.exists():
            continue
        findings.append(
            InfraTrickyMatrixFinding(
                code="missing_required_file",
                path=_repo_relative(path, root=root),
                reference=_repo_relative(path, root=root),
                message="Infra tricky matrix gate cannot run without this file.",
            )
        )
    return findings


def _active_stack_findings(
    active_stack: list[str],
    family_ids: set[str],
    matrix_path: Path,
    *,
    root: Path,
) -> list[InfraTrickyMatrixFinding]:
    findings: list[InfraTrickyMatrixFinding] = []
    if not active_stack:
        findings.append(
            _finding("missing_active_stack", matrix_path, "activeStack", "Active stack must be non-empty.", root=root)
        )
    for family_id in active_stack:
        if family_id not in family_ids:
            findings.append(
                _finding(
                    "active_stack_family_missing",
                    matrix_path,
                    family_id,
                    "Active stack id has no family proof entry.",
                    root=root,
                )
            )
    return findings


def _cross_cutting_flow_findings(
    flow_ids: list[str],
    proof_ids: set[str],
    matrix_path: Path,
    *,
    root: Path,
) -> list[InfraTrickyMatrixFinding]:
    return [
        _finding(
            "cross_cutting_flow_missing", matrix_path, flow_id, "Cross-cutting flow id has no proof entry.", root=root
        )
        for flow_id in flow_ids
        if flow_id not in proof_ids
    ]


def _queue_findings(families: list[JsonObject], infra_doc: Path, *, root: Path) -> list[InfraTrickyMatrixFinding]:
    active_names = _active_queue_names(infra_doc)
    findings: list[InfraTrickyMatrixFinding] = []
    for entry in families:
        if entry.get("status") != "active-covered":
            continue
        name = entry.get("name")
        if not isinstance(name, str) or name in active_names:
            continue
        findings.append(
            _finding(
                "active_family_missing_from_queue",
                infra_doc,
                _entry_id(entry),
                "Active-covered matrix family must appear as active-covered in the infra ratchet queue.",
                root=root,
            )
        )
    return findings


def _composition_stack_findings(
    active_stack: list[str],
    cross_cutting_flows: list[str],
    compositions: list[JsonObject],
    matrix_path: Path,
    *,
    root: Path,
) -> list[InfraTrickyMatrixFinding]:
    declared = {tuple(_string_list(entry.get("members"))) for entry in compositions}
    required: list[tuple[str, ...]] = []
    for size in range(2, len(active_stack) + 1):
        required.append(tuple(active_stack[:size]))
    for flow_id in cross_cutting_flows:
        required.append(tuple([*active_stack, flow_id]))
    return [
        _finding(
            "missing_required_composition_stack",
            matrix_path,
            "+".join(stack),
            "Every active prefix stack and cross-cutting flow stack needs a composition proof.",
            root=root,
        )
        for stack in required
        if stack not in declared
    ]


def _entry_findings(
    entry: JsonObject,
    required_proof_classes: set[str],
    checklist_items: dict[str, ChecklistItem],
    collected_test_names: set[str],
    package_text: str,
    ci_text: str,
    matrix_path: Path,
    *,
    root: Path,
) -> list[InfraTrickyMatrixFinding]:
    entry_id = _entry_id(entry)
    findings: list[InfraTrickyMatrixFinding] = []
    findings.extend(
        _proof_class_findings(
            entry_id, _proofs(entry), required_proof_classes, collected_test_names, matrix_path, root=root
        )
    )
    findings.extend(_ci_wiring_findings(entry_id, entry, package_text, ci_text, matrix_path, root=root))
    findings.extend(_test_path_findings(entry_id, entry, matrix_path, root=root))
    findings.extend(
        _tricky_item_findings(
            entry_id, _tricky_items(entry), checklist_items, collected_test_names, matrix_path, root=root
        )
    )
    return findings


def _proof_class_findings(
    entry_id: str,
    proofs: dict[str, list[str]],
    required_proof_classes: set[str],
    collected_test_names: set[str],
    matrix_path: Path,
    *,
    root: Path,
) -> list[InfraTrickyMatrixFinding]:
    findings: list[InfraTrickyMatrixFinding] = []
    for proof_class in sorted(required_proof_classes):
        tests = proofs.get(proof_class, [])
        if not tests:
            findings.append(
                _finding(
                    "missing_proof_class_tests",
                    matrix_path,
                    f"{entry_id}:{proof_class}",
                    "Required proof class must list at least one pytest test.",
                    root=root,
                )
            )
            continue
        findings.extend(
            _missing_collectable_test_findings(
                entry_id, proof_class, tests, collected_test_names, matrix_path, root=root
            )
        )
    return findings


def _ci_wiring_findings(
    entry_id: str,
    entry: JsonObject,
    package_text: str,
    ci_text: str,
    matrix_path: Path,
    *,
    root: Path,
) -> list[InfraTrickyMatrixFinding]:
    findings: list[InfraTrickyMatrixFinding] = []
    for script_name in _quality_scripts(entry):
        if f'"{script_name}"' not in package_text:
            findings.append(
                _finding(
                    "missing_quality_script",
                    matrix_path,
                    f"{entry_id}:{script_name}",
                    "Quality script is missing from package.json.",
                    root=root,
                )
            )
    for command in _ci_commands(entry):
        if command not in ci_text:
            findings.append(
                _finding(
                    "missing_ci_command",
                    matrix_path,
                    f"{entry_id}:{command}",
                    "CI gate does not run the matrix command.",
                    root=root,
                )
            )
    return findings


def _test_path_findings(
    entry_id: str, entry: JsonObject, matrix_path: Path, *, root: Path
) -> list[InfraTrickyMatrixFinding]:
    findings: list[InfraTrickyMatrixFinding] = []
    for test_path in _test_paths(entry):
        if (root / test_path).exists():
            continue
        findings.append(
            _finding(
                "missing_test_path",
                matrix_path,
                f"{entry_id}:{test_path}",
                "Matrix test path does not exist.",
                root=root,
            )
        )
    return findings


def _tricky_item_findings(
    entry_id: str,
    tricky_items: list[JsonObject],
    checklist_items: dict[str, ChecklistItem],
    collected_test_names: set[str],
    matrix_path: Path,
    *,
    root: Path,
) -> list[InfraTrickyMatrixFinding]:
    findings: list[InfraTrickyMatrixFinding] = []
    if not tricky_items:
        findings.append(
            _finding(
                "missing_tricky_items",
                matrix_path,
                entry_id,
                "Entry must pull at least one tricky checklist item.",
                root=root,
            )
        )
        return findings
    for item in tricky_items:
        item_id = item.get("id")
        if not isinstance(item_id, str):
            findings.append(
                _finding("missing_tricky_item_id", matrix_path, entry_id, "Tricky item entry needs an id.", root=root)
            )
            continue
        checklist_item = checklist_items.get(item_id)
        if checklist_item is None:
            findings.append(
                _finding(
                    "tricky_item_not_found",
                    matrix_path,
                    f"{entry_id}:{item_id}",
                    "Matrix item is not present in the checklist.",
                    root=root,
                )
            )
            continue
        if not checklist_item.is_checked:
            findings.append(
                _finding(
                    "tricky_item_not_checked",
                    matrix_path,
                    f"{entry_id}:{item_id}",
                    "Active infra pulled an unchecked tricky item.",
                    root=root,
                )
            )
        tests = _string_list(item.get("tests"))
        if not tests:
            findings.append(
                _finding(
                    "tricky_item_missing_tests",
                    matrix_path,
                    f"{entry_id}:{item_id}",
                    "Pulled tricky item must list pytest evidence.",
                    root=root,
                )
            )
            continue
        findings.extend(
            _missing_collectable_test_findings(entry_id, item_id, tests, collected_test_names, matrix_path, root=root)
        )
        for test_name in tests:
            if test_name not in checklist_item.block:
                findings.append(
                    _finding(
                        "tricky_item_missing_test_in_checklist",
                        matrix_path,
                        f"{entry_id}:{item_id}:{test_name}",
                        "Pulled tricky item must cite its pytest evidence in the checklist item block.",
                        root=root,
                    )
                )
    return findings


def _missing_collectable_test_findings(
    entry_id: str,
    proof_or_item: str,
    tests: list[str],
    collected_test_names: set[str],
    matrix_path: Path,
    *,
    root: Path,
) -> list[InfraTrickyMatrixFinding]:
    return [
        _finding(
            "matrix_test_not_collected",
            matrix_path,
            f"{entry_id}:{proof_or_item}:{test_name}",
            "Matrix references a pytest test that is not collected.",
            root=root,
        )
        for test_name in tests
        if test_name not in collected_test_names
    ]


def _finding(code: str, path: Path, reference: str, message: str, *, root: Path) -> InfraTrickyMatrixFinding:
    return InfraTrickyMatrixFinding(
        code=code,
        path=_repo_relative(path, root=root),
        reference=reference,
        message=message,
    )


def write_report(output: Path, findings: list[InfraTrickyMatrixFinding]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "gate": "infra_tricky_matrix",
        "baseline": 0,
        "gate_pass": not findings,
        "requiredFamilyProofClasses": sorted(REQUIRED_FAMILY_PROOF_CLASSES),
        "requiredCompositionProofClasses": sorted(REQUIRED_COMPOSITION_PROOF_CLASSES),
        "violations": [asdict(finding) for finding in findings],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_failure(findings: list[InfraTrickyMatrixFinding], output: Path) -> None:
    print("Infra tricky matrix gate failed:")
    for finding in findings:
        print(f"- {finding.path}: {finding.code}: {finding.reference} - {finding.message}")
    print(f"Report: {_repo_relative(output)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate infra tricky checklist/test/CI matrix.")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--checklist", type=Path, default=DEFAULT_CHECKLIST)
    parser.add_argument("--infra-doc", type=Path, default=DEFAULT_INFRA_DOC)
    parser.add_argument("--package-json", type=Path, default=DEFAULT_PACKAGE_JSON)
    parser.add_argument("--ci-script", type=Path, default=DEFAULT_CI_SCRIPT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    findings = collect_findings(
        matrix_path=args.matrix,
        checklist_path=args.checklist,
        infra_doc_path=args.infra_doc,
        package_json_path=args.package_json,
        ci_script_path=args.ci_script,
    )
    write_report(args.output, findings)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Infra tricky matrix gate passed: active infra pulls checked tricky items into tests and CI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
