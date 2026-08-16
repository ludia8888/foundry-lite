"""Measure Sprint 36 MVP local performance smoke evidence.

The fast CI profile proves that the CSV ingest, object indexing, object query,
and no-writeback action path still run end to end. Larger release profiles use
the same code path so 100k/1M measurements are repeatable without making every
developer gate take several minutes.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import cast

from foundry_lite.application.action_types import ActionApplyResponse
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.upload_limits import CSV_UPLOAD_LIMIT_ENV
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORAGE_ROOT = ROOT / ".foundry-lite-performance-smoke"
DEFAULT_WORKSPACE_ROOT = ROOT / ".foundry-lite-performance-workspace"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "mvp_performance_smoke.json"
OBJECT_QUERY_TARGET_P95_SECONDS = 0.5
ACTION_APPLY_TARGET_P95_SECONDS = 0.3
CSV_INGEST_1M_TARGET_SECONDS = 300.0


@dataclass(frozen=True)
class SmokeProfile:
    name: str
    csv_rows: int
    object_rows: int
    query_iterations: int
    action_iterations: int
    query_limit: int = 50


@dataclass(frozen=True)
class PerformanceFinding:
    code: str
    subject: str
    message: str
    details: dict[str, object]


PROFILES = {
    "ci": SmokeProfile(
        name="ci",
        csv_rows=1_000,
        object_rows=1_000,
        query_iterations=3,
        action_iterations=3,
    ),
    "release-100k": SmokeProfile(
        name="release-100k",
        csv_rows=100_000,
        object_rows=100_000,
        query_iterations=5,
        action_iterations=10,
    ),
    "release-1m": SmokeProfile(
        name="release-1m",
        csv_rows=1_000_000,
        object_rows=100_000,
        query_iterations=5,
        action_iterations=10,
    ),
}


def run_smoke(
    *,
    storage_root: Path = DEFAULT_STORAGE_ROOT,
    workspace_root: Path = DEFAULT_WORKSPACE_ROOT,
    profile_name: str = "ci",
    csv_rows: int | None = None,
    object_rows: int | None = None,
    query_iterations: int | None = None,
    action_iterations: int | None = None,
    should_enforce_targets: bool = False,
) -> tuple[dict[str, object], list[PerformanceFinding]]:
    profile = _profile_with_overrides(
        profile_name,
        csv_rows=csv_rows,
        object_rows=object_rows,
        query_iterations=query_iterations,
        action_iterations=action_iterations,
    )
    _reset_path(storage_root)
    _reset_path(workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True)
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=storage_root))
    try:
        ctx = demo_admin_context()
        csv_report, csv_findings = _measure_csv_ingest(foundry, ctx, workspace_root, profile.csv_rows)
        object_report, object_findings = _measure_object_flow(foundry, ctx, workspace_root, profile)
        report = _build_report(profile, csv_report, object_report, should_enforce_targets)
        findings = [*csv_findings, *object_findings]
        if should_enforce_targets:
            findings.extend(target_findings(report))
        report["count"] = len(findings)
        report["gatePass"] = not findings
        report["violations"] = [asdict(finding) for finding in findings]
        return report, findings
    finally:
        foundry.close()


def target_findings(report: Mapping[str, object]) -> list[PerformanceFinding]:
    findings: list[PerformanceFinding] = []
    csv_ingest = _mapping(report["csvIngest"])
    object_query = _mapping(report["objectQuery"])
    action_apply = _mapping(report["actionApplyNoWriteback"])
    csv_rows = _int_value(csv_ingest["rows"])
    csv_seconds = _float_value(csv_ingest["seconds"])
    query_p95 = _float_value(object_query["p95Seconds"])
    action_p95 = _float_value(action_apply["p95Seconds"])
    if csv_rows >= 1_000_000 and csv_seconds > CSV_INGEST_1M_TARGET_SECONDS:
        findings.append(
            _finding(
                "csv_ingest_target_missed",
                "csv-ingest-1m",
                "CSV ingest exceeded the local 1M-row target",
                {"rows": csv_rows, "seconds": csv_seconds, "targetSeconds": CSV_INGEST_1M_TARGET_SECONDS},
            )
        )
    if query_p95 > OBJECT_QUERY_TARGET_P95_SECONDS:
        findings.append(
            _finding(
                "object_query_target_missed",
                "object-query-100k",
                "Object query p95 exceeded the v1 local target",
                {
                    "p95Seconds": query_p95,
                    "targetP95Seconds": OBJECT_QUERY_TARGET_P95_SECONDS,
                    "populationRows": object_query["populationRows"],
                },
            )
        )
    if action_p95 > ACTION_APPLY_TARGET_P95_SECONDS:
        findings.append(
            _finding(
                "action_apply_target_missed",
                "action-apply-no-writeback",
                "No-writeback action apply p95 exceeded the v1 local target",
                {"p95Seconds": action_p95, "targetP95Seconds": ACTION_APPLY_TARGET_P95_SECONDS},
            )
        )
    return findings


def write_report(output: Path, report: Mapping[str, object]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _measure_csv_ingest(
    foundry: FoundryLite,
    ctx: RequestContext,
    workspace_root: Path,
    rows: int,
) -> tuple[dict[str, object], list[PerformanceFinding]]:
    csv_path = workspace_root / "csv_ingest_orders.csv"
    _write_orders_csv(csv_path, rows)
    _allow_upload_size(csv_path)
    foundry.datasets.ensure("perf.csv_ingest_orders", ctx=ctx, primary_key=["order_id"])
    result, seconds = _measure(lambda: foundry.datasets.upload_csv("perf.csv_ingest_orders", csv_path, ctx=ctx))
    report = _operation_report("csv_ingest", rows, seconds)
    findings: list[PerformanceFinding] = []
    if result.row_count != rows:
        findings.append(
            _finding(
                "csv_ingest_row_count_mismatch",
                "perf.csv_ingest_orders",
                "Committed CSV row count does not match the generated smoke input",
                {"expected": rows, "actual": result.row_count},
            )
        )
    return report, findings


def _measure_object_flow(
    foundry: FoundryLite,
    ctx: RequestContext,
    workspace_root: Path,
    profile: SmokeProfile,
) -> tuple[dict[str, object], list[PerformanceFinding]]:
    csv_path = workspace_root / "object_orders.csv"
    ontology_path = workspace_root / "performance_ontology.yaml"
    _write_orders_csv(csv_path, profile.object_rows)
    _write_performance_ontology(ontology_path)
    _allow_upload_size(csv_path)

    foundry.datasets.ensure("perf.object_orders", ctx=ctx, primary_key=["order_id"])
    upload_result, upload_seconds = _measure(
        lambda: foundry.datasets.upload_csv("perf.object_orders", csv_path, ctx=ctx)
    )
    _, ontology_seconds = _measure(lambda: foundry.ontology.apply(ontology_path, ctx=ctx))
    index_result, index_seconds = _measure(lambda: foundry.objects.reindex("PerfOrder", ctx=ctx))
    query_report = _measure_object_query(foundry, ctx, profile)
    action_report = _measure_action_apply(foundry, ctx, profile)

    report: dict[str, object] = {
        "objectDatasetUpload": _operation_report("object_dataset_upload", profile.object_rows, upload_seconds),
        "ontologyApply": _duration_report(ontology_seconds),
        "objectIndex": {
            **_operation_report("object_index", profile.object_rows, index_seconds),
            "rowsRead": index_result["rows_read"],
            "objectsUpserted": index_result["objects_upserted"],
        },
        "objectQuery": query_report,
        "actionApplyNoWriteback": action_report,
    }
    return report, _object_flow_findings(profile, upload_result.row_count, index_result, query_report, action_report)


def _measure_object_query(
    foundry: FoundryLite,
    ctx: RequestContext,
    profile: SmokeProfile,
) -> dict[str, object]:
    durations = []
    item_counts = []
    filter_ast = {"property": "bucket", "op": "eq", "value": 5}
    for _ in range(profile.query_iterations):
        result, seconds = _measure(
            lambda: foundry.objects.query("PerfOrder", ctx=ctx, filter_ast=filter_ast, limit=profile.query_limit)
        )
        durations.append(seconds)
        item_counts.append(len(result["items"]))
    return {
        "populationRows": profile.object_rows,
        "iterations": profile.query_iterations,
        "limit": profile.query_limit,
        "filter": filter_ast,
        "durationsSeconds": _rounded_list(durations),
        "p95Seconds": _rounded(_p95(durations)),
        "targetP95Seconds": OBJECT_QUERY_TARGET_P95_SECONDS,
        "itemCounts": item_counts,
    }


def _measure_action_apply(
    foundry: FoundryLite,
    ctx: RequestContext,
    profile: SmokeProfile,
) -> dict[str, object]:
    durations = []
    statuses = []
    for index in range(profile.action_iterations):
        object_id = f"PO-{index:06d}"
        target = foundry.objects.get("PerfOrder", object_id, ctx=ctx)
        object_version = target["objectVersion"]
        result, seconds = _measure(
            partial(
                _apply_performance_action,
                foundry,
                ctx,
                object_id=object_id,
                object_version=object_version,
                iteration=index,
            )
        )
        durations.append(seconds)
        statuses.append(result["status"])
    return {
        "iterations": profile.action_iterations,
        "durationsSeconds": _rounded_list(durations),
        "p95Seconds": _rounded(_p95(durations)),
        "targetP95Seconds": ACTION_APPLY_TARGET_P95_SECONDS,
        "statuses": statuses,
    }


def _object_flow_findings(
    profile: SmokeProfile,
    upload_row_count: int,
    index_result: Mapping[str, object],
    query_report: Mapping[str, object],
    action_report: Mapping[str, object],
) -> list[PerformanceFinding]:
    findings: list[PerformanceFinding] = []
    if upload_row_count != profile.object_rows:
        findings.append(
            _finding(
                "object_dataset_row_count_mismatch",
                "perf.object_orders",
                "Object smoke dataset row count does not match generated input",
                {"expected": profile.object_rows, "actual": upload_row_count},
            )
        )
    if index_result["rows_read"] != profile.object_rows or index_result["objects_upserted"] != profile.object_rows:
        findings.append(
            _finding(
                "object_index_count_mismatch",
                "PerfOrder",
                "Object index did not read/upsert the generated object population",
                {
                    "expected": profile.object_rows,
                    "rowsRead": index_result["rows_read"],
                    "objectsUpserted": index_result["objects_upserted"],
                },
            )
        )
    if 0 in cast(list[int], query_report["itemCounts"]):
        findings.append(
            _finding(
                "object_query_empty_page",
                "PerfOrder",
                "Object query returned an empty page for the generated filter population",
                {"itemCounts": query_report["itemCounts"]},
            )
        )
    if any(status != "succeeded" for status in cast(list[str], action_report["statuses"])):
        findings.append(
            _finding(
                "action_apply_status_mismatch",
                "PerfApproveOrder",
                "No-writeback action apply returned a non-succeeded status",
                {"statuses": action_report["statuses"]},
            )
        )
    return findings


def _build_report(
    profile: SmokeProfile,
    csv_report: Mapping[str, object],
    object_report: Mapping[str, object],
    should_enforce_targets: bool,
) -> dict[str, object]:
    object_query = _mapping(object_report["objectQuery"])
    action_apply = _mapping(object_report["actionApplyNoWriteback"])
    csv_ingest = _mapping(csv_report)
    return {
        "gate": "mvp_performance_smoke",
        "profile": {
            "name": profile.name,
            "csvRows": profile.csv_rows,
            "objectRows": profile.object_rows,
            "queryIterations": profile.query_iterations,
            "actionIterations": profile.action_iterations,
        },
        "targets": {
            "objectQueryP95Seconds": OBJECT_QUERY_TARGET_P95_SECONDS,
            "actionApplyNoWritebackP95Seconds": ACTION_APPLY_TARGET_P95_SECONDS,
            "csvIngest1mSeconds": CSV_INGEST_1M_TARGET_SECONDS,
            "isObjectQueryTargetMet": _float_value(object_query["p95Seconds"]) <= OBJECT_QUERY_TARGET_P95_SECONDS,
            "isActionApplyTargetMet": _float_value(action_apply["p95Seconds"]) <= ACTION_APPLY_TARGET_P95_SECONDS,
            "isCsvIngest1mTargetMet": _csv_target_met(csv_ingest),
        },
        "shouldEnforceTargets": should_enforce_targets,
        "csvIngest": csv_report,
        **object_report,
    }


def _profile_with_overrides(
    profile_name: str,
    *,
    csv_rows: int | None,
    object_rows: int | None,
    query_iterations: int | None,
    action_iterations: int | None,
) -> SmokeProfile:
    if profile_name not in PROFILES:
        raise ValueError(f"unknown performance smoke profile: {profile_name}")
    profile = PROFILES[profile_name]
    resolved = SmokeProfile(
        name=profile.name,
        csv_rows=csv_rows or profile.csv_rows,
        object_rows=object_rows or profile.object_rows,
        query_iterations=query_iterations or profile.query_iterations,
        action_iterations=action_iterations or profile.action_iterations,
        query_limit=profile.query_limit,
    )
    _validate_profile(resolved)
    return resolved


def _validate_profile(profile: SmokeProfile) -> None:
    positive_values = {
        "csv_rows": profile.csv_rows,
        "object_rows": profile.object_rows,
        "query_iterations": profile.query_iterations,
        "action_iterations": profile.action_iterations,
        "query_limit": profile.query_limit,
    }
    invalid = {name: value for name, value in positive_values.items() if value < 1}
    if invalid:
        raise ValueError(f"performance smoke values must be positive: {invalid}")
    if profile.action_iterations > profile.object_rows:
        raise ValueError("action iterations cannot exceed object row count")


def _write_orders_csv(path: Path, rows: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["order_id", "status", "amount", "bucket"])
        for index in range(rows):
            writer.writerow([f"PO-{index:06d}", "PENDING", index % 1000, index % 10])


def _write_performance_ontology(path: Path) -> None:
    path.write_text(
        """objectTypes:
  - apiName: PerfOrder
    displayName: Perf Order
    primaryKey: orderId
    backing:
      dataset: perf.object_orders
      mode: snapshot
      primaryKeyColumns: [order_id]
    properties:
      - apiName: orderId
        column: order_id
        type: string
        indexed: true
        nullable: false
      - apiName: status
        column: status
        type: string
        indexed: true
        editable: true
        editPolicy: edit_wins
      - apiName: amount
        column: amount
        type: float
        indexed: true
      - apiName: bucket
        column: bucket
        type: integer
        indexed: true
actionTypes:
  - apiName: PerfApproveOrder
    displayName: Perf approve order
    target: PerfOrder
    parameters:
      - apiName: reason
        type: string
        required: true
    permissions:
      allowedRoles: [ops_manager]
    preconditions:
      - safeExpression: "object.status == 'PENDING'"
        message: "Only pending orders can be approved"
    mutations:
      - type: setProperty
        property: status
        value: APPROVED
""",
        encoding="utf-8",
    )


def _apply_performance_action(
    foundry: FoundryLite,
    ctx: RequestContext,
    *,
    object_id: str,
    object_version: int,
    iteration: int,
) -> ActionApplyResponse:
    return foundry.actions.apply(
        "PerfApproveOrder",
        object_type="PerfOrder",
        object_id=object_id,
        expected_object_version=object_version,
        params={"reason": "performance smoke"},
        idempotency_key=f"perf-approve-{iteration}",
        ctx=ctx,
    )


def _allow_upload_size(path: Path) -> None:
    required_bytes = path.stat().st_size + 1024
    current_bytes = int(os.getenv(CSV_UPLOAD_LIMIT_ENV, "0") or "0")
    if current_bytes < required_bytes:
        os.environ[CSV_UPLOAD_LIMIT_ENV] = str(required_bytes)


def _operation_report(name: str, rows: int, seconds: float) -> dict[str, object]:
    return {
        "operation": name,
        "rows": rows,
        "seconds": _rounded(seconds),
        "rowsPerSecond": _rounded(rows / seconds if seconds else 0.0),
    }


def _duration_report(seconds: float) -> dict[str, object]:
    return {"seconds": _rounded(seconds)}


def _measure[T](operation: Callable[[], T]) -> tuple[T, float]:
    started_at = time.perf_counter()
    result = operation()
    return result, time.perf_counter() - started_at


def _rounded(value: float) -> float:
    return round(value, 6)


def _rounded_list(values: list[float]) -> list[float]:
    return [_rounded(value) for value in values]


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    index = max(0, math.ceil(len(values) * 0.95) - 1)
    return sorted(values)[index]


def _csv_target_met(csv_ingest: Mapping[str, object]) -> bool:
    rows = _int_value(csv_ingest["rows"])
    seconds = _float_value(csv_ingest["seconds"])
    return rows < 1_000_000 or seconds <= CSV_INGEST_1M_TARGET_SECONDS


def _reset_path(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _finding(code: str, subject: str, message: str, details: dict[str, object]) -> PerformanceFinding:
    return PerformanceFinding(code=code, subject=subject, message=message, details=details)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"expected mapping, got {type(value).__name__}")
    return cast(Mapping[str, object], value)


def _int_value(value: object) -> int:
    return value if isinstance(value, int) else int(str(value))


def _float_value(value: object) -> float:
    return value if isinstance(value, float) else float(str(value))


def _repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _print_failure(findings: list[PerformanceFinding], output: Path) -> None:
    print("MVP performance smoke failed:")
    for finding in findings:
        print(f"- {finding.subject} {finding.code}: {finding.message}")
    print(f"Report: {_repo_relative(output)}")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure MVP local performance smoke evidence.")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="ci")
    parser.add_argument("--storage-root", type=Path, default=DEFAULT_STORAGE_ROOT)
    parser.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--csv-rows", type=int)
    parser.add_argument("--object-rows", type=int)
    parser.add_argument("--query-iterations", type=int)
    parser.add_argument("--action-iterations", type=int)
    parser.add_argument("--enforce-targets", action="store_true", dest="should_enforce_targets")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    report, findings = run_smoke(
        storage_root=args.storage_root,
        workspace_root=args.workspace_root,
        profile_name=args.profile,
        csv_rows=args.csv_rows,
        object_rows=args.object_rows,
        query_iterations=args.query_iterations,
        action_iterations=args.action_iterations,
        should_enforce_targets=args.should_enforce_targets,
    )
    write_report(args.output, report)
    if findings:
        _print_failure(findings, args.output)
        return 1
    print(f"MVP performance smoke passed: report={_repo_relative(args.output)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
