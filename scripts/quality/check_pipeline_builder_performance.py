"""Measure the currently executable Pipeline Builder performance contract."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import cast

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.services.pipeline_graph_v2_validation import validate_pipeline_graph_v2
from foundry_lite.application.services.pipeline_plan_compiler import PipelinePlanCompiler
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite.infrastructure.repositories.pipeline_repository import SqlAlchemyPipelineRepository
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "pipeline_builder_performance.json"
GRAPH_NODE_COUNT = 200
GRAPH_EDGE_COUNT = 600
PREVIEW_ROW_COUNT = 50
SCHEDULE_SCAN_POPULATION = 10_000
SCHEDULE_SCAN_LIMIT = 100
VALIDATE_TARGET_P95_SECONDS = 0.3
COMPILE_TARGET_P95_SECONDS = 2.0
PREVIEW_ACK_TARGET_P95_SECONDS = 0.5
PREVIEW_EXECUTION_TARGET_P95_SECONDS = 5.0
SCHEDULE_SCAN_TARGET_P95_SECONDS = 1.0


@dataclass(frozen=True)
class PerformanceFinding:
    code: str
    metric: str
    message: str
    details: dict[str, object]


def run_benchmark(
    *,
    graph_iterations: int = 20,
    preview_iterations: int = 7,
    schedule_iterations: int = 7,
) -> tuple[dict[str, object], list[PerformanceFinding]]:
    graph = build_scale_graph()
    compiler = PipelinePlanCompiler()
    validation_samples = _duration_samples(
        lambda: _require_valid_graph(graph),
        graph_iterations,
    )
    compile_samples = _duration_samples(
        lambda: compiler.compile(graph),
        graph_iterations,
    )
    preview_metrics = _measure_preview(preview_iterations)
    metrics = {
        "graphValidation": _metric(validation_samples, VALIDATE_TARGET_P95_SECONDS),
        "planCompilation": _metric(compile_samples, COMPILE_TARGET_P95_SECONDS),
        **preview_metrics,
        "scheduleDueScan": measure_schedule_scan(schedule_iterations),
    }
    findings = target_findings(metrics)
    report = _report(metrics, findings, graph_iterations, preview_iterations, schedule_iterations)
    return report, findings


def build_scale_graph() -> dict[str, object]:
    nodes = [_source_node()]
    nodes.extend(_transform_node(index) for index in range(GRAPH_NODE_COUNT - 2))
    nodes.append(_output_node())
    edges = _chain_edges()
    edges.extend(_extra_edges(GRAPH_EDGE_COUNT - len(edges)))
    if len(nodes) != GRAPH_NODE_COUNT or len(edges) != GRAPH_EDGE_COUNT:
        raise RuntimeError("pipeline performance graph dimensions drifted")
    return {
        "schemaVersion": 2,
        "nodes": nodes,
        "edges": edges,
        "layout": {},
        "outputContract": {"columns": []},
        "tests": [],
        "schedule": None,
    }


def target_findings(metrics: Mapping[str, object]) -> list[PerformanceFinding]:
    findings: list[PerformanceFinding] = []
    for name in (
        "graphValidation",
        "planCompilation",
        "previewAcknowledgement",
        "previewExecution",
        "scheduleDueScan",
    ):
        metric = _mapping(metrics[name])
        actual = _float_value(metric["p95Seconds"])
        target = _float_value(metric["targetP95Seconds"])
        if actual > target:
            findings.append(
                PerformanceFinding(
                    code="pipeline_performance_target_missed",
                    metric=name,
                    message="Pipeline Builder p95 exceeded its enforced target",
                    details={"p95Seconds": actual, "targetP95Seconds": target},
                )
            )
    return findings


def write_report(output: Path, report: Mapping[str, object]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _measure_preview(iterations: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="foundry-lite-pipeline-performance-") as raw_root:
        root = Path(raw_root)
        foundry = _preview_foundry(root)
        try:
            ctx = demo_admin_context()
            _seed_preview_rows(foundry, ctx, root)
            branch = foundry.pipelines.create_branch(
                pipeline_id="pipeline-performance",
                name="preview",
                idempotency_key="pipeline-performance-branch",
                ctx=ctx,
            )
            graph = _preview_graph()
            _warm_preview(foundry, ctx, str(branch["id"]), graph)
            acknowledgements, executions = _preview_samples(foundry, ctx, str(branch["id"]), graph, iterations)
        finally:
            foundry.close()
    return {
        "previewAcknowledgement": _metric(
            acknowledgements,
            PREVIEW_ACK_TARGET_P95_SECONDS,
            row_count=PREVIEW_ROW_COUNT,
        ),
        "previewExecution": _metric(
            executions,
            PREVIEW_EXECUTION_TARGET_P95_SECONDS,
            row_count=PREVIEW_ROW_COUNT,
        ),
    }


def _preview_foundry(root: Path) -> FoundryLite:
    dependencies = create_local_core_dependencies(
        db_url=f"sqlite:///{root / 'pipeline-performance.db'}",
        storage_root=root / "storage",
    )
    return FoundryLite(dependencies=dependencies)


def _seed_preview_rows(foundry: FoundryLite, ctx: RequestContext, root: Path) -> None:
    csv_path = root / "preview-rows.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["id", "value"])
        writer.writerows((index, f"value-{index}") for index in range(PREVIEW_ROW_COUNT))
    foundry.datasets.ensure("raw.pipeline_preview_performance", ctx=ctx)
    foundry.datasets.upload_csv("raw.pipeline_preview_performance", csv_path, ctx=ctx)


def _preview_samples(
    foundry: FoundryLite,
    ctx: RequestContext,
    branch_id: str,
    graph: Mapping[str, object],
    iterations: int,
) -> tuple[list[float], list[float]]:
    acknowledgements: list[float] = []
    executions: list[float] = []
    for index in range(iterations):
        queued, acknowledgement = _timed_result(
            partial(
                foundry.pipelines.create_preview_run,
                branch_id,
                graph=graph,
                target_node_id="out",
                limits={"tableRows": PREVIEW_ROW_COUNT},
                idempotency_key=f"pipeline-performance-preview-{index}",
                ctx=ctx,
            )
        )
        completed, execution = _timed_result(
            partial(
                foundry.pipelines.execute_preview_run,
                str(_mapping(queued)["id"]),
                ctx=ctx,
            )
        )
        _require_preview_rows(completed)
        acknowledgements.append(acknowledgement)
        executions.append(execution)
    return acknowledgements, executions


def _warm_preview(
    foundry: FoundryLite,
    ctx: RequestContext,
    branch_id: str,
    graph: Mapping[str, object],
) -> None:
    """Exclude process/database first-use initialization from the documented warm-state SLO."""
    queued = foundry.pipelines.create_preview_run(
        branch_id,
        graph=graph,
        target_node_id="out",
        limits={"tableRows": PREVIEW_ROW_COUNT},
        idempotency_key="pipeline-performance-preview-warmup",
        ctx=ctx,
    )
    completed = foundry.pipelines.execute_preview_run(str(_mapping(queued)["id"]), ctx=ctx)
    _require_preview_rows(completed)


def _require_preview_rows(value: object) -> None:
    payload = _mapping(value)
    outputs = _mapping_rows(payload["outputs"])
    items = _mapping_rows(outputs[0]["items"]) if outputs else []
    if payload.get("status") != "SUCCEEDED" or len(items) != PREVIEW_ROW_COUNT:
        raise RuntimeError("50-row pipeline preview did not complete with the expected rows")


def _preview_graph() -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "nodes": [
            {
                "id": "source",
                "kind": "source",
                "descriptorId": "source.dataset",
                "specVersion": 1,
                "config": {"datasetRef": "raw.pipeline_preview_performance"},
            },
            _output_node(),
        ],
        "edges": [_edge("source-out", "source", "out", "input")],
        "layout": {},
        "outputContract": {"columns": []},
        "tests": [],
        "schedule": None,
    }


def _source_node() -> dict[str, object]:
    return {
        "id": "source",
        "kind": "source",
        "descriptorId": "source.dataset",
        "specVersion": 1,
        "config": {"datasetRef": "raw.pipeline_performance"},
    }


def _transform_node(index: int) -> dict[str, object]:
    node_id = f"transform-{index:03d}"
    return {
        "id": node_id,
        "kind": "transform",
        "descriptorId": "transform.sql",
        "specVersion": 1,
        "config": {"sql": "SELECT 1", "outputDatasetRef": f"work.{node_id.replace('-', '_')}"},
    }


def _output_node() -> dict[str, object]:
    return {
        "id": "out",
        "kind": "output",
        "descriptorId": "output.dataset",
        "specVersion": 1,
        "config": {"outputDatasetRef": "analytics.pipeline_performance"},
    }


def _chain_edges() -> list[dict[str, object]]:
    edges: list[dict[str, object]] = []
    previous = "source"
    for index in range(GRAPH_NODE_COUNT - 2):
        current = f"transform-{index:03d}"
        edges.append(_edge(f"chain-{index:03d}", previous, current, "inputs"))
        previous = current
    edges.append(_edge("chain-out", previous, "out", "input"))
    return edges


def _extra_edges(required: int) -> list[dict[str, object]]:
    edges: list[dict[str, object]] = []
    for target_index in range(1, GRAPH_NODE_COUNT - 2):
        for source_index in range(min(target_index, 8)):
            source = "source" if source_index == 0 else f"transform-{source_index - 1:03d}"
            if source == f"transform-{target_index - 1:03d}":
                continue
            target = f"transform-{target_index:03d}"
            edges.append(_edge(f"extra-{len(edges):03d}", source, target, "inputs"))
            if len(edges) == required:
                return edges
    raise RuntimeError("unable to construct the requested performance graph edges")


def _edge(edge_id: str, source: str, target: str, target_port: str) -> dict[str, object]:
    return {
        "id": edge_id,
        "sourceNodeId": source,
        "sourcePortId": "dataset",
        "targetNodeId": target,
        "targetPortId": target_port,
    }


def _require_valid_graph(graph: Mapping[str, object]) -> None:
    result = validate_pipeline_graph_v2(graph)
    if result.get("valid") is not True:
        raise RuntimeError("pipeline performance graph is invalid")


def _duration_samples(operation: Callable[[], object], iterations: int) -> list[float]:
    samples: list[float] = []
    for _index in range(iterations):
        _result, duration = _timed_result(operation)
        samples.append(duration)
    return samples


def _timed_result(operation: Callable[[], object]) -> tuple[object, float]:
    started = time.perf_counter()
    result = operation()
    return result, time.perf_counter() - started


def _metric(samples: list[float], target: float, *, row_count: int | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "current_enforced",
        "p95Seconds": _rounded(_p95(samples)),
        "targetP95Seconds": target,
        "sampleCount": len(samples),
        "samplesSeconds": [_rounded(value) for value in samples],
    }
    if row_count is not None:
        payload["rowCount"] = row_count
    return payload


def measure_schedule_scan(iterations: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="foundry-lite-schedule-performance-") as raw_root:
        engine = create_engine(f"sqlite:///{Path(raw_root) / 'scheduler.db'}", future=True)
        db.create_database(engine)
        _seed_schedule_population(engine)
        repository = SqlAlchemyPipelineRepository(engine)
        operation = partial(_scan_due_schedules, repository, engine)
        _require_due_scan(operation())
        samples = _duration_samples(operation, iterations)
        engine.dispose()
    metric = _metric(samples, SCHEDULE_SCAN_TARGET_P95_SECONDS)
    metric.update(
        population=SCHEDULE_SCAN_POPULATION,
        resultLimit=SCHEDULE_SCAN_LIMIT,
        source="libs/foundry_lite/infrastructure/repositories/pipeline_schedule_rows.py:list_due_schedules",
    )
    return metric


def _seed_schedule_population(engine: Engine) -> None:
    due_at = "2026-07-17T00:00:00Z"
    rows = [_schedule_row(index, due_at) for index in range(SCHEDULE_SCAN_POPULATION)]
    with engine.begin() as transaction:
        transaction.execute(db.pipeline_schedules.insert(), rows)


def _schedule_row(index: int, due_at: str) -> dict[str, object]:
    is_due = index % 2 == 0
    is_claimed = index % 10 == 0
    return {
        "id": f"schedule-{index:05d}",
        "tenant_id": "tenant-performance",
        "pipeline_id": f"pipeline-{index:05d}",
        "version_id": f"version-{index:05d}",
        "schedule": {"triggerType": "interval", "timezone": "UTC", "intervalSeconds": 60},
        "enabled": True,
        "status": "active",
        "updated_by": "performance-gate",
        "updated_at": due_at,
        "runtime_config_updated_at": due_at,
        "trigger_type": "interval",
        "timezone": "UTC",
        "next_due_at": due_at if is_due else "2026-07-18T00:00:00Z",
        "lease_expires_at": "2026-07-17T00:01:00Z" if is_claimed else None,
    }


def _scan_due_schedules(repository: SqlAlchemyPipelineRepository, engine: Engine) -> object:
    with engine.begin() as transaction:
        return repository.list_due_schedules(
            transaction=transaction,
            tenant_id="tenant-performance",
            due_at="2026-07-17T00:00:00Z",
            limit=SCHEDULE_SCAN_LIMIT,
        )


def _require_due_scan(value: object) -> None:
    if not isinstance(value, list) or len(value) != SCHEDULE_SCAN_LIMIT:
        raise RuntimeError("10,000-row scheduler scan did not return the bounded due result")


def _report(
    metrics: Mapping[str, object],
    findings: list[PerformanceFinding],
    graph_iterations: int,
    preview_iterations: int,
    schedule_iterations: int,
) -> dict[str, object]:
    return {
        "gate": "pipeline_builder_performance",
        "gatePass": not findings,
        "completionStatus": "current_enforced",
        "count": len(findings),
        "unprovenTargets": [],
        "profile": {
            "graphNodes": GRAPH_NODE_COUNT,
            "graphEdges": GRAPH_EDGE_COUNT,
            "previewRows": PREVIEW_ROW_COUNT,
            "graphIterations": graph_iterations,
            "previewIterations": preview_iterations,
            "scheduleIterations": schedule_iterations,
        },
        "metrics": dict(metrics),
        "violations": [asdict(finding) for finding in findings],
    }


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    index = max(0, math.ceil(len(values) * 0.95) - 1)
    return sorted(values)[index]


def _rounded(value: float) -> float:
    return round(value, 6)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"expected mapping, got {type(value).__name__}")
    return cast(Mapping[str, object], value)


def _mapping_rows(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [cast(Mapping[str, object], item) for item in value if isinstance(item, Mapping)]


def _float_value(value: object) -> float:
    return value if isinstance(value, float) else float(str(value))


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--graph-iterations", type=int, default=20)
    parser.add_argument("--preview-iterations", type=int, default=7)
    parser.add_argument("--schedule-iterations", type=int, default=7)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    report, findings = run_benchmark(
        graph_iterations=args.graph_iterations,
        preview_iterations=args.preview_iterations,
        schedule_iterations=args.schedule_iterations,
    )
    write_report(args.output, report)
    if findings:
        print(f"Pipeline Builder performance gate failed: report={args.output}")
        return 1
    print(f"Pipeline Builder performance gate passed: report={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
