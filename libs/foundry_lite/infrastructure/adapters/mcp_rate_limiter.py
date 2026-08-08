"""SQLAlchemy adapter for shared MCP fixed-window counters."""

from __future__ import annotations

import math
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.sql.elements import ColumnElement

from foundry_lite.application.ports.mcp_rate_limiter import (
    McpRateLimitDecision,
    McpRateLimiter,
    McpRateLimitRequest,
)
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.infrastructure import schema as db

_IDENTITY_COLUMNS = (
    "tenant_id",
    "plane",
    "application_id",
    "client_id",
    "actor_user_id",
    "limit_scope",
    "window_started_at_epoch",
)
_PRUNE_BATCH_SIZE = 128


class SqlAlchemyMcpRateLimiter(McpRateLimiter):
    """Consume counters with one atomic SQLite/PostgreSQL upsert."""

    def consume(
        self,
        *,
        transaction: TransactionContext,
        request: McpRateLimitRequest,
    ) -> McpRateLimitDecision:
        _require_positive_window(request)
        window_start = _window_start(request)
        window_expiry = window_start + request.window_seconds
        candidate_evidence_id = _new_evidence_id()
        connection = cast(Any, transaction)
        _prune_expired(connection, request, window_start)
        statement = _upsert_statement(
            connection,
            request,
            candidate_evidence_id,
            window_start,
            window_expiry,
        )
        row = connection.execute(statement).mappings().one()
        count = int(row["request_count"])
        is_allowed = count <= request.limit
        retry_after = 0 if is_allowed else max(1, math.ceil(window_expiry - request.observed_at_epoch))
        return McpRateLimitDecision(
            is_allowed=is_allowed,
            evidence_id=str(row["id"]),
            request_count=count,
            denied_count=int(row["denied_count"]),
            limit=request.limit,
            window_seconds=request.window_seconds,
            window_started_at_epoch=window_start,
            window_expires_at_epoch=window_expiry,
            retry_after_seconds=retry_after,
        )


def _upsert_statement(
    connection: Any,
    request: McpRateLimitRequest,
    candidate_evidence_id: str,
    window_start: int,
    window_expiry: int,
) -> Any:
    insert_statement = _dialect_insert(connection)
    values = _insert_values(request, candidate_evidence_id, window_start, window_expiry)
    is_denied = db.mcp_rate_limit_windows.c.request_count >= request.limit
    updates = _conflict_updates(request, is_denied)
    return (
        insert_statement.values(**values)
        .on_conflict_do_update(index_elements=_IDENTITY_COLUMNS, set_=updates)
        .returning(
            db.mcp_rate_limit_windows.c.id,
            db.mcp_rate_limit_windows.c.request_count,
            db.mcp_rate_limit_windows.c.denied_count,
        )
    )


def _dialect_insert(connection: Any) -> Any:
    if connection.dialect.name == "postgresql":
        return postgres_insert(db.mcp_rate_limit_windows)
    if connection.dialect.name == "sqlite":
        return sqlite_insert(db.mcp_rate_limit_windows)
    raise RuntimeError(f"unsupported MCP rate-limit dialect: {connection.dialect.name}")


def _prune_expired(connection: Any, request: McpRateLimitRequest, window_start: int) -> None:
    table = db.mcp_rate_limit_windows
    expired_ids = (
        select(table.c.id)
        .where(
            table.c.tenant_id == request.tenant_id,
            table.c.window_expires_at_epoch <= window_start,
        )
        .order_by(table.c.window_expires_at_epoch, table.c.id)
        .limit(_PRUNE_BATCH_SIZE)
    )
    connection.execute(delete(table).where(table.c.id.in_(expired_ids)))


def _insert_values(
    request: McpRateLimitRequest,
    candidate_evidence_id: str,
    window_start: int,
    window_expiry: int,
) -> dict[str, object]:
    return {
        "id": candidate_evidence_id,
        "tenant_id": request.tenant_id,
        "plane": request.plane,
        "application_id": request.application_id,
        "client_id": request.client_id,
        "actor_user_id": request.actor_user_id,
        "limit_scope": request.limit_scope,
        "window_started_at_epoch": window_start,
        "window_expires_at_epoch": window_expiry,
        "limit_value": request.limit,
        "window_seconds": request.window_seconds,
        "request_count": 1,
        "denied_count": 0,
        "last_request_id": request.request_id,
        "last_denied_at": None,
        "created_at": request.observed_at,
        "updated_at": request.observed_at,
    }


def _conflict_updates(request: McpRateLimitRequest, is_denied: ColumnElement[bool]) -> dict[str, object]:
    table = db.mcp_rate_limit_windows
    return {
        "limit_value": request.limit,
        "window_seconds": request.window_seconds,
        "request_count": table.c.request_count + 1,
        "denied_count": table.c.denied_count + case((is_denied, 1), else_=0),
        "last_request_id": request.request_id,
        "last_denied_at": case((is_denied, request.observed_at), else_=table.c.last_denied_at),
        "updated_at": request.observed_at,
    }


def _window_start(request: McpRateLimitRequest) -> int:
    return int(request.observed_at_epoch // request.window_seconds) * request.window_seconds


def _require_positive_window(request: McpRateLimitRequest) -> None:
    if request.limit <= 0:
        raise ValueError("MCP rate limit must be greater than zero")
    if request.window_seconds <= 0:
        raise ValueError("MCP rate-limit window must be greater than zero")


def _new_evidence_id() -> str:
    return f"mcp_rate_limit_{uuid4().hex}"


__all__ = ["SqlAlchemyMcpRateLimiter"]
