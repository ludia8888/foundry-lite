from __future__ import annotations

from pathlib import Path

from scripts.quality import check_trace_continuity as gate


def _span(
    name: str,
    *,
    trace_id: str = "trace-a",
    attributes: dict[str, object] | None = None,
) -> gate.SpanRecord:
    return gate.SpanRecord(
        name=name,
        trace_id=trace_id,
        span_id=f"span-{name}",
        parent_span_id=None,
        attributes=attributes or {},
    )


def _passing_records() -> list[gate.SpanRecord]:
    records = [
        _span(gate.REQUEST_SPAN_NAME, attributes={"foundry_lite.request_id": gate.REQUEST_ID}),
        _span("sqlalchemy.query", attributes={"db.system.name": "sqlite"}),
    ]
    records.extend(
        _span(name, attributes={"foundry_lite.request_id": gate.REQUEST_ID})
        for name in sorted(gate.REQUIRED_SERVICE_SPANS)
    )
    return records


def test_trace_continuity_flags_missing_request_span() -> None:
    findings = gate.collect_findings([_span("DemoService.run_supply_chain_demo")])

    assert findings == [
        gate.TraceFinding(
            code="trace_request_span_missing",
            message="Synthetic request span was not emitted",
            span_name=gate.REQUEST_SPAN_NAME,
        )
    ]


def test_trace_continuity_flags_service_request_id_gap() -> None:
    records = _passing_records()
    records[2] = _span(records[2].name, attributes={})

    findings = gate.collect_findings(records)

    assert findings == [
        gate.TraceFinding(
            code="trace_service_request_id_missing",
            message="Service span does not carry the request_id from RequestContext",
            span_name=records[2].name,
        )
    ]


def test_trace_continuity_flags_db_trace_mismatch() -> None:
    records = _passing_records()
    records[1] = _span("sqlalchemy.query", trace_id="trace-b", attributes={"db.system.name": "sqlite"})

    findings = gate.collect_findings(records)

    assert findings == [
        gate.TraceFinding(
            code="trace_id_mismatch",
            message="Service or DB span is outside the request trace",
            span_name="sqlalchemy.query",
        ),
        gate.TraceFinding(
            code="trace_db_span_missing",
            message="No SQLAlchemy DB span was emitted inside the request trace",
            span_name=None,
        ),
    ]


def test_trace_continuity_accepts_request_service_and_db_spans() -> None:
    assert gate.collect_findings(_passing_records()) == []


def test_trace_continuity_writes_json_report(tmp_path: Path) -> None:
    output = tmp_path / "trace_continuity.json"
    records = [_span("DemoService.run_supply_chain_demo")]
    findings = gate.collect_findings(records)

    gate.write_report(output, findings, records)

    report = output.read_text(encoding="utf-8")
    assert '"gate_pass": false' in report
    assert '"trace_request_span_missing"' in report
