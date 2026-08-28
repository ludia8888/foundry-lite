"""Enforces guideline §1.2 #4: failed mutations need durable failure state.

The gate forces a dataset upload adapter failure, then verifies that the
runtime database preserves all three pieces of failure evidence together:

- the run is terminal FAILED with a traceable error payload;
- the linked dataset transaction is ABORTED and not committed;
- a durable audit row records the abort with the same correlation id.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parents[2]
for module_root in (ROOT / "libs", ROOT / "apps" / "cli", ROOT / "apps" / "api", ROOT / "apps" / "worker"):
    sys.path.insert(0, str(module_root))

import foundry_lite.infrastructure.schema as db  # noqa: E402
from foundry_lite.application.foundry import FoundryLite  # noqa: E402
from foundry_lite.domain.context import DEMO_ADMIN_ROLES, RequestContext  # noqa: E402
from foundry_lite.domain.errors import ValidationFailed  # noqa: E402
from foundry_lite.infrastructure.adapters import DuckDBComputeAdapter  # noqa: E402
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies  # noqa: E402

DEFAULT_STORAGE_ROOT = ROOT / ".foundry-lite-failure-state-gate"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "failed_mutation_state.json"
TENANT_ID = "tenant-failure-state"
REQUIRED_TRACE_KEYS = {
    "tenant_id",
    "actor_user_id",
    "request_id",
    "run_id",
    "correlation_id",
    "adapter",
}


class ExplodingCsvComputeAdapter(DuckDBComputeAdapter):
    """Compute adapter that fails at the CSV boundary for the dynamic gate."""

    profile_name = "exploding-failure-state"

    def csv_to_parquet(self, source_path: Path, target_path: Path) -> None:
        raise RuntimeError("adapter exploded during failure-state probe")


@dataclass(frozen=True)
class FailedMutationFinding:
    code: str
    message: str
    resource_id: str | None = None


@dataclass(frozen=True)
class FailureRows:
    sync_runs: list[dict[str, Any]]
    dataset_transactions: list[dict[str, Any]]
    dataset_versions: list[dict[str, Any]]
    audit_events: list[dict[str, Any]]


def _repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _json_mapping(value: object) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(decoded, Mapping):
            return {str(key): item for key, item in decoded.items()}
    return None


def _row_status(row: Mapping[str, object]) -> str:
    return str(row.get("status", "")).lower()


def _row_id(row: Mapping[str, object]) -> str:
    return str(row.get("id", ""))


def _table_rows(engine: Engine, table: Any, tenant_id: str) -> list[dict[str, Any]]:
    with engine.begin() as conn:
        return [dict(row) for row in conn.execute(select(table).where(table.c.tenant_id == tenant_id)).mappings().all()]


def load_failure_rows(database_url: str, *, tenant_id: str = TENANT_ID) -> FailureRows:
    engine = create_engine(database_url, future=True)
    return FailureRows(
        sync_runs=_table_rows(engine, db.sync_runs, tenant_id),
        dataset_transactions=_table_rows(engine, db.dataset_transactions, tenant_id),
        dataset_versions=_table_rows(engine, db.dataset_versions, tenant_id),
        audit_events=_table_rows(engine, db.audit_events, tenant_id),
    )


def _matching_abort_audits(
    audit_events: list[dict[str, Any]],
    *,
    transaction_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    return [
        event
        for event in audit_events
        if event.get("event_type") == "dataset.transaction.aborted"
        and event.get("resource_type") == "dataset_transaction"
        and str(event.get("resource_id")) == transaction_id
        and event.get("action") == "abort"
        and event.get("correlation_id") == run_id
    ]


def _collect_error_trace_findings(
    *,
    run_id: str,
    error: dict[str, Any],
    expected_context: Mapping[str, str],
) -> list[FailedMutationFinding]:
    trace = _json_mapping(error.get("trace"))
    if trace is None:
        return [
            FailedMutationFinding(
                code="missing_error_trace",
                message="FAILED run error payload must include a trace object",
                resource_id=run_id,
            )
        ]
    findings: list[FailedMutationFinding] = []
    missing = sorted(REQUIRED_TRACE_KEYS - set(trace))
    if missing:
        findings.append(
            FailedMutationFinding(
                code="missing_trace_keys",
                message=f"FAILED run error trace missing keys: {', '.join(missing)}",
                resource_id=run_id,
            )
        )
    for key, expected in expected_context.items():
        if trace.get(key) != expected:
            findings.append(
                FailedMutationFinding(
                    code="trace_key_mismatch",
                    message=f"FAILED run error trace {key}={trace.get(key)!r}, expected {expected!r}",
                    resource_id=run_id,
                )
            )
    if trace.get("run_id") != run_id or trace.get("correlation_id") != run_id:
        findings.append(
            FailedMutationFinding(
                code="run_correlation_mismatch",
                message="FAILED run trace run_id and correlation_id must match the run id",
                resource_id=run_id,
            )
        )
    return findings


def _completed_run_findings(run: Mapping[str, object], run_id: str) -> list[FailedMutationFinding]:
    if run.get("completed_at"):
        return []
    return [
        FailedMutationFinding(
            code="failed_run_not_completed",
            message="FAILED run must have completed_at",
            resource_id=run_id,
        )
    ]


def _run_error_and_findings(
    run: Mapping[str, object],
    run_id: str,
    expected_context: Mapping[str, str],
) -> tuple[dict[str, Any] | None, list[FailedMutationFinding]]:
    error = _json_mapping(run.get("error"))
    if error is None:
        return None, [
            FailedMutationFinding(
                code="missing_error_payload",
                message="FAILED run must persist a structured error payload",
                resource_id=run_id,
            )
        ]
    return error, _collect_error_trace_findings(run_id=run_id, error=error, expected_context=expected_context)


def _transaction_id_and_findings(
    run: Mapping[str, object], run_id: str
) -> tuple[str | None, list[FailedMutationFinding]]:
    transaction_id = str(run.get("transaction_id") or "")
    if transaction_id:
        return transaction_id, []
    return None, [
        FailedMutationFinding(
            code="missing_failed_run_transaction",
            message="FAILED run must keep the linked transaction id",
            resource_id=run_id,
        )
    ]


def _transaction_by_id(rows: FailureRows, transaction_id: str) -> dict[str, Any] | None:
    return next((row for row in rows.dataset_transactions if _row_id(row) == transaction_id), None)


def _linked_transaction_findings(
    *,
    run_id: str,
    transaction_id: str,
    transaction: Mapping[str, object] | None,
) -> list[FailedMutationFinding]:
    if transaction is None:
        return [
            FailedMutationFinding(
                code="missing_linked_transaction",
                message="FAILED run transaction_id must point to a durable dataset transaction",
                resource_id=run_id,
            )
        ]
    if _row_status(transaction) == "aborted":
        return []
    return [
        FailedMutationFinding(
            code="linked_transaction_not_aborted",
            message="FAILED run must leave its linked dataset transaction ABORTED",
            resource_id=transaction_id,
        )
    ]


def _transaction_error_findings(
    *,
    transaction_id: str,
    transaction: Mapping[str, object],
    error: Mapping[str, object] | None,
) -> list[FailedMutationFinding]:
    metadata = _json_mapping(transaction.get("metadata"))
    metadata_error = _json_mapping(metadata.get("error")) if metadata is not None else None
    if metadata_error is None:
        return [
            FailedMutationFinding(
                code="missing_transaction_error",
                message="ABORTED dataset transaction must persist the same structured error payload",
                resource_id=transaction_id,
            )
        ]
    if error is not None and metadata_error != error:
        return [
            FailedMutationFinding(
                code="transaction_error_mismatch",
                message="ABORTED transaction metadata error must match the FAILED run error",
                resource_id=transaction_id,
            )
        ]
    return []


def _committed_version_findings(rows: FailureRows, transaction_id: str) -> list[FailedMutationFinding]:
    if not any(str(version.get("transaction_id")) == transaction_id for version in rows.dataset_versions):
        return []
    return [
        FailedMutationFinding(
            code="failed_transaction_committed_version",
            message="FAILED/ABORTED mutation must not create a committed dataset version",
            resource_id=transaction_id,
        )
    ]


def _abort_audit_findings(rows: FailureRows, *, transaction_id: str, run_id: str) -> list[FailedMutationFinding]:
    audits = _matching_abort_audits(rows.audit_events, transaction_id=transaction_id, run_id=run_id)
    if not audits:
        return [
            FailedMutationFinding(
                code="missing_abort_audit",
                message=(
                    "ABORTED dataset transaction must have a durable abort audit with the failed run correlation id"
                ),
                resource_id=transaction_id,
            )
        ]
    after_ref = _json_mapping(audits[0].get("after_ref"))
    if after_ref is not None and "error" in after_ref:
        return []
    return [
        FailedMutationFinding(
            code="missing_abort_audit_error_ref",
            message="abort audit after_ref must include the persisted error payload",
            resource_id=transaction_id,
        )
    ]


def _collect_failed_run_findings(
    run: dict[str, Any],
    rows: FailureRows,
    expected_context: Mapping[str, str],
) -> list[FailedMutationFinding]:
    run_id = _row_id(run)
    findings = _completed_run_findings(run, run_id)
    error, error_findings = _run_error_and_findings(run, run_id, expected_context)
    findings.extend(error_findings)

    transaction_id, transaction_id_findings = _transaction_id_and_findings(run, run_id)
    findings.extend(transaction_id_findings)
    if transaction_id is None:
        return findings

    transaction = _transaction_by_id(rows, transaction_id)
    findings.extend(_linked_transaction_findings(run_id=run_id, transaction_id=transaction_id, transaction=transaction))
    if transaction is None:
        return findings

    findings.extend(_transaction_error_findings(transaction_id=transaction_id, transaction=transaction, error=error))
    findings.extend(_committed_version_findings(rows, transaction_id))
    findings.extend(_abort_audit_findings(rows, transaction_id=transaction_id, run_id=run_id))
    return findings


def collect_findings(rows: FailureRows, expected_context: Mapping[str, str]) -> list[FailedMutationFinding]:
    findings: list[FailedMutationFinding] = []
    failed_runs = [row for row in rows.sync_runs if _row_status(row) == "failed"]
    aborted_transactions = [row for row in rows.dataset_transactions if _row_status(row) == "aborted"]
    if not failed_runs:
        findings.append(
            FailedMutationFinding(
                code="missing_failed_run_state",
                message="failure-state probe must produce at least one FAILED sync run",
            )
        )
    if not aborted_transactions:
        findings.append(
            FailedMutationFinding(
                code="missing_aborted_transaction_state",
                message="failure-state probe must produce at least one ABORTED dataset transaction",
            )
        )
    for run in failed_runs:
        findings.extend(_collect_failed_run_findings(run, rows, expected_context))
    return findings


def _run_failure_probe(storage_root: Path) -> tuple[str, dict[str, str]]:
    if storage_root.exists():
        shutil.rmtree(storage_root)
    dependencies = create_local_core_dependencies(storage_root=storage_root)
    dependencies = dataclass_replace(dependencies, compute_adapter=ExplodingCsvComputeAdapter())
    foundry = FoundryLite(dependencies=dependencies)
    try:
        ctx = RequestContext(
            tenant_id=TENANT_ID,
            actor_user_id="actor-failure-state",
            request_id="request-failure-state",
            roles=DEMO_ADMIN_ROLES,
        )
        foundry.datasets.ensure("raw.failure_state", ctx=ctx, primary_key=["id"])
        csv_path = storage_root / "failure-state.csv"
        csv_path.write_text("id,value\nA,1\n", encoding="utf-8")
        try:
            foundry.datasets.upload_csv("raw.failure_state", csv_path, ctx=ctx)
        except ValidationFailed:
            pass
        else:
            raise AssertionError("exploding adapter unexpectedly let upload_csv succeed")
        return (
            f"sqlite:///{storage_root / 'foundry-lite.db'}",
            {
                "tenant_id": ctx.tenant_id,
                "actor_user_id": ctx.actor_user_id,
                "request_id": ctx.request_id,
                "adapter": "compute_adapter.csv_to_parquet",
            },
        )
    finally:
        foundry.close()


def write_report(
    output: Path,
    *,
    database_url: str,
    rows: FailureRows,
    findings: list[FailedMutationFinding],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "gate": "failed_mutation_state_runtime",
        "database_url": database_url,
        "baseline": {"max_violations": 0},
        "count": len(findings),
        "summary": {
            "failed_sync_runs": sum(1 for row in rows.sync_runs if _row_status(row) == "failed"),
            "aborted_dataset_transactions": sum(
                1 for row in rows.dataset_transactions if _row_status(row) == "aborted"
            ),
            "audit_events": len(rows.audit_events),
        },
        "violations": [asdict(finding) for finding in findings],
        "gate_pass": not findings,
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def run_gate(*, storage_root: Path = DEFAULT_STORAGE_ROOT, output: Path = DEFAULT_OUTPUT) -> int:
    database_url, expected_context = _run_failure_probe(storage_root)
    rows = load_failure_rows(database_url)
    findings = collect_findings(rows, expected_context)
    write_report(output, database_url=database_url, rows=rows, findings=findings)
    if findings:
        print("Failed mutation state gate blocked release evidence:")
        for finding in findings:
            location = f" resource={finding.resource_id}" if finding.resource_id else ""
            print(f"- {finding.code}{location}: {finding.message}")
        print(f"Report: {_repo_relative(output)}")
        return 1
    print("Failed mutation state gate passed: FAILED run, ABORTED transaction, and audit evidence match.")
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify failed mutation runtime state is durable and audited.")
    parser.add_argument("--storage-root", type=Path, default=DEFAULT_STORAGE_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    return run_gate(storage_root=args.storage_root, output=args.output)


if __name__ == "__main__":
    sys.exit(main())
