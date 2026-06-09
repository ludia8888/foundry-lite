from __future__ import annotations

import functools
import os
from collections.abc import Callable
from typing import Any, cast

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor

from foundry_lite.domain.context import RequestContext
from foundry_lite.observability.metrics import core_operation

_CONFIGURED = False


def configure_observability(service_name: str = "foundry-lite") -> None:
    global _CONFIGURED
    if _CONFIGURED or os.getenv("FOUNDRY_LITE_OTEL_DISABLED") == "1":
        return

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if endpoint := os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    if os.getenv("FOUNDRY_LITE_OTEL_CONSOLE") == "1":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _CONFIGURED = True


def tracer() -> trace.Tracer:
    return trace.get_tracer("foundry_lite")


def _ctx_from_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> RequestContext | None:
    ctx = kwargs.get("ctx")
    if isinstance(ctx, RequestContext):
        return ctx
    for value in args:
        if isinstance(value, RequestContext):
            return value
    return None


def trace_operation[F: Callable[..., Any]](operation: str, func: F) -> F:
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        ctx = _ctx_from_call(args, kwargs)
        with tracer().start_as_current_span(operation) as span:
            span.set_attribute("foundry_lite.operation", operation)
            if ctx is not None:
                span.set_attribute("foundry_lite.tenant_id", ctx.tenant_id)
                span.set_attribute("foundry_lite.actor_user_id", ctx.actor_user_id)
                span.set_attribute("foundry_lite.request_id", ctx.request_id)
            with core_operation(operation):
                return func(*args, **kwargs)

    return cast(F, wrapper)


def trace_public_methods[T](cls: type[T]) -> type[T]:
    for base in reversed(cls.mro()):
        if base is object:
            continue
        for name, value in list(base.__dict__.items()):
            if name.startswith("_") or not callable(value):
                continue
            if isinstance(value, staticmethod | classmethod):
                continue
            setattr(cls, name, trace_operation(f"{cls.__name__}.{name}", value))
    return cls


def instrument_fastapi_app(app: Any) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)


def instrument_sqlalchemy_engine(engine: Any) -> None:
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(engine=engine)
