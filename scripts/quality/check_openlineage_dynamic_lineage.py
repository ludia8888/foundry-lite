"""Enforces guideline §7.2/§12.3: runtime lineage must be version-bound.

The gate runs after the supply-chain demo smoke. It reads the runtime database,
checks successful transform runs against committed `lineage_edges`, and writes
OpenLineage-compatible RunEvent JSON so the same evidence can later be sent to
an OpenLineage backend or CLI.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import foundry_lite.infrastructure.schema as db
from sqlalchemy import and_, create_engine, select
from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORAGE_ROOT = ROOT / ".foundry-lite-ci-smoke"
DEFAULT_REPORT = ROOT / "artifacts" / "quality" / "openlineage_dynamic_lineage.json"
DEFAULT_EVENTS = ROOT / "artifacts" / "quality" / "openlineage_events.json"
PRODUCER = "https://github.com/ludia8888/foundry-lite/scripts/quality/check_openlineage_dynamic_lineage.py"
OPENLINEAGE_SCHEMA_URL = "https://openlineage.io/spec/1-0-5/OpenLineage.json#/$defs/RunEvent"
OPENLINEAGE_RUN_NAMESPACE = uuid.UUID("90cc6b28-6a32-47a9-b67a-f245f593c999")


@dataclass(frozen=True)
class DatasetVersionInfo:
    version_id: str
    dataset_id: str
    dataset_ref: str
    version_number: int
    status: str
    row_count: int
    byte_size: int


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    run_id: str | None = None
    edge_id: str | None = None
    dataset_version_id: str | None = None


type LineageKey = tuple[str, str, str]
type ActualLineageEdges = dict[LineageKey, list[dict[str, Any]]]


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


def _load_dataset_versions(engine: Engine, tenant_id: str) -> dict[str, DatasetVersionInfo]:
    with engine.begin() as conn:
        rows = (
            conn.execute(
                select(
                    db.dataset_versions.c.id,
                    db.dataset_versions.c.dataset_id,
                    db.dataset_versions.c.version_number,
                    db.dataset_versions.c.status,
                    db.dataset_versions.c.row_count,
                    db.dataset_versions.c.byte_size,
                    db.datasets.c.namespace,
                    db.datasets.c.name,
                )
                .join(db.datasets, db.dataset_versions.c.dataset_id == db.datasets.c.id)
                .where(
                    and_(
                        db.dataset_versions.c.tenant_id == tenant_id,
                        db.datasets.c.tenant_id == tenant_id,
                    )
                )
            )
            .mappings()
            .all()
        )
    return {
        str(row["id"]): DatasetVersionInfo(
            version_id=str(row["id"]),
            dataset_id=str(row["dataset_id"]),
            dataset_ref=f"{row['namespace']}.{row['name']}",
            version_number=int(row["version_number"]),
            status=str(row["status"]),
            row_count=int(row["row_count"]),
            byte_size=int(row["byte_size"]),
        )
        for row in rows
    }


def _load_rows(engine: Engine, tenant_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    with engine.begin() as conn:
        runs = [
            dict(row)
            for row in conn.execute(select(db.transform_runs).where(db.transform_runs.c.tenant_id == tenant_id))
            .mappings()
            .all()
        ]
        edges = [
            dict(row)
            for row in conn.execute(select(db.lineage_edges).where(db.lineage_edges.c.tenant_id == tenant_id))
            .mappings()
            .all()
        ]
        transform_names = {
            str(row["id"]): str(row["api_name"])
            for row in conn.execute(
                select(db.transforms.c.id, db.transforms.c.api_name).where(db.transforms.c.tenant_id == tenant_id)
            )
            .mappings()
            .all()
        }
    return runs, edges, transform_names


def _input_version_ids(run: dict[str, Any]) -> list[str]:
    input_versions = run.get("input_versions")
    if not isinstance(input_versions, dict):
        return []
    return [str(version_id) for version_id in input_versions.values() if isinstance(version_id, str)]


def _successful_transform_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [run for run in runs if run.get("status") == "SUCCESS"]


def _successful_run_contract(
    run: dict[str, Any],
    dataset_versions: dict[str, DatasetVersionInfo],
) -> tuple[set[LineageKey], list[Finding]]:
    findings: list[Finding] = []
    expected_edges: set[LineageKey] = set()
    run_id = str(run["id"])
    output_version_id = run.get("output_version_id")
    if not isinstance(output_version_id, str) or not output_version_id:
        findings.append(
            Finding(
                code="successful_run_missing_output_version",
                message="successful transform run has no output_version_id",
                run_id=run_id,
            )
        )
        return expected_edges, findings
    if output_version_id not in dataset_versions:
        findings.append(
            Finding(
                code="successful_run_output_version_missing",
                message="successful transform run points at a missing dataset version",
                run_id=run_id,
                dataset_version_id=output_version_id,
            )
        )
    input_version_ids = _input_version_ids(run)
    if not input_version_ids:
        findings.append(
            Finding(
                code="successful_run_missing_input_versions",
                message="successful transform run has no input_versions",
                run_id=run_id,
            )
        )
    for input_version_id in input_version_ids:
        if input_version_id not in dataset_versions:
            findings.append(
                Finding(
                    code="successful_run_input_version_missing",
                    message="successful transform run points at a missing input dataset version",
                    run_id=run_id,
                    dataset_version_id=input_version_id,
                )
            )
        expected_edges.add((run_id, input_version_id, output_version_id))
    return expected_edges, findings


def _expected_edges_for_runs(
    successful_runs: list[dict[str, Any]],
    dataset_versions: dict[str, DatasetVersionInfo],
) -> tuple[set[LineageKey], list[Finding]]:
    findings: list[Finding] = []
    expected_edges: set[LineageKey] = set()
    for run in successful_runs:
        run_edges, run_findings = _successful_run_contract(run, dataset_versions)
        expected_edges.update(run_edges)
        findings.extend(run_findings)
    return expected_edges, findings


def _edge_resource_finding(
    *,
    code: str,
    message: str,
    version_id: str,
    edge_id: str,
    run_id: str,
) -> Finding | None:
    if version_id:
        return Finding(
            code=code,
            message=message,
            edge_id=edge_id,
            run_id=run_id or None,
            dataset_version_id=version_id,
        )
    return None


def _lineage_edge_key(
    edge: dict[str, Any],
    runs_by_id: dict[str, dict[str, Any]],
) -> tuple[LineageKey | None, list[Finding]]:
    findings: list[Finding] = []
    edge_id = str(edge["id"])
    run_id = str(edge.get("created_by_run_id") or "")
    from_id = str(edge.get("from_resource_id") or "")
    to_id = str(edge.get("to_resource_id") or "")
    if edge.get("from_resource_type") != "dataset_version" or edge.get("to_resource_type") != "dataset_version":
        findings.append(
            Finding(
                code="input_to_edge_not_version_bound",
                message="input_to lineage edge must connect dataset_version resources",
                edge_id=edge_id,
                run_id=run_id or None,
            )
        )
        return None, findings
    referenced_run = runs_by_id.get(run_id)
    if referenced_run is None:
        findings.append(
            Finding(
                code="lineage_run_missing",
                message="lineage edge references a missing transform run",
                edge_id=edge_id,
                run_id=run_id or None,
            )
        )
        return None, findings
    if referenced_run.get("status") != "SUCCESS":
        findings.append(
            Finding(
                code="lineage_run_not_successful",
                message="input_to lineage edge must not be committed for a failed transform run",
                edge_id=edge_id,
                run_id=run_id,
            )
        )
    return (run_id, from_id, to_id), findings


def _lineage_edge_version_findings(
    edge: dict[str, Any],
    dataset_versions: dict[str, DatasetVersionInfo],
) -> list[Finding]:
    findings: list[Finding] = []
    edge_id = str(edge["id"])
    run_id = str(edge.get("created_by_run_id") or "")
    from_id = str(edge.get("from_resource_id") or "")
    to_id = str(edge.get("to_resource_id") or "")
    if from_id not in dataset_versions:
        finding = _edge_resource_finding(
            code="lineage_input_version_missing",
            message="lineage edge points at a missing input dataset version",
            version_id=from_id,
            edge_id=edge_id,
            run_id=run_id,
        )
        if finding is not None:
            findings.append(finding)
    if to_id not in dataset_versions:
        finding = _edge_resource_finding(
            code="lineage_output_version_missing",
            message="lineage edge points at a missing output dataset version",
            version_id=to_id,
            edge_id=edge_id,
            run_id=run_id,
        )
        if finding is not None:
            findings.append(finding)
    return findings


def _actual_edges_for_rows(
    *,
    edges: list[dict[str, Any]],
    runs_by_id: dict[str, dict[str, Any]],
    dataset_versions: dict[str, DatasetVersionInfo],
) -> tuple[ActualLineageEdges, list[Finding]]:
    findings: list[Finding] = []
    actual_edges: ActualLineageEdges = {}
    for edge in edges:
        if edge.get("relation") != "input_to":
            continue
        edge_key, edge_findings = _lineage_edge_key(edge, runs_by_id)
        findings.extend(edge_findings)
        if edge_key is None:
            continue
        findings.extend(_lineage_edge_version_findings(edge, dataset_versions))
        actual_edges.setdefault(edge_key, []).append(edge)
    return actual_edges, findings


def _missing_or_duplicate_edge_findings(
    expected_edges: set[LineageKey],
    actual_edges: ActualLineageEdges,
) -> list[Finding]:
    findings: list[Finding] = []
    for expected in sorted(expected_edges):
        matching_edges = actual_edges.get(expected, [])
        if not matching_edges:
            findings.append(
                Finding(
                    code="missing_lineage_edge",
                    message="successful transform run is missing an input_version -> output_version lineage edge",
                    run_id=expected[0],
                    dataset_version_id=expected[1],
                )
            )
        if len(matching_edges) > 1:
            findings.append(
                Finding(
                    code="duplicate_lineage_edge",
                    message="successful transform run has duplicate input_version -> output_version lineage edges",
                    run_id=expected[0],
                    dataset_version_id=expected[1],
                )
            )
    return findings


def _unexpected_edge_findings(
    expected_edges: set[LineageKey],
    actual_edges: ActualLineageEdges,
) -> list[Finding]:
    return [
        Finding(
            code="lineage_edge_not_declared_by_transform_run",
            message="input_to lineage edge does not match the transform run input/output contract",
            run_id=actual[0],
            dataset_version_id=actual[1],
        )
        for actual in sorted(actual_edges)
        if actual not in expected_edges
    ]


def collect_findings(
    *,
    runs: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    dataset_versions: dict[str, DatasetVersionInfo],
) -> list[Finding]:
    findings: list[Finding] = []
    runs_by_id = {str(run["id"]): run for run in runs}
    successful_runs = _successful_transform_runs(runs)
    if not successful_runs:
        findings.append(
            Finding(
                code="no_successful_transform_runs",
                message="no successful transform run exists for dynamic lineage validation",
            )
        )
    expected_edges, expected_findings = _expected_edges_for_runs(successful_runs, dataset_versions)
    actual_edges, actual_findings = _actual_edges_for_rows(
        edges=edges,
        runs_by_id=runs_by_id,
        dataset_versions=dataset_versions,
    )
    findings.extend(expected_findings)
    findings.extend(actual_findings)
    findings.extend(_missing_or_duplicate_edge_findings(expected_edges, actual_edges))
    findings.extend(_unexpected_edge_findings(expected_edges, actual_edges))
    return findings


def _dataset_name(info: DatasetVersionInfo) -> str:
    return f"{info.dataset_ref}@{info.version_id}"


def _openlineage_run_id(foundry_run_id: str) -> str:
    return str(uuid.uuid5(OPENLINEAGE_RUN_NAMESPACE, foundry_run_id))


def build_openlineage_events(
    *,
    runs: list[dict[str, Any]],
    dataset_versions: dict[str, DatasetVersionInfo],
    transform_names: dict[str, str],
    tenant_id: str,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    dataset_namespace = f"foundry-lite://{tenant_id}/dataset-version"
    job_namespace = f"foundry-lite://{tenant_id}/transform"
    for run in runs:
        if run.get("status") != "SUCCESS":
            continue
        run_id = str(run["id"])
        output_version_id = run.get("output_version_id")
        if not isinstance(output_version_id, str):
            continue
        output_info = dataset_versions.get(output_version_id)
        input_infos = [
            dataset_versions[version_id] for version_id in _input_version_ids(run) if version_id in dataset_versions
        ]
        if output_info is None or not input_infos:
            continue
        transform_name = transform_names.get(str(run["transform_id"]), str(run["transform_id"]))
        event = {
            "eventType": "COMPLETE",
            "eventTime": run.get("completed_at") or run.get("created_at"),
            "producer": PRODUCER,
            "schemaURL": OPENLINEAGE_SCHEMA_URL,
            "run": {
                "runId": _openlineage_run_id(run_id),
            },
            "job": {
                "namespace": job_namespace,
                "name": transform_name,
            },
            "inputs": [
                {
                    "namespace": dataset_namespace,
                    "name": _dataset_name(info),
                }
                for info in input_infos
            ],
            "outputs": [
                {
                    "namespace": dataset_namespace,
                    "name": _dataset_name(output_info),
                }
            ],
        }
        events.append(event)
    return events


def _event_type_time_findings(event: dict[str, Any], prefix: str) -> list[Finding]:
    findings: list[Finding] = []
    if event.get("eventType") != "COMPLETE":
        findings.append(Finding(code="openlineage_event_type_invalid", message=f"{prefix} is not COMPLETE"))
    if not event.get("eventTime"):
        findings.append(Finding(code="openlineage_event_time_missing", message=f"{prefix} has no eventTime"))
    return findings


def _event_run_findings(event: dict[str, Any], prefix: str) -> list[Finding]:
    run = event.get("run")
    if not isinstance(run, dict):
        return [Finding(code="openlineage_run_missing", message=f"{prefix} has no run object")]
    try:
        uuid.UUID(str(run.get("runId")))
    except ValueError:
        return [Finding(code="openlineage_run_id_invalid", message=f"{prefix} runId is not a UUID")]
    return []


def _event_job_findings(event: dict[str, Any], prefix: str) -> list[Finding]:
    job = event.get("job")
    if isinstance(job, dict) and job.get("namespace") and job.get("name"):
        return []
    return [Finding(code="openlineage_job_invalid", message=f"{prefix} has no job namespace/name")]


def _event_dataset_findings(event: dict[str, Any], prefix: str) -> list[Finding]:
    findings: list[Finding] = []
    if not event.get("inputs"):
        findings.append(Finding(code="openlineage_inputs_missing", message=f"{prefix} has no input datasets"))
    if not event.get("outputs"):
        findings.append(Finding(code="openlineage_outputs_missing", message=f"{prefix} has no output datasets"))
    return findings


def _event_findings(event: dict[str, Any], index: int) -> list[Finding]:
    prefix = f"OpenLineage event #{index}"
    return [
        *_event_type_time_findings(event, prefix),
        *_event_run_findings(event, prefix),
        *_event_job_findings(event, prefix),
        *_event_dataset_findings(event, prefix),
    ]


def collect_event_findings(events: list[dict[str, Any]]) -> list[Finding]:
    findings: list[Finding] = []
    for index, event in enumerate(events):
        findings.extend(_event_findings(event, index))
    return findings


def write_report(
    *,
    output: Path,
    events_output: Path,
    findings: list[Finding],
    events: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    events_output.parent.mkdir(parents=True, exist_ok=True)
    events_output.write_text(json.dumps({"events": _json_ready(events)}, indent=2, sort_keys=True), encoding="utf-8")
    successful_run_count = sum(1 for run in runs if run.get("status") == "SUCCESS")
    input_to_edge_count = sum(1 for edge in edges if edge.get("relation") == "input_to")
    report = {
        "count": len(findings),
        "baseline": 0,
        "gate_pass": not findings,
        "violations": [_json_ready(finding.__dict__) for finding in findings],
        "summary": {
            "successful_transform_runs": successful_run_count,
            "input_to_lineage_edges": input_to_edge_count,
            "openlineage_events": len(events),
            "events_artifact": _repo_relative(events_output),
        },
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def run_gate(
    *,
    storage_root: Path = DEFAULT_STORAGE_ROOT,
    db_url: str | None = None,
    tenant_id: str = "tenant-demo",
    output: Path = DEFAULT_REPORT,
    events_output: Path = DEFAULT_EVENTS,
) -> int:
    database_url, preflight_finding = _database_url(storage_root, db_url)
    if preflight_finding is not None:
        write_report(
            output=output,
            events_output=events_output,
            findings=[preflight_finding],
            events=[],
            runs=[],
            edges=[],
        )
        print(f"OpenLineage dynamic lineage gate failed. Report: {_repo_relative(output)}")
        return 1

    engine = create_engine(database_url, future=True)
    dataset_versions = _load_dataset_versions(engine, tenant_id)
    runs, edges, transform_names = _load_rows(engine, tenant_id)
    events = build_openlineage_events(
        runs=runs,
        dataset_versions=dataset_versions,
        transform_names=transform_names,
        tenant_id=tenant_id,
    )
    findings = collect_findings(runs=runs, edges=edges, dataset_versions=dataset_versions)
    findings.extend(collect_event_findings(events))
    write_report(output=output, events_output=events_output, findings=findings, events=events, runs=runs, edges=edges)
    if findings:
        print("OpenLineage dynamic lineage gate blocked release evidence.")
        for finding in findings:
            location = f" run={finding.run_id}" if finding.run_id else ""
            print(f"- {finding.code}:{location} {finding.message}")
        print(f"Report: {_repo_relative(output)}")
        return 1
    print(
        f"OpenLineage dynamic lineage gate passed ({len(events)} RunEvent artifacts, report: {_repo_relative(output)})."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate runtime lineage as OpenLineage-compatible RunEvents.")
    parser.add_argument("--storage-root", type=Path, default=DEFAULT_STORAGE_ROOT)
    parser.add_argument("--db-url")
    parser.add_argument("--tenant-id", default="tenant-demo")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--events-output", type=Path, default=DEFAULT_EVENTS)
    args = parser.parse_args(argv)
    return run_gate(
        storage_root=args.storage_root,
        db_url=args.db_url,
        tenant_id=args.tenant_id,
        output=args.output,
        events_output=args.events_output,
    )


if __name__ == "__main__":
    sys.exit(main())
