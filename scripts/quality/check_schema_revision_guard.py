"""Enforces guideline §18.1: DB schema changes need revision evidence.

Foundry-lite does not yet run Alembic in the local runtime, but DB shape still
must not change as a hidden code assumption. This gate fingerprints the current
SQLAlchemy metadata and requires the latest schema revision snapshot to match it.
When `schema.py` changes, the revision snapshot must change in the same review.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "libs"))

from foundry_lite.infrastructure import schema as db  # noqa: E402
from sqlalchemy import MetaData, UniqueConstraint  # noqa: E402

DEFAULT_REVISION_DIR = ROOT / "infra" / "schema_revisions"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "schema_revision_guard.json"


@dataclass(frozen=True)
class SchemaRevisionFinding:
    code: str
    message: str
    revision_path: str | None = None
    expected: str | None = None
    actual: str | None = None


def _repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _constraint_columns(constraint: UniqueConstraint) -> list[str]:
    return [column.name for column in constraint.columns]


def _unique_constraint_sort_key(item: Mapping[str, object]) -> tuple[str, str]:
    columns = item.get("columns")
    column_key = ",".join(columns) if isinstance(columns, list) and all(isinstance(col, str) for col in columns) else ""
    return str(item.get("name")), column_key


def _table_snapshot(metadata: MetaData) -> dict[str, object]:
    tables: list[dict[str, object]] = []
    for table in sorted(metadata.tables.values(), key=lambda item: item.name):
        columns = [
            {
                "name": column.name,
                "nullable": bool(column.nullable),
                "primary_key": bool(column.primary_key),
                "type": column.type.__class__.__name__,
            }
            for column in table.columns
        ]
        unique_constraints = sorted(
            (
                {
                    "columns": _constraint_columns(constraint),
                    "name": constraint.name,
                }
                for constraint in table.constraints
                if isinstance(constraint, UniqueConstraint)
            ),
            key=_unique_constraint_sort_key,
        )
        tables.append(
            {
                "columns": columns,
                "name": table.name,
                "unique_constraints": unique_constraints,
            }
        )
    return {"tables": tables}


def _fingerprint(schema_snapshot: Mapping[str, object]) -> str:
    payload = json.dumps(schema_snapshot, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def build_current_revision_payload(metadata: MetaData = db.metadata) -> dict[str, object]:
    schema_snapshot = _table_snapshot(metadata)
    tables_obj = schema_snapshot["tables"]
    assert isinstance(tables_obj, list)
    tables = [table for table in tables_obj if isinstance(table, dict)]
    column_count = sum(len(columns) for table in tables if isinstance((columns := table.get("columns")), list))
    return {
        "schema": schema_snapshot,
        "schema_fingerprint": _fingerprint(schema_snapshot),
        "summary": {
            "column_count": column_count,
            "table_count": len(tables),
            "table_names": [table["name"] for table in tables if isinstance(table, dict)],
        },
    }


def _revision_files(revision_dir: Path) -> list[Path]:
    return sorted(path for path in revision_dir.glob("*.json") if path.is_file())


def _load_latest_revision(revision_dir: Path) -> tuple[Path | None, dict[str, Any] | None, list[SchemaRevisionFinding]]:
    if not revision_dir.exists():
        return (
            None,
            None,
            [
                SchemaRevisionFinding(
                    code="schema_revision_dir_missing",
                    message="Schema revision directory does not exist",
                    revision_path=_repo_relative(revision_dir),
                )
            ],
        )
    revisions = _revision_files(revision_dir)
    if not revisions:
        return (
            None,
            None,
            [
                SchemaRevisionFinding(
                    code="schema_revision_missing",
                    message="No schema revision snapshot exists",
                    revision_path=_repo_relative(revision_dir),
                )
            ],
        )
    latest = revisions[-1]
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return (
            latest,
            None,
            [
                SchemaRevisionFinding(
                    code="schema_revision_invalid_json",
                    message=f"Schema revision snapshot is not valid JSON: {exc}",
                    revision_path=_repo_relative(latest),
                )
            ],
        )
    if not isinstance(payload, dict):
        return (
            latest,
            None,
            [
                SchemaRevisionFinding(
                    code="schema_revision_invalid_payload",
                    message="Schema revision snapshot must be a JSON object",
                    revision_path=_repo_relative(latest),
                )
            ],
        )
    return latest, payload, []


def _metadata_findings(revision_path: Path, revision: dict[str, Any]) -> list[SchemaRevisionFinding]:
    findings: list[SchemaRevisionFinding] = []
    revision_id = revision.get("revision")
    if revision_id != revision_path.stem:
        findings.append(
            SchemaRevisionFinding(
                code="schema_revision_id_mismatch",
                message="Schema revision id must match the JSON filename stem",
                revision_path=_repo_relative(revision_path),
                expected=revision_path.stem,
                actual=str(revision_id),
            )
        )
    if not revision.get("description"):
        findings.append(
            SchemaRevisionFinding(
                code="schema_revision_missing_description",
                message="Schema revision snapshot must explain why this DB shape exists",
                revision_path=_repo_relative(revision_path),
            )
        )
    return findings


def collect_findings(
    *,
    revision_dir: Path = DEFAULT_REVISION_DIR,
    current_revision: dict[str, object] | None = None,
) -> list[SchemaRevisionFinding]:
    current = current_revision or build_current_revision_payload()
    revision_path, revision, load_findings = _load_latest_revision(revision_dir)
    if load_findings:
        return load_findings
    assert revision_path is not None
    assert revision is not None

    findings = _metadata_findings(revision_path, revision)
    current_fingerprint = str(current.get("schema_fingerprint"))
    revision_fingerprint = revision.get("schema_fingerprint")
    if revision_fingerprint != current_fingerprint:
        findings.append(
            SchemaRevisionFinding(
                code="schema_revision_fingerprint_mismatch",
                message="Current SQLAlchemy metadata does not match the latest schema revision snapshot",
                revision_path=_repo_relative(revision_path),
                expected=current_fingerprint,
                actual=str(revision_fingerprint),
            )
        )
    if revision.get("schema") != current.get("schema"):
        findings.append(
            SchemaRevisionFinding(
                code="schema_revision_snapshot_mismatch",
                message="Schema revision snapshot does not match the current table/column/constraint contract",
                revision_path=_repo_relative(revision_path),
            )
        )
    return findings


def write_report(
    output: Path,
    findings: list[SchemaRevisionFinding],
    *,
    revision_dir: Path = DEFAULT_REVISION_DIR,
    current_revision: dict[str, object] | None = None,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    current = current_revision or build_current_revision_payload()
    revision_files = _revision_files(revision_dir) if revision_dir.exists() else []
    report = {
        "baseline": 0,
        "count": len(findings),
        "current_schema_fingerprint": current.get("schema_fingerprint"),
        "gate": "schema_revision_guard",
        "gate_pass": not findings,
        "latest_revision": _repo_relative(revision_files[-1]) if revision_files else None,
        "summary": current.get("summary", {}),
        "violations": [asdict(finding) for finding in findings],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def write_revision_snapshot(path: Path, *, description: str) -> None:
    payload = build_current_revision_payload()
    payload.update(
        {
            "description": description,
            "revision": path.stem,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _print_failure(findings: list[SchemaRevisionFinding], output: Path) -> None:
    print("Schema revision guard failed: DB shape changed without matching revision evidence.")
    for finding in findings:
        location = f" {finding.revision_path}" if finding.revision_path else ""
        print(f"- {finding.code}{location}: {finding.message}")
    print(f"Report: {_repo_relative(output)}")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate DB schema metadata against the latest revision snapshot.")
    parser.add_argument("--revision-dir", type=Path, default=DEFAULT_REVISION_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--write-current-revision", type=Path)
    parser.add_argument("--description", default="Current Foundry-lite SQLAlchemy metadata snapshot.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    if args.write_current_revision is not None:
        write_revision_snapshot(args.write_current_revision, description=args.description)
        print(f"Wrote schema revision snapshot to {_repo_relative(args.write_current_revision)}")
        return 0

    revision_dir = args.revision_dir if args.revision_dir.is_absolute() else ROOT / args.revision_dir
    findings = collect_findings(revision_dir=revision_dir)
    write_report(args.output, findings, revision_dir=revision_dir)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print("Schema revision guard passed: current DB metadata matches latest revision snapshot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
