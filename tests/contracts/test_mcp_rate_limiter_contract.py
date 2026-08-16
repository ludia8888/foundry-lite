"""Contract proof for durable MCP fixed-window admission counters."""

from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.ports.mcp_rate_limiter import McpRateLimitRequest
from foundry_lite.application.services.mcp_rate_limit_service import (
    McpRateLimitConfig,
    McpRateLimitService,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import RateLimited
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters import mcp_rate_limiter as rate_limiter_adapter_module
from foundry_lite.infrastructure.adapters.mcp_rate_limiter import SqlAlchemyMcpRateLimiter
from foundry_lite.infrastructure.repositories import SqlAlchemyRuntimeRepository
from sqlalchemy import create_engine, func, insert, select
from sqlalchemy.engine import Engine


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    value = create_engine(
        f"sqlite:///{tmp_path / 'mcp-rate-limit.db'}",
        future=True,
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    db.metadata.create_all(value)
    return value


def test_fixed_window_counter_is_atomic_and_endpoint_tool_scopes_are_independent(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_ids = iter(("candidate-endpoint-first", "candidate-endpoint-replay", "candidate-tool"))
    monkeypatch.setattr(rate_limiter_adapter_module, "_new_evidence_id", lambda: next(candidate_ids))
    limiter = SqlAlchemyMcpRateLimiter()
    endpoint = _request(limit_scope="endpoint", request_id="req-endpoint-1")
    tool = _request(limit_scope="tool", request_id="req-tool-1")
    with engine.begin() as conn:
        first = limiter.consume(transaction=conn, request=endpoint)
        second = limiter.consume(transaction=conn, request=_replace_request(endpoint, "req-endpoint-2"))
        separate_tool = limiter.consume(transaction=conn, request=tool)
    assert first.is_allowed is True
    assert second.is_allowed is False
    assert second.request_count == 2
    assert second.denied_count == 1
    assert second.retry_after_seconds == 59
    assert first.evidence_id == second.evidence_id == "candidate-endpoint-first"
    assert separate_tool.is_allowed is True
    assert separate_tool.evidence_id == "candidate-tool"
    with engine.connect() as conn:
        persisted_ids = set(conn.execute(select(db.mcp_rate_limit_windows.c.id)).scalars())
    assert persisted_ids == {"candidate-endpoint-first", "candidate-tool"}


def test_non_positive_limit_and_window_fail_closed_before_counter_write(engine: Engine) -> None:
    limiter = SqlAlchemyMcpRateLimiter()
    invalid = (
        _request(limit=0),
        _request(window_seconds=0),
        _request(limit=-1),
        _request(window_seconds=-1),
    )
    for request in invalid:
        with engine.begin() as conn, pytest.raises(ValueError):
            limiter.consume(transaction=conn, request=request)
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(db.mcp_rate_limit_windows)).scalar_one() == 0


def test_expired_window_pruning_is_bounded_and_tenant_scoped(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(insert(db.mcp_rate_limit_windows), [_expired_row(index) for index in range(140)])
        conn.execute(insert(db.mcp_rate_limit_windows), _expired_row(999, tenant_id="tenant-other"))
        SqlAlchemyMcpRateLimiter().consume(transaction=conn, request=_request(observed_at_epoch=10_000.25))
    with engine.connect() as conn:
        own_count = conn.execute(
            select(func.count())
            .select_from(db.mcp_rate_limit_windows)
            .where(db.mcp_rate_limit_windows.c.tenant_id == "tenant-a")
        ).scalar_one()
        other_count = conn.execute(
            select(func.count())
            .select_from(db.mcp_rate_limit_windows)
            .where(db.mcp_rate_limit_windows.c.tenant_id == "tenant-other")
        ).scalar_one()
    assert own_count == 13
    assert other_count == 1


def test_counter_rolls_over_at_the_exact_fixed_window_boundary(engine: Engine) -> None:
    limiter = SqlAlchemyMcpRateLimiter()
    before_boundary = _request(observed_at_epoch=119.999)
    at_boundary = _request(request_id="req-next-window", observed_at_epoch=120.0)
    with engine.begin() as conn:
        first = limiter.consume(transaction=conn, request=before_boundary)
        next_window = limiter.consume(transaction=conn, request=at_boundary)
    assert first.is_allowed is True
    assert first.window_started_at_epoch == 60
    assert next_window.is_allowed is True
    assert next_window.request_count == 1
    assert next_window.window_started_at_epoch == 120
    assert next_window.evidence_id != first.evidence_id


def test_service_preserves_denial_audit_and_exact_retry_after(
    engine: Engine,
) -> None:
    service = McpRateLimitService(
        engine=engine,
        mcp_rate_limiter=SqlAlchemyMcpRateLimiter(),
        runtime_repository=SqlAlchemyRuntimeRepository(engine),
        config=McpRateLimitConfig(endpoint_limit=1, tool_limit=1, window_seconds=60),
        clock=lambda: 121.25,
    )
    first_ctx = _context("req-first")
    denied_ctx = _context("req-denied")
    service.consume_endpoint(first_ctx, plane="builder", application_id="app-a")
    with pytest.raises(RateLimited) as exc_info:
        service.consume_endpoint(denied_ctx, plane="builder", application_id="app-a")
    error = exc_info.value
    assert error.details["retryAfterSeconds"] == 59
    assert error.details["requestId"] == "req-denied"
    with engine.connect() as conn:
        window = conn.execute(select(db.mcp_rate_limit_windows)).mappings().one()
        audit = (
            conn.execute(select(db.audit_events).where(db.audit_events.c.event_type == "mcp.rate_limit.denied"))
            .mappings()
            .one()
        )
        outbox = (
            conn.execute(select(db.outbox_events).where(db.outbox_events.c.event_type == "mcp.rate_limit.denied"))
            .mappings()
            .one()
        )
    assert window["request_count"] == 2
    assert window["denied_count"] == 1
    assert window["last_request_id"] == "req-denied"
    assert audit["decision"] == "deny"
    assert audit["request_id"] == "req-denied"
    assert audit["after_ref"]["retryAfterSeconds"] == 59
    assert outbox["correlation_id"] == "req-denied"
    assert outbox["payload"]["retryAfterSeconds"] == 59
    assert outbox["idempotency_key"].endswith(":1")


def _request(
    *,
    limit_scope: str = "endpoint",
    request_id: str = "req-1",
    limit: int = 1,
    window_seconds: int = 60,
    observed_at_epoch: float = 121.25,
) -> McpRateLimitRequest:
    return McpRateLimitRequest(
        tenant_id="tenant-a",
        plane="builder",
        application_id="app-a",
        client_id="client-a",
        actor_user_id="actor-a",
        limit_scope=limit_scope,  # type: ignore[arg-type]
        limit=limit,
        window_seconds=window_seconds,
        request_id=request_id,
        observed_at_epoch=observed_at_epoch,
        observed_at="2026-08-09T00:02:01.250000+00:00",
    )


def _replace_request(request: McpRateLimitRequest, request_id: str) -> McpRateLimitRequest:
    return McpRateLimitRequest(**{**request.__dict__, "request_id": request_id})


def _context(request_id: str) -> RequestContext:
    return RequestContext(
        tenant_id="tenant-a",
        actor_user_id="actor-a",
        request_id=request_id,
        roles=("admin",),
        application_id="app-a",
        client_id="client-a",
    )


def _expired_row(index: int, *, tenant_id: str = "tenant-a") -> dict[str, object]:
    start = index * 10
    return {
        "id": f"expired-{tenant_id}-{index}",
        "tenant_id": tenant_id,
        "plane": "builder",
        "application_id": "app-a",
        "client_id": "client-a",
        "actor_user_id": "actor-a",
        "limit_scope": "endpoint",
        "window_started_at_epoch": start,
        "window_expires_at_epoch": start + 1,
        "limit_value": 1,
        "window_seconds": 1,
        "request_count": 1,
        "denied_count": 0,
        "last_request_id": f"req-expired-{index}",
        "last_denied_at": None,
        "created_at": "2026-08-08T00:00:00+00:00",
        "updated_at": "2026-08-08T00:00:00+00:00",
    }
