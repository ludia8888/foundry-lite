"""HTTP admission response helpers for durable MCP rate limits."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import HTTPException, Request
from foundry_lite.domain.errors import RateLimited

from foundry_lite_api.errors import _handle_error


def mcp_rate_limit_http_error(exc: RateLimited, request: Request) -> HTTPException:
    """Preserve the domain payload and expose the exact fixed-window retry value."""

    failure = _handle_error(exc, request)
    retry_after = exc.details.get("retryAfterSeconds")
    if not isinstance(retry_after, int) or retry_after <= 0:
        raise RuntimeError("MCP rate-limit decision omitted a positive retryAfterSeconds")
    failure.headers = {"Retry-After": str(retry_after)}
    return failure


def mcp_result_headers(
    result: Mapping[str, object],
    base_headers: Mapping[str, str],
) -> dict[str, str]:
    """Add Retry-After to an HTTP-200 MCP tool denial without changing its body."""

    retry_after = _tool_retry_after(result)
    headers = dict(base_headers)
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return headers


def _tool_retry_after(result: Mapping[str, object]) -> int | None:
    structured = result.get("structuredContent")
    if not isinstance(structured, Mapping):
        return None
    error = structured.get("error")
    if not isinstance(error, Mapping) or error.get("type") != "RATE_LIMITED":
        return None
    details = error.get("details")
    retry_after = details.get("retryAfterSeconds") if isinstance(details, Mapping) else None
    if not isinstance(retry_after, int) or retry_after <= 0:
        raise RuntimeError("MCP tool rate-limit result omitted a positive retryAfterSeconds")
    return retry_after


__all__ = ["mcp_rate_limit_http_error", "mcp_result_headers"]
