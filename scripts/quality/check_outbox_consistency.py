"""Enforces guideline §7.1/§10.2: outbox events match runtime state changes.

The gate runs after the supply-chain demo smoke. It reads the runtime database
and verifies that every event-propagated state change has exactly one durable
`outbox_events` row with the expected event type, aggregate, idempotency key,
and correlation key.
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
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "outbox_consistency.json"
SUCCESS_STATUSES = {"succeeded", "success"}
COVERED_EVENT_TYPES = {
    "action.run.committed",
    "dataset.version.committed",
    "materialization.completed",
    "object.changed",
    "object.edit.committed",
    "ontology.version.activated",
}

type OutboxKey = tuple[str, str, str, str]


@dataclass(frozen=True)
class OutboxExpectation:
    category: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    idempotency_key: str
    correlation_id: str | None
    source_table: str
    source_id: str

    @property
    def outbox_key(self) -> OutboxKey:
        return (self.event_type, self.aggregate_type, self.aggregate_id, self.idempotency_key)


@dataclass(frozen=True)
class OutboxCountExpectation:
    category: str
    event_type: str
    aggregate_type: str
    correlation_id: str
    idempotency_key_prefix: str
    expected_count: int
    source_table: str
    source_id: str


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    category: str | None = None
    expected_count: int | None = None
    actual_count: int | None = None
    event_type: str | None = None
    aggregate_type: str | None = None
    aggregate_id: str | None = None
    source_table: str | None = None
    source_id: str | None = None


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
        "action_runs": _rows_for_table(engine, db.action_runs, tenant_id),
        "dataset_versions": _rows_for_table(engine, db.dataset_versions, tenant_id),
        "index_runs": _rows_for_table(engine, db.index_runs, tenant_id),
        "materialization_runs": _rows_for_table(engine, db.materialization_runs, tenant_id),
        "object_edits": _rows_for_table(engine, db.object_edits, tenant_id),
        "ontology_versions": _rows_for_table(engine, db.ontology_versions, tenant_id),
        "outbox_events": _rows_for_table(engine, db.outbox_events, tenant_id),
        "sync_runs": _rows_for_table(engine, db.sync_runs, tenant_id),
        "transform_runs": _rows_for_table(engine, db.transform_runs, tenant_id),
    }


def _expectation(
    *,
    category: str,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    idempotency_key: str,
    correlation_id: str | None,
    source_table: str,
    source_id: str,
) -> OutboxExpectation:
    return OutboxExpectation(
        category=category,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        source_table=source_table,
        source_id=source_id,
    )


def _materialization_run_by_version(rows: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    materializations: dict[str, dict[str, Any]] = {}
    for row in rows["materialization_runs"]:
        status = str(row.get("status", "")).lower()
        version_id = row.get("target_dataset_version_id")
        if status in SUCCESS_STATUSES and version_id:
            materializations[str(version_id)] = row
    return materializations


def _producer_run_by_version(rows: dict[str, list[dict[str, Any]]]) -> dict[str, str]:
    producers: dict[str, str] = {}
    for row in rows["sync_runs"]:
        version_id = row.get("committed_version_id")
        if version_id:
            producers[str(version_id)] = str(row["id"])
    for row in rows["transform_runs"]:
        status = str(row.get("status", "")).lower()
        version_id = row.get("output_version_id")
        if status in SUCCESS_STATUSES and version_id:
            producers[str(version_id)] = str(row["id"])
    return producers


def _dataset_version_expectations(rows: dict[str, list[dict[str, Any]]]) -> list[OutboxExpectation]:
    expectations: list[OutboxExpectation] = []
    materialization_by_version = _materialization_run_by_version(rows)
    producer_by_version = _producer_run_by_version(rows)
    for row in rows["dataset_versions"]:
        if str(row.get("status", "")).lower() != "active":
            continue
        version_id = str(row["id"])
        materialization_run = materialization_by_version.get(version_id)
        if materialization_run is not None:
            expectations.append(
                _expectation(
                    category="materialization.completed",
                    event_type="materialization.completed",
                    aggregate_type="dataset_version",
                    aggregate_id=version_id,
                    idempotency_key=version_id,
                    correlation_id=str(materialization_run["id"]),
                    source_table="materialization_runs",
                    source_id=str(materialization_run["id"]),
                )
            )
            continue
        producer_run_id = producer_by_version.get(version_id, str(row["transaction_id"]))
        expectations.append(
            _expectation(
                category="dataset.version.committed",
                event_type="dataset.version.committed",
                aggregate_type="dataset_version",
                aggregate_id=version_id,
                idempotency_key=version_id,
                correlation_id=producer_run_id,
                source_table="dataset_versions",
                source_id=version_id,
            )
        )
    return expectations


def _ontology_expectations(rows: dict[str, list[dict[str, Any]]]) -> list[OutboxExpectation]:
    return [
        _expectation(
            category="ontology.version.activated",
            event_type="ontology.version.activated",
            aggregate_type="ontology_version",
            aggregate_id=str(row["id"]),
            idempotency_key=str(row["id"]),
            correlation_id=None,
            source_table="ontology_versions",
            source_id=str(row["id"]),
        )
        for row in rows["ontology_versions"]
        if str(row.get("status", "")).lower() == "active"
    ]


def _action_expectations(rows: dict[str, list[dict[str, Any]]]) -> list[OutboxExpectation]:
    expectations: list[OutboxExpectation] = []
    succeeded_action_ids = {
        str(row["id"]) for row in rows["action_runs"] if str(row.get("status", "")).lower() in SUCCESS_STATUSES
    }
    for action_id in succeeded_action_ids:
        expectations.append(
            _expectation(
                category="action.run.committed",
                event_type="action.run.committed",
                aggregate_type="action_run",
                aggregate_id=action_id,
                idempotency_key=f"action.run.committed:{action_id}",
                correlation_id=action_id,
                source_table="action_runs",
                source_id=action_id,
            )
        )
    for row in rows["object_edits"]:
        raw_action_run_id = row.get("action_run_id")
        if not raw_action_run_id or str(raw_action_run_id) not in succeeded_action_ids:
            continue
        action_id = str(raw_action_run_id)
        object_id = str(row["object_id"])
        expectations.append(
            _expectation(
                category="object.edit.committed",
                event_type="object.edit.committed",
                aggregate_type="object",
                aggregate_id=object_id,
                idempotency_key=f"object.edit.committed:{action_id}",
                correlation_id=action_id,
                source_table="object_edits",
                source_id=str(row["id"]),
            )
        )
        expectations.append(
            _expectation(
                category="object.changed.action",
                event_type="object.changed",
                aggregate_type="object",
                aggregate_id=object_id,
                idempotency_key=f"object.changed:{action_id}",
                correlation_id=action_id,
                source_table="object_edits",
                source_id=str(row["id"]),
            )
        )
    return expectations


def build_expectations(rows: dict[str, list[dict[str, Any]]]) -> list[OutboxExpectation]:
    expectations: list[OutboxExpectation] = []
    expectations.extend(_dataset_version_expectations(rows))
    expectations.extend(_ontology_expectations(rows))
    expectations.extend(_action_expectations(rows))
    return expectations


def build_count_expectations(rows: dict[str, list[dict[str, Any]]]) -> list[OutboxCountExpectation]:
    expectations: list[OutboxCountExpectation] = []
    for row in rows["index_runs"]:
        if str(row.get("status", "")).lower() not in SUCCESS_STATUSES:
            continue
        source_ref = row.get("source_ref")
        if not isinstance(source_ref, dict):
            continue
        source_version_id = source_ref.get("dataset_version_id")
        if not source_version_id:
            continue
        expected_count = int(row.get("objects_upserted") or 0) + int(row.get("objects_deleted") or 0)
        if expected_count <= 0:
            continue
        source_version = str(source_version_id)
        expectations.append(
            OutboxCountExpectation(
                category="object.changed.index",
                event_type="object.changed",
                aggregate_type="object",
                correlation_id=source_version,
                idempotency_key_prefix=f"{source_version}:",
                expected_count=expected_count,
                source_table="index_runs",
                source_id=str(row["id"]),
            )
        )
    return expectations


def _covered_outbox_events(rows: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [row for row in rows["outbox_events"] if row.get("event_type") in COVERED_EVENT_TYPES]


def _outbox_key(row: dict[str, Any]) -> OutboxKey:
    return (
        str(row["event_type"]),
        str(row["aggregate_type"]),
        str(row["aggregate_id"]),
        str(row.get("idempotency_key") or ""),
    )


def _actual_count_for_key(outbox_events: list[dict[str, Any]], key: OutboxKey) -> int:
    return sum(1 for row in outbox_events if _outbox_key(row) == key)


def _category_summary(
    expectations: list[OutboxExpectation],
    count_expectations: list[OutboxCountExpectation],
    outbox_events: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    expected_by_category: Counter[str] = Counter(expectation.category for expectation in expectations)
    actual_by_category: defaultdict[str, int] = defaultdict(int)
    keys_by_category: defaultdict[str, set[OutboxKey]] = defaultdict(set)
    for expectation in expectations:
        keys_by_category[expectation.category].add(expectation.outbox_key)
    for category, keys in keys_by_category.items():
        actual_by_category[category] = sum(_actual_count_for_key(outbox_events, key) for key in keys)
    for count_expectation in count_expectations:
        expected_by_category[count_expectation.category] += count_expectation.expected_count
        actual_by_category[count_expectation.category] += _actual_count_for_count_expectation(
            outbox_events,
            count_expectation,
        )
    return {
        category: {
            "expected": expected_count,
            "actual": actual_by_category.get(category, 0),
        }
        for category, expected_count in sorted(expected_by_category.items())
    }


def _matches_count_expectation(row: dict[str, Any], expectation: OutboxCountExpectation) -> bool:
    idempotency_key = str(row.get("idempotency_key") or "")
    return (
        row.get("event_type") == expectation.event_type
        and row.get("aggregate_type") == expectation.aggregate_type
        and row.get("correlation_id") == expectation.correlation_id
        and idempotency_key.startswith(expectation.idempotency_key_prefix)
    )


def _actual_count_for_count_expectation(
    outbox_events: list[dict[str, Any]],
    expectation: OutboxCountExpectation,
) -> int:
    return sum(1 for row in outbox_events if _matches_count_expectation(row, expectation))


def _no_state_change_findings(expected_total: int) -> list[Finding]:
    if expected_total > 0:
        return []
    return [
        Finding(
            code="no_state_changes_observed",
            message="runtime database has no covered state changes for outbox verification",
            expected_count=1,
            actual_count=0,
        )
    ]


def _total_count_findings(expected_total: int, actual_total: int) -> list[Finding]:
    if actual_total == expected_total:
        return []
    return [
        Finding(
            code="outbox_count_mismatch",
            message="covered outbox_events row count must equal expected state-change event count",
            expected_count=expected_total,
            actual_count=actual_total,
        )
    ]


def _expected_by_key(expectations: list[OutboxExpectation]) -> Counter[OutboxKey]:
    return Counter(expectation.outbox_key for expectation in expectations)


def _actual_by_key(outbox_events: list[dict[str, Any]]) -> Counter[OutboxKey]:
    return Counter(_outbox_key(row) for row in outbox_events)


def _exact_expectation_findings(
    expectations: list[OutboxExpectation],
    actual_by_key: Counter[OutboxKey],
) -> list[Finding]:
    findings: list[Finding] = []
    expected_by_key = _expected_by_key(expectations)
    exemplar_by_key = {expectation.outbox_key: expectation for expectation in expectations}
    for key, expected_count in sorted(expected_by_key.items(), key=lambda item: item[0]):
        actual_count = actual_by_key.get(key, 0)
        if actual_count == expected_count:
            continue
        exemplar = exemplar_by_key[key]
        findings.append(
            Finding(
                code="outbox_expectation_mismatch",
                message="expected state change is not matched by durable outbox_events rows",
                category=exemplar.category,
                expected_count=expected_count,
                actual_count=actual_count,
                event_type=exemplar.event_type,
                aggregate_type=exemplar.aggregate_type,
                aggregate_id=exemplar.aggregate_id,
                source_table=exemplar.source_table,
                source_id=exemplar.source_id,
            )
        )
    return findings


def _accounted_count_indexes(
    outbox_events: list[dict[str, Any]],
    count_expectations: list[OutboxCountExpectation],
) -> set[int]:
    return {
        index
        for index, row in enumerate(outbox_events)
        if any(_matches_count_expectation(row, expectation) for expectation in count_expectations)
    }


def _count_expectation_findings(
    count_expectations: list[OutboxCountExpectation],
    outbox_events: list[dict[str, Any]],
) -> list[Finding]:
    findings: list[Finding] = []
    for expectation in count_expectations:
        actual_count = _actual_count_for_count_expectation(outbox_events, expectation)
        if actual_count == expectation.expected_count:
            continue
        findings.append(
            Finding(
                code="outbox_count_expectation_mismatch",
                message="expected indexed object changes are not matched by object.changed outbox rows",
                category=expectation.category,
                expected_count=expectation.expected_count,
                actual_count=actual_count,
                event_type=expectation.event_type,
                aggregate_type=expectation.aggregate_type,
                source_table=expectation.source_table,
                source_id=expectation.source_id,
            )
        )
    return findings


def _unexpected_outbox_findings(
    outbox_events: list[dict[str, Any]],
    expected_by_key: Counter[OutboxKey],
    actual_by_key: Counter[OutboxKey],
    accounted_by_count: set[int],
) -> list[Finding]:
    findings: list[Finding] = []
    for key, actual_count in sorted(actual_by_key.items(), key=lambda item: item[0]):
        if key in expected_by_key:
            continue
        matching_indexes = [index for index, row in enumerate(outbox_events) if _outbox_key(row) == key]
        if matching_indexes and all(index in accounted_by_count for index in matching_indexes):
            continue
        event_type, aggregate_type, aggregate_id, _idempotency_key = key
        findings.append(
            Finding(
                code="unexpected_outbox_event",
                message="covered outbox_events row has no matching runtime state-change evidence",
                expected_count=0,
                actual_count=actual_count,
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
            )
        )
    return findings


def _matching_correlation_count(
    rows: list[dict[str, Any]],
    expected_correlation_id: str | None,
) -> int:
    if expected_correlation_id is None:
        return sum(1 for row in rows if row.get("correlation_id"))
    return sum(1 for row in rows if str(row.get("correlation_id") or "") == expected_correlation_id)


def _correlation_findings(
    expectations: list[OutboxExpectation],
    outbox_events: list[dict[str, Any]],
) -> list[Finding]:
    findings: list[Finding] = []
    for expectation in expectations:
        matching_rows = [row for row in outbox_events if _outbox_key(row) == expectation.outbox_key]
        if not matching_rows:
            continue
        matching_correlation_count = _matching_correlation_count(matching_rows, expectation.correlation_id)
        if matching_correlation_count == len(matching_rows):
            continue
        findings.append(
            Finding(
                code="outbox_correlation_mismatch",
                message="outbox event exists but does not keep the expected correlation id",
                category=expectation.category,
                expected_count=len(matching_rows),
                actual_count=matching_correlation_count,
                event_type=expectation.event_type,
                aggregate_type=expectation.aggregate_type,
                aggregate_id=expectation.aggregate_id,
                source_table=expectation.source_table,
                source_id=expectation.source_id,
            )
        )
    return findings


def collect_findings(
    expectations: list[OutboxExpectation],
    outbox_events: list[dict[str, Any]],
    count_expectations: list[OutboxCountExpectation] | None = None,
) -> list[Finding]:
    count_expectations = count_expectations or []
    expected_total = len(expectations) + sum(expectation.expected_count for expectation in count_expectations)
    findings = _no_state_change_findings(expected_total)
    if findings:
        return findings

    expected_keys = _expected_by_key(expectations)
    actual_keys = _actual_by_key(outbox_events)
    accounted_by_count = _accounted_count_indexes(outbox_events, count_expectations)
    findings.extend(_total_count_findings(expected_total, len(outbox_events)))
    findings.extend(_exact_expectation_findings(expectations, actual_keys))
    findings.extend(_count_expectation_findings(count_expectations, outbox_events))
    findings.extend(_unexpected_outbox_findings(outbox_events, expected_keys, actual_keys, accounted_by_count))
    findings.extend(_correlation_findings(expectations, outbox_events))
    return findings


def _write_report(
    output: Path,
    *,
    tenant_id: str,
    database_url: str,
    expectations: list[OutboxExpectation],
    count_expectations: list[OutboxCountExpectation],
    outbox_events: list[dict[str, Any]],
    findings: list[Finding],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    expected_total = len(expectations) + sum(expectation.expected_count for expectation in count_expectations)
    report = {
        "gate": "outbox_consistency",
        "tenant_id": tenant_id,
        "database_url": database_url,
        "baseline": {"max_difference": 0, "max_violations": 0},
        "count": len(findings),
        "state_change_count": expected_total,
        "outbox_count": len(outbox_events),
        "difference": len(outbox_events) - expected_total,
        "categories": _category_summary(expectations, count_expectations, outbox_events),
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
    expectations: list[OutboxExpectation] = []
    count_expectations: list[OutboxCountExpectation] = []
    outbox_events: list[dict[str, Any]] = []
    if database_finding is None:
        engine = create_engine(database_url, future=True)
        rows = _load_runtime_rows(engine, tenant_id)
        expectations = build_expectations(rows)
        count_expectations = build_count_expectations(rows)
        outbox_events = _covered_outbox_events(rows)
        findings.extend(collect_findings(expectations, outbox_events, count_expectations))
    _write_report(
        output,
        tenant_id=tenant_id,
        database_url=database_url,
        expectations=expectations,
        count_expectations=count_expectations,
        outbox_events=outbox_events,
        findings=findings,
    )
    if findings:
        print("Release gate blocked: runtime state changes must match durable outbox_events.")
        for finding in findings:
            print(f"- {finding.code}: {finding.message}")
        print(f"Report: {_repo_relative(output)}")
        return 1
    print(
        "Outbox consistency gate passed: "
        f"{len(outbox_events)} outbox_events match "
        f"{len(expectations) + sum(expectation.expected_count for expectation in count_expectations)} "
        "expected state changes."
    )
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate runtime outbox consistency after the supply-chain demo.")
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
