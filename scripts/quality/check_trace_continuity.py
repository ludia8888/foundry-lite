"""Enforces guideline §4.3/§12.3: trace keys stay continuous across boundaries.

The gate runs the supply-chain demo under an in-memory OpenTelemetry provider.
It creates a synthetic request span, executes service methods and SQLAlchemy DB
work underneath it, then verifies that required service spans and DB spans share
one trace id. Service spans that receive a RequestContext must also carry the
same `foundry_lite.request_id`. Tenant and actor identity may only be exported
as bounded hashes, never as raw span attributes.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORAGE_ROOT = ROOT / ".foundry-lite-trace-gate"
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "trace_continuity.json"
REQUEST_SPAN_NAME = "foundry-lite.trace-continuity.demo-request"
REQUEST_ID = "trace-gate-request"
REQUIRED_SERVICE_SPANS = {
    "DemoService.run_supply_chain_demo",
    "DatasetIngestService.upload_csv",
    "TransformService.run_transform",
    "OntologyService.apply_ontology",
    "ObjectIndexingService.index_rebuild",
    "ActionService.apply_action",
    "MaterializationService.materialize",
}
RAW_IDENTITY_ATTRIBUTES = {"foundry_lite.tenant_id", "foundry_lite.actor_user_id"}


@dataclass(frozen=True)
class SpanRecord:
    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    attributes: dict[str, Any]


@dataclass(frozen=True)
class TraceFinding:
    code: str
    message: str
    span_name: str | None = None


def _hex_id(value: int, width: int) -> str:
    return f"{value:0{width}x}"


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _span_record(span: Any) -> SpanRecord:
    context = span.get_span_context()
    parent = getattr(span, "parent", None)
    return SpanRecord(
        name=str(span.name),
        trace_id=_hex_id(context.trace_id, 32),
        span_id=_hex_id(context.span_id, 16),
        parent_span_id=_hex_id(parent.span_id, 16) if parent else None,
        attributes={str(key): _json_ready(value) for key, value in dict(span.attributes or {}).items()},
    )


def _is_db_span(record: SpanRecord) -> bool:
    if any(key.startswith("db.") for key in record.attributes):
        return True
    return record.name in {"connect", "engine.connect"} or record.name.upper().startswith(("SELECT ", "INSERT "))


def _service_records(records: list[SpanRecord]) -> list[SpanRecord]:
    return [record for record in records if record.name in REQUIRED_SERVICE_SPANS]


def _request_span(records: list[SpanRecord]) -> SpanRecord | None:
    for record in records:
        if record.name == REQUEST_SPAN_NAME:
            return record
    return None


def _missing_required_service_findings(service_names: set[str]) -> list[TraceFinding]:
    return [
        TraceFinding(
            code="trace_required_service_span_missing",
            message="Required service operation did not emit an OpenTelemetry span",
            span_name=name,
        )
        for name in sorted(REQUIRED_SERVICE_SPANS - service_names)
    ]


def _service_request_id_findings(records: list[SpanRecord], *, request_id: str) -> list[TraceFinding]:
    findings: list[TraceFinding] = []
    for record in records:
        if record.attributes.get("foundry_lite.request_id") == request_id:
            continue
        findings.append(
            TraceFinding(
                code="trace_service_request_id_missing",
                message="Service span does not carry the request_id from RequestContext",
                span_name=record.name,
            )
        )
    return findings


def _raw_identity_attribute_findings(records: list[SpanRecord]) -> list[TraceFinding]:
    findings: list[TraceFinding] = []
    for record in records:
        leaked = sorted(RAW_IDENTITY_ATTRIBUTES & set(record.attributes))
        if not leaked:
            continue
        findings.append(
            TraceFinding(
                code="trace_raw_identity_attribute",
                message=f"Span exports raw tenant/user identity attributes: {', '.join(leaked)}",
                span_name=record.name,
            )
        )
    return findings


def _trace_mismatch_findings(records: list[SpanRecord], *, trace_id: str) -> list[TraceFinding]:
    findings: list[TraceFinding] = []
    for record in records:
        if record.trace_id == trace_id:
            continue
        if record.name in REQUIRED_SERVICE_SPANS or _is_db_span(record):
            findings.append(
                TraceFinding(
                    code="trace_id_mismatch",
                    message="Service or DB span is outside the request trace",
                    span_name=record.name,
                )
            )
    return findings


def collect_findings(records: list[SpanRecord], *, request_id: str = REQUEST_ID) -> list[TraceFinding]:
    request_span = _request_span(records)
    if request_span is None:
        return [
            TraceFinding(
                code="trace_request_span_missing",
                message="Synthetic request span was not emitted",
                span_name=REQUEST_SPAN_NAME,
            )
        ]

    findings: list[TraceFinding] = []
    if request_span.attributes.get("foundry_lite.request_id") != request_id:
        findings.append(
            TraceFinding(
                code="trace_request_id_missing",
                message="Request span does not carry the expected request_id",
                span_name=request_span.name,
            )
        )

    service_records = _service_records(records)
    findings.extend(_missing_required_service_findings({record.name for record in service_records}))
    findings.extend(_service_request_id_findings(service_records, request_id=request_id))
    findings.extend(_raw_identity_attribute_findings(records))
    findings.extend(_trace_mismatch_findings(records, trace_id=request_span.trace_id))
    if not any(_is_db_span(record) and record.trace_id == request_span.trace_id for record in records):
        findings.append(
            TraceFinding(
                code="trace_db_span_missing",
                message="No SQLAlchemy DB span was emitted inside the request trace",
            )
        )
    return findings


def write_report(
    output: Path,
    findings: list[TraceFinding],
    records: list[SpanRecord],
    *,
    request_id: str = REQUEST_ID,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    request_span = _request_span(records)
    report = {
        "gate": "trace_continuity",
        "baseline": {"max_violations": 0},
        "count": len(findings),
        "request_id": request_id,
        "request_trace_id": request_span.trace_id if request_span else None,
        "span_count": len(records),
        "service_span_count": len(_service_records(records)),
        "db_span_count": sum(1 for record in records if _is_db_span(record)),
        "violations": [asdict(finding) for finding in findings],
        "spans": [asdict(record) for record in records],
        "gate_pass": not findings,
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _run_demo_and_collect_spans(storage_root: Path) -> list[SpanRecord]:
    if storage_root.exists():
        shutil.rmtree(storage_root)
    storage_root.parent.mkdir(parents=True, exist_ok=True)

    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "foundry-lite-trace-gate"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    from foundry_lite.application.foundry import FoundryLite
    from foundry_lite.domain.context import DEFAULT_ACTOR_USER_ID, DEFAULT_TENANT_ID, RequestContext
    from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
    from foundry_lite.observability.tracing import instrument_sqlalchemy_engine, trace_identity_hash

    dependencies = create_local_core_dependencies(storage_root=storage_root)
    instrument_sqlalchemy_engine(dependencies.engine)
    foundry = FoundryLite(dependencies=dependencies)
    exporter.clear()

    ctx = RequestContext(
        tenant_id=DEFAULT_TENANT_ID,
        actor_user_id=DEFAULT_ACTOR_USER_ID,
        request_id=REQUEST_ID,
        roles=("admin", "data_engineer", "ops_manager", "finance"),
    )
    tracer = trace.get_tracer("foundry_lite.quality.trace_continuity")
    with tracer.start_as_current_span(REQUEST_SPAN_NAME) as span:
        span.set_attribute("foundry_lite.tenant_hash", trace_identity_hash(ctx.tenant_id))
        span.set_attribute("foundry_lite.actor_user_hash", trace_identity_hash(ctx.actor_user_id))
        span.set_attribute("foundry_lite.request_id", ctx.request_id)
        foundry.demo.run(ctx=ctx, fresh=True)

    provider.force_flush()
    return [_span_record(span) for span in exporter.get_finished_spans()]


def run_gate(*, storage_root: Path = DEFAULT_STORAGE_ROOT, output: Path = DEFAULT_OUTPUT) -> list[TraceFinding]:
    records = _run_demo_and_collect_spans(storage_root)
    findings = collect_findings(records)
    write_report(output, findings, records)
    return findings


def _print_failure(findings: list[TraceFinding], output: Path) -> None:
    print("Release gate blocked: trace continuity broke across request/service/DB spans.")
    for finding in findings:
        location = f" span={finding.span_name}" if finding.span_name else ""
        print(f"- {finding.code}{location}: {finding.message}")
    print(f"Report: {output.relative_to(ROOT)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate OpenTelemetry trace continuity for the demo loop.")
    parser.add_argument("--storage-root", type=Path, default=DEFAULT_STORAGE_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    storage_root = args.storage_root if args.storage_root.is_absolute() else ROOT / args.storage_root
    output = args.output if args.output.is_absolute() else ROOT / args.output
    if ROOT not in storage_root.resolve().parents:
        raise SystemExit(f"trace gate storage root must stay inside repo: {storage_root}")
    findings = run_gate(storage_root=storage_root, output=output)
    if findings:
        _print_failure(findings, output)
        return 1
    print(f"Trace continuity gate passed: request, service, and DB spans share one trace ({output.relative_to(ROOT)}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
