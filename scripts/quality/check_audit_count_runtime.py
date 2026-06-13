"""Enforces guideline §1.2/§7.3/§10.2/§18.3: runtime mutations need audit rows.

The gate runs after the supply-chain demo smoke. It reads the runtime database
and compares high-level mutation evidence with durable `audit_events` rows. The
contract is intentionally numeric: expected mutation count and audit row count
must match exactly, and every expected mutation must have a resource-bound audit
event.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import foundry_lite.infrastructure.schema as db
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORAGE_ROOT = ROOT / ".foundry-lite-ci-smoke"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "audit_count_runtime.json"
SUCCESS_INDEX_STATUSES = {"succeeded", "success"}
TERMINAL_ACTION_STATUSES = {"succeeded", "failed", "conflict"}

type AuditKey = tuple[tuple[str, ...], str, str | None, str | None]


@dataclass(frozen=True)
class AuditExpectation:
    category: str
    event_types: tuple[str, ...]
    resource_type: str
    resource_id: str | None
    action: str | None
    source_table: str
    source_id: str

    @property
    def audit_key(self) -> AuditKey:
        return (self.event_types, self.resource_type, self.resource_id, self.action)


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    category: str | None = None
    expected_count: int | None = None
    actual_count: int | None = None
    resource_type: str | None = None
    resource_id: str | None = None


def _repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _database_url(storage_root: Path, explicit_db_url: str | None) -> tuple[str, Finding | None]:
    if explicit_db_url is not None:
        return explicit_db_url, None
    db_path = storage_root / "foundry-lite.db"
    if not db_path.exists():
        return (
            f"sqlite:///{db_path}",
            Finding(
                code="database_not_found",
                message=f"runtime database does not exist at {_repo_relative(db_path)}",
            ),
        )
    return f"sqlite:///{db_path}", None


def _rows_for_table(engine: Engine, table: Any, tenant_id: str) -> list[dict[str, Any]]:
    with engine.begin() as conn:
        return [dict(row) for row in conn.execute(select(table).where(table.c.tenant_id == tenant_id)).mappings().all()]


def _load_runtime_rows(engine: Engine, tenant_id: str) -> dict[str, list[dict[str, Any]]]:
    return {
        "datasets": _rows_for_table(engine, db.datasets, tenant_id),
        "dataset_versions": _rows_for_table(engine, db.dataset_versions, tenant_id),
        "dataset_transactions": _rows_for_table(engine, db.dataset_transactions, tenant_id),
        "transforms": _rows_for_table(engine, db.transforms, tenant_id),
        "ontology_versions": _rows_for_table(engine, db.ontology_versions, tenant_id),
        "index_runs": _rows_for_table(engine, db.index_runs, tenant_id),
        "action_runs": _rows_for_table(engine, db.action_runs, tenant_id),
    }


def _load_audit_events(engine: Engine, tenant_id: str) -> list[dict[str, Any]]:
    with engine.begin() as conn:
        return [
            dict(row)
            for row in conn.execute(select(db.audit_events).where(db.audit_events.c.tenant_id == tenant_id))
            .mappings()
            .all()
        ]


def _expectation(
    *,
    category: str,
    event_type: str,
    resource_type: str,
    resource_id: str | None,
    action: str | None,
    source_table: str,
    source_id: str,
) -> AuditExpectation:
    return AuditExpectation(
        category=category,
        event_types=(event_type,),
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        source_table=source_table,
        source_id=source_id,
    )


def _dataset_expectations(rows: dict[str, list[dict[str, Any]]]) -> list[AuditExpectation]:
    expectations: list[AuditExpectation] = []
    for row in rows["datasets"]:
        expectations.append(
            _expectation(
                category="dataset.created",
                event_type="dataset.created",
                resource_type="dataset",
                resource_id=str(row["id"]),
                action="create",
                source_table="datasets",
                source_id=str(row["id"]),
            )
        )
    for row in rows["dataset_versions"]:
        expectations.append(
            _expectation(
                category="dataset.version.committed",
                event_type="dataset.version.committed",
                resource_type="dataset_version",
                resource_id=str(row["id"]),
                action=None,
                source_table="dataset_versions",
                source_id=str(row["id"]),
            )
        )
    return expectations


def _transform_expectations(rows: dict[str, list[dict[str, Any]]]) -> list[AuditExpectation]:
    return [
        AuditExpectation(
            category="transform.definition.changed",
            event_types=("transform.definition.created", "transform.definition.updated"),
            resource_type="transform",
            resource_id=str(row["id"]),
            action="register",
            source_table="transforms",
            source_id=str(row["id"]),
        )
        for row in rows["transforms"]
    ]


def _ontology_expectations(rows: dict[str, list[dict[str, Any]]]) -> list[AuditExpectation]:
    return [
        _expectation(
            category="ontology.version.activated",
            event_type="ontology.version.activated",
            resource_type="ontology_version",
            resource_id=str(row["id"]),
            action="activate",
            source_table="ontology_versions",
            source_id=str(row["id"]),
        )
        for row in rows["ontology_versions"]
        if str(row.get("status", "")).lower() == "active"
    ]


def _index_expectations(rows: dict[str, list[dict[str, Any]]]) -> list[AuditExpectation]:
    expectations: list[AuditExpectation] = []
    for row in rows["index_runs"]:
        if str(row.get("status", "")).lower() not in SUCCESS_INDEX_STATUSES:
            continue
        expectations.append(
            _expectation(
                category="object.index.rebuilt",
                event_type="object.index.rebuilt",
                resource_type="object_type",
                resource_id=str(row["object_type_id"]),
                action="index_rebuild",
                source_table="index_runs",
                source_id=str(row["id"]),
            )
        )
    return expectations


def _action_event_type(status: str) -> str | None:
    if status == "succeeded":
        return "action.run.committed"
    if status in {"failed", "conflict"}:
        return "action.run.failed"
    return None


def _action_expectations(rows: dict[str, list[dict[str, Any]]]) -> list[AuditExpectation]:
    expectations: list[AuditExpectation] = []
    for row in rows["action_runs"]:
        status = str(row.get("status", "")).lower()
        if status not in TERMINAL_ACTION_STATUSES:
            continue
        event_type = _action_event_type(status)
        if event_type is None:
            continue
        expectations.append(
            _expectation(
                category=event_type,
                event_type=event_type,
                resource_type="action_run",
                resource_id=str(row["id"]),
                action="apply",
                source_table="action_runs",
                source_id=str(row["id"]),
            )
        )
    return expectations


def _aborted_transaction_expectations(rows: dict[str, list[dict[str, Any]]]) -> list[AuditExpectation]:
    expectations: list[AuditExpectation] = []
    for row in rows["dataset_transactions"]:
        if str(row.get("status", "")).lower() != "aborted":
            continue
        expectations.append(
            _expectation(
                category="dataset.transaction.aborted",
                event_type="dataset.transaction.aborted",
                resource_type="dataset_transaction",
                resource_id=str(row["id"]),
                action="abort",
                source_table="dataset_transactions",
                source_id=str(row["id"]),
            )
        )
    return expectations


def build_expectations(rows: dict[str, list[dict[str, Any]]]) -> list[AuditExpectation]:
    expectations: list[AuditExpectation] = []
    expectations.extend(_dataset_expectations(rows))
    expectations.extend(_transform_expectations(rows))
    expectations.extend(_ontology_expectations(rows))
    expectations.extend(_index_expectations(rows))
    expectations.extend(_action_expectations(rows))
    expectations.extend(_aborted_transaction_expectations(rows))
    return expectations


def _audit_matches(row: dict[str, Any], key: AuditKey) -> bool:
    event_types, resource_type, resource_id, action = key
    if row.get("event_type") not in event_types:
        return False
    if row.get("resource_type") != resource_type:
        return False
    if resource_id is not None and str(row.get("resource_id")) != resource_id:
        return False
    return action is None or row.get("action") == action


def _actual_count_for_key(audit_events: list[dict[str, Any]], key: AuditKey) -> int:
    return sum(1 for row in audit_events if _audit_matches(row, key))


def _category_summary(
    expectations: list[AuditExpectation],
    audit_events: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    expected_by_category: Counter[str] = Counter(expectation.category for expectation in expectations)
    actual_by_category: defaultdict[str, int] = defaultdict(int)
    keys_by_category: defaultdict[str, set[AuditKey]] = defaultdict(set)
    for expectation in expectations:
        keys_by_category[expectation.category].add(expectation.audit_key)
    for category, keys in keys_by_category.items():
        actual_by_category[category] = sum(_actual_count_for_key(audit_events, key) for key in keys)
    return {
        category: {
            "expected": expected_count,
            "actual": actual_by_category.get(category, 0),
        }
        for category, expected_count in sorted(expected_by_category.items())
    }


def collect_findings(
    expectations: list[AuditExpectation],
    audit_events: list[dict[str, Any]],
) -> list[Finding]:
    findings: list[Finding] = []
    if not expectations:
        findings.append(
            Finding(
                code="no_mutations_observed",
                message="runtime database has no supported mutation evidence for the demo tenant",
                expected_count=1,
                actual_count=0,
            )
        )
        return findings
    if len(audit_events) != len(expectations):
        findings.append(
            Finding(
                code="audit_count_mismatch",
                message="audit_events row count must equal expected runtime mutation count",
                expected_count=len(expectations),
                actual_count=len(audit_events),
            )
        )
    expected_by_key: Counter[AuditKey] = Counter(expectation.audit_key for expectation in expectations)
    exemplar_by_key = {expectation.audit_key: expectation for expectation in expectations}
    for key, expected_count in sorted(expected_by_key.items(), key=lambda item: item[0]):
        actual_count = _actual_count_for_key(audit_events, key)
        if actual_count == expected_count:
            continue
        exemplar = exemplar_by_key[key]
        findings.append(
            Finding(
                code="audit_expectation_mismatch",
                message="expected mutation evidence is not matched by durable audit_events rows",
                category=exemplar.category,
                expected_count=expected_count,
                actual_count=actual_count,
                resource_type=exemplar.resource_type,
                resource_id=exemplar.resource_id,
            )
        )
    return findings


def _write_report(
    output: Path,
    *,
    tenant_id: str,
    database_url: str,
    expectations: list[AuditExpectation],
    audit_events: list[dict[str, Any]],
    findings: list[Finding],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "gate": "audit_count_runtime",
        "tenant_id": tenant_id,
        "database_url": database_url,
        "baseline": {"max_difference": 0, "max_violations": 0},
        "count": len(findings),
        "mutation_count": len(expectations),
        "audit_count": len(audit_events),
        "difference": len(audit_events) - len(expectations),
        "categories": _category_summary(expectations, audit_events),
        "violations": [asdict(finding) for finding in findings],
        "gate_pass": not findings,
    }
    output.write_text(json.dumps(_json_ready(report), indent=2, sort_keys=True), encoding="utf-8")


def run_gate(
    *,
    storage_root: Path = DEFAULT_STORAGE_ROOT,
    output: Path = DEFAULT_OUTPUT,
    tenant_id: str = "tenant-demo",
    db_url: str | None = None,
) -> int:
    database_url, database_finding = _database_url(storage_root, db_url)
    findings = [database_finding] if database_finding is not None else []
    expectations: list[AuditExpectation] = []
    audit_events: list[dict[str, Any]] = []
    if database_finding is None:
        engine = create_engine(database_url, future=True)
        rows = _load_runtime_rows(engine, tenant_id)
        audit_events = _load_audit_events(engine, tenant_id)
        expectations = build_expectations(rows)
        findings.extend(collect_findings(expectations, audit_events))
    _write_report(
        output,
        tenant_id=tenant_id,
        database_url=database_url,
        expectations=expectations,
        audit_events=audit_events,
        findings=findings,
    )
    if findings:
        print("Release gate blocked: runtime mutation count must match durable audit_events.")
        for finding in findings:
            print(f"- {finding.code}: {finding.message}")
        print(f"Report: {_repo_relative(output)}")
        return 1
    print(
        "Audit runtime count gate passed: "
        f"{len(audit_events)} audit_events match {len(expectations)} expected mutations."
    )
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate runtime audit count after the supply-chain demo smoke.")
    parser.add_argument("--storage-root", type=Path, default=DEFAULT_STORAGE_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--tenant-id", default="tenant-demo")
    parser.add_argument("--db-url")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return run_gate(
        storage_root=args.storage_root,
        output=args.output,
        tenant_id=args.tenant_id,
        db_url=args.db_url,
    )


if __name__ == "__main__":
    sys.exit(main())
