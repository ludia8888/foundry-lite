"""Safe server-side evidence for MCP JSON-RPC internal failures."""

from __future__ import annotations

import logging
import traceback
from pathlib import Path

from fastapi import Request
from foundry_lite.application.services.runtime_error_payloads import runtime_error_payload
from foundry_lite.observability.logging import log_event


def log_mcp_internal_failure(
    logger: logging.Logger,
    request: Request,
    *,
    plane: str,
    rpc_method: str,
    exc: Exception,
) -> None:
    """Log only classified coordinates; never serialize the exception message."""

    log_event(
        logger,
        "mcp.request.internal_failure",
        level=logging.ERROR,
        request_id=str(getattr(request.state, "request_id", "unknown")),
        plane=plane,
        rpc_method=rpc_method,
        error_type=type(exc).__name__,
        stack=_stack_summary(exc),
        cleanup_failures=_cleanup_failure_summary(exc),
    )


def _stack_summary(exc: Exception) -> list[dict[str, object]]:
    return [
        {"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
        for frame in traceback.extract_tb(exc.__traceback__)[-8:]
    ]


def _cleanup_failure_summary(exc: Exception) -> list[dict[str, object]]:
    details = runtime_error_payload(exc).get("details")
    failures = details.get("cleanupFailures") if isinstance(details, dict) else None
    return [dict(item) for item in failures if isinstance(item, dict)] if isinstance(failures, list) else []


__all__ = ["log_mcp_internal_failure"]
