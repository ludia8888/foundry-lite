"""Observability helpers for metrics."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

REQUIRED_OPERATIONAL_METRICS = (
    "foundry_lite_dataset_commit_seconds",
    "foundry_lite_transform_run_seconds",
    "foundry_lite_action_apply_seconds",
    "foundry_lite_object_query_seconds",
    "foundry_lite_outbox_publish_lag_seconds",
    "foundry_lite_stream_archive_lag_events",
    "foundry_lite_failed_runs_total",
    "foundry_lite_dlq_size",
)

CORE_OPERATIONS = Counter(
    "foundry_lite_core_operations_total",
    "Foundry-lite core operation executions.",
    ["operation", "status", "error_code"],
)
CORE_OPERATION_SECONDS = Histogram(
    "foundry_lite_core_operation_seconds",
    "Foundry-lite core operation duration.",
    ["operation"],
)
HTTP_REQUESTS = Counter(
    "foundry_lite_http_requests_total",
    "Foundry-lite API HTTP requests.",
    ["method", "path", "status"],
)
HTTP_REQUEST_SECONDS = Histogram(
    "foundry_lite_http_request_seconds",
    "Foundry-lite API HTTP request duration.",
    ["method", "path"],
)
DATASET_COMMIT_SECONDS = Histogram(
    "foundry_lite_dataset_commit_seconds",
    "Foundry-lite dataset commit duration.",
)
TRANSFORM_RUN_SECONDS = Histogram(
    "foundry_lite_transform_run_seconds",
    "Foundry-lite transform run duration.",
)
ACTION_APPLY_SECONDS = Histogram(
    "foundry_lite_action_apply_seconds",
    "Foundry-lite action apply latency.",
)
OBJECT_QUERY_SECONDS = Histogram(
    "foundry_lite_object_query_seconds",
    "Foundry-lite object query latency.",
)
OUTBOX_PUBLISH_LAG_SECONDS = Gauge(
    "foundry_lite_outbox_publish_lag_seconds",
    "Foundry-lite outbox publish lag.",
)
STREAM_ARCHIVE_LAG_EVENTS = Gauge(
    "foundry_lite_stream_archive_lag_events",
    "Foundry-lite stream archive unread event lag.",
)
FAILED_RUNS_TOTAL = Counter(
    "foundry_lite_failed_runs_total",
    "Foundry-lite failed run count.",
)
DLQ_SIZE = Gauge(
    "foundry_lite_dlq_size",
    "Foundry-lite dead-letter queue size.",
)


@contextmanager
def core_operation(operation: str) -> Iterator[None]:
    started_at = time.perf_counter()
    status = "succeeded"
    error_code = "none"
    try:
        yield
    except Exception as exc:
        status = "failed"
        error_code = getattr(exc, "code", exc.__class__.__name__)
        raise
    finally:
        CORE_OPERATIONS.labels(operation=operation, status=status, error_code=error_code).inc()
        CORE_OPERATION_SECONDS.labels(operation=operation).observe(time.perf_counter() - started_at)


def record_http_request(method: str, path: str, status_code: int, duration_seconds: float) -> None:
    status = str(status_code)
    HTTP_REQUESTS.labels(method=method, path=path, status=status).inc()
    HTTP_REQUEST_SECONDS.labels(method=method, path=path).observe(duration_seconds)


def record_dataset_commit(duration_seconds: float) -> None:
    DATASET_COMMIT_SECONDS.observe(duration_seconds)


def record_transform_run(duration_seconds: float) -> None:
    TRANSFORM_RUN_SECONDS.observe(duration_seconds)


def record_action_apply(duration_seconds: float) -> None:
    ACTION_APPLY_SECONDS.observe(duration_seconds)


def record_object_query(duration_seconds: float) -> None:
    OBJECT_QUERY_SECONDS.observe(duration_seconds)


def set_outbox_publish_lag(duration_seconds: float) -> None:
    OUTBOX_PUBLISH_LAG_SECONDS.set(duration_seconds)


def set_stream_archive_lag(events: int) -> None:
    STREAM_ARCHIVE_LAG_EVENTS.set(events)


def record_failed_run() -> None:
    FAILED_RUNS_TOTAL.inc()


def set_dlq_size(size: int) -> None:
    DLQ_SIZE.set(size)


def prometheus_payload() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


def operation_labels(operation: str, **fields: Any) -> dict[str, Any]:
    return {"operation": operation, **fields}
