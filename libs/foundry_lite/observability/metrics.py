from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

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


def prometheus_payload() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


def operation_labels(operation: str, **fields: Any) -> dict[str, Any]:
    return {"operation": operation, **fields}
