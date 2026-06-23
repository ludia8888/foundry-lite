"""Shared Elasticsearch client construction + error classification.

Both the object-search adapter and the content-index adapter talk to the same
Elasticsearch surface, so the lazy client builder and the elastic_transport ->
typed AdapterError mapping live here rather than being duplicated. elasticsearch
is imported lazily so it stays an optional dependency for local/test runs.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any, cast

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureKind,
)


def build_es_client(
    *,
    endpoint: str,
    username: str | None = None,
    password: str | None = None,
    request_timeout_seconds: int = 30,
    max_retries: int = 3,
) -> Any:
    module = importlib.import_module("elasticsearch")
    client_type = cast(Callable[..., object], module.Elasticsearch)
    auth = (username, password) if username and password else None
    return client_type(
        hosts=[endpoint],
        basic_auth=auth,
        request_timeout=request_timeout_seconds,
        retry_on_timeout=True,
        max_retries=max_retries,
    )


def classify_es_error(profile: str, operation: str, exc: Exception) -> AdapterError:
    """Map an Elasticsearch transport/API exception to a typed, classified AdapterError."""
    transport = importlib.import_module("elastic_transport")
    kind, is_retryable, timeout = _es_failure_kind(transport, exc)
    return AdapterError(
        AdapterFailure(
            adapter_profile=profile,
            operation=operation,
            kind=kind,
            is_retryable=is_retryable,
            operator_message=f"Elasticsearch {operation} failed ({kind}): {exc}",
            timeout_seconds=timeout,
            details={"exceptionType": type(exc).__name__},
        )
    )


def _es_failure_kind(transport: object, exc: Exception) -> tuple[AdapterFailureKind, bool, int | None]:
    connection_timeout = getattr(transport, "ConnectionTimeout", ())
    connection_error = getattr(transport, "ConnectionError", ())
    api_error = getattr(transport, "ApiError", ())
    if isinstance(exc, connection_timeout):
        return "timeout", True, 30
    if isinstance(exc, api_error):
        return _api_error_kind(exc)
    if isinstance(exc, connection_error):
        # Connection refused / DNS / TLS — the cluster is unreachable, not wrong.
        return "unavailable", True, None
    return "unknown", False, None


def _api_error_kind(exc: Exception) -> tuple[AdapterFailureKind, bool, int | None]:
    status = getattr(getattr(exc, "meta", None), "status", None)
    if status == 429:
        return "rate_limited", True, None
    if isinstance(status, int) and status >= 500:
        return "unavailable", True, None
    if status == 409:
        return "conflict", False, None
    return "validation", False, None
