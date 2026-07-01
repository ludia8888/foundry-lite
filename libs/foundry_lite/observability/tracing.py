"""Observability helpers for tracing."""

from __future__ import annotations

import functools
import hashlib
import os
from collections.abc import Callable, Mapping
from typing import Any, cast

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor

from foundry_lite.domain.context import RequestContext
from foundry_lite.observability.metrics import core_operation

_CONFIGURED = False
_TRACED_MARKER = "__foundry_lite_traced__"
_TRACE_IDENTITY_HASH_PREFIX = 16


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


def trace_identity_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:_TRACE_IDENTITY_HASH_PREFIX]


def _set_context_attributes(span: Any, ctx: RequestContext) -> None:
    span.set_attribute("foundry_lite.tenant_hash", trace_identity_hash(ctx.tenant_id))
    span.set_attribute("foundry_lite.actor_user_hash", trace_identity_hash(ctx.actor_user_id))
    span.set_attribute("foundry_lite.request_id", ctx.request_id)


def trace_operation[F: Callable[..., Any]](operation: str, func: F) -> F:
    if getattr(func, _TRACED_MARKER, False):
        return func

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        ctx = _ctx_from_call(args, kwargs)
        with tracer().start_as_current_span(operation) as span:
            span.set_attribute("foundry_lite.operation", operation)
            if ctx is not None:
                _set_context_attributes(span, ctx)
            with core_operation(operation):
                return func(*args, **kwargs)

    setattr(wrapper, _TRACED_MARKER, True)
    return cast(F, wrapper)


def trace_public_methods[T](cls: type[T], *, include_inherited: bool = True) -> type[T]:
    classes = reversed(cls.mro()) if include_inherited else (cls,)
    for base in classes:
        _trace_class_dict(cls, base.__dict__)
    return cls


def _trace_class_dict[T](target: type[T], values: Mapping[str, Any]) -> None:
    for name, value in list(values.items()):
        if name.startswith("_") or not callable(value):
            continue
        if isinstance(value, staticmethod | classmethod):
            continue
        setattr(target, name, trace_operation(f"{target.__name__}.{name}", value))


def trace_direct_public_methods[T](cls: type[T]) -> type[T]:
    return trace_public_methods(cls, include_inherited=False)


def instrument_fastapi_app(app: Any) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)


def instrument_sqlalchemy_engine(engine: Any) -> None:
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(engine=engine)
