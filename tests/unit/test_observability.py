from __future__ import annotations

import pytest
from foundry_lite.domain.context import RequestContext
from foundry_lite.observability import metrics, tracing


class DummySpan:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}

    def __enter__(self) -> DummySpan:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


class DummyTracer:
    def __init__(self, span: DummySpan) -> None:
        self.span = span

    def start_as_current_span(self, _operation: str) -> DummySpan:
        return self.span


def test_metrics_record_success_failure_and_payload() -> None:
    metrics.record_http_request("GET", "/healthz", 200, 0.01)
    metrics.record_dataset_commit(0.01)
    metrics.record_transform_run(0.02)
    metrics.record_action_apply(0.03)
    metrics.record_object_query(0.04)
    metrics.set_outbox_publish_lag(0.5)
    metrics.set_stream_archive_lag(0)
    metrics.record_failed_run()
    metrics.set_dlq_size(1)
    payload, media_type = metrics.prometheus_payload()

    assert b"foundry_lite_http_requests_total" in payload
    for metric_name in metrics.REQUIRED_OPERATIONAL_METRICS:
        assert metric_name.encode() in payload
    assert "text/plain" in media_type
    assert metrics.operation_labels("demo", run_id="run-1") == {"operation": "demo", "run_id": "run-1"}

    with pytest.raises(RuntimeError):
        with metrics.core_operation("unit_failure"):
            raise RuntimeError("boom")


def test_trace_operation_adds_request_context_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    span = DummySpan()
    monkeypatch.setattr(tracing, "tracer", lambda: DummyTracer(span))

    wrapped = tracing.trace_operation("unit.operation", lambda *, ctx: "ok")
    ctx = RequestContext(tenant_id="tenant-a", actor_user_id="user-a", request_id="req-a")

    assert wrapped(ctx=ctx) == "ok"
    assert span.attributes["foundry_lite.operation"] == "unit.operation"
    assert span.attributes["foundry_lite.tenant_id"] == "tenant-a"
    assert span.attributes["foundry_lite.actor_user_id"] == "user-a"
    assert span.attributes["foundry_lite.request_id"] == "req-a"


def test_trace_public_methods_wraps_only_public_instance_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    span = DummySpan()
    monkeypatch.setattr(tracing, "tracer", lambda: DummyTracer(span))

    @tracing.trace_public_methods
    class Sample:
        def public(self) -> str:
            return "public"

        def _private(self) -> str:
            return "private"

        @staticmethod
        def utility() -> str:
            return "utility"

    sample = Sample()
    assert sample.public() == "public"
    assert sample._private() == "private"
    assert Sample.utility() == "utility"
    assert span.attributes["foundry_lite.operation"] == "Sample.public"


def test_trace_direct_public_methods_does_not_wrap_inherited_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    span = DummySpan()
    monkeypatch.setattr(tracing, "tracer", lambda: DummyTracer(span))

    class Parent:
        def inherited(self) -> str:
            return "inherited"

    @tracing.trace_direct_public_methods
    class Child(Parent):
        def direct(self) -> str:
            return "direct"

    child = Child()
    assert child.inherited() == "inherited"
    assert "foundry_lite.operation" not in span.attributes

    assert child.direct() == "direct"
    assert span.attributes["foundry_lite.operation"] == "Child.direct"


def test_configure_observability_respects_disabled_and_exporters(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyResource:
        @staticmethod
        def create(attributes: dict[str, str]) -> dict[str, str]:
            return attributes

    class DummyProvider:
        def __init__(self, resource: dict[str, str]) -> None:
            self.resource = resource
            self.processors: list[object] = []

        def add_span_processor(self, processor: object) -> None:
            self.processors.append(processor)

    providers: list[DummyProvider] = []

    monkeypatch.setenv("FOUNDRY_LITE_OTEL_DISABLED", "1")
    monkeypatch.setattr(tracing, "_CONFIGURED", False)
    tracing.configure_observability("disabled")
    assert tracing._CONFIGURED is False

    monkeypatch.delenv("FOUNDRY_LITE_OTEL_DISABLED")
    monkeypatch.setenv("FOUNDRY_LITE_OTEL_CONSOLE", "1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://tempo:4318/v1/traces")
    monkeypatch.setattr(tracing, "Resource", DummyResource)
    monkeypatch.setattr(tracing, "TracerProvider", DummyProvider)
    monkeypatch.setattr(tracing, "OTLPSpanExporter", lambda endpoint: ("otlp", endpoint))
    monkeypatch.setattr(tracing, "BatchSpanProcessor", lambda exporter: ("batch", exporter))
    monkeypatch.setattr(tracing, "ConsoleSpanExporter", lambda: "console")
    monkeypatch.setattr(tracing, "SimpleSpanProcessor", lambda exporter: ("simple", exporter))
    monkeypatch.setattr(tracing.trace, "set_tracer_provider", providers.append)

    tracing.configure_observability("configured")

    assert tracing._CONFIGURED is True
    provider = providers[0]
    assert provider.resource == {"service.name": "configured"}
    assert provider.processors == [
        ("batch", ("otlp", "http://tempo:4318/v1/traces")),
        ("simple", "console"),
    ]
