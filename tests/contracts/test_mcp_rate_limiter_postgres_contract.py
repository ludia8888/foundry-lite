"""Live PostgreSQL concurrency and RLS proof for MCP rate-limit windows."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any
from uuid import uuid4

import pytest
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
from sqlalchemy import create_engine, func, insert, select, text
from sqlalchemy.engine import Connection, Engine

from tests.contracts.test_postgres_rls_contract import _grant_rls_role, _rls_enabled, _rls_forced


def test_two_service_instances_share_one_postgres_atomic_tool_limit(
    postgres_fixture: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempted_ids: list[str] = []
    candidate_lock = Lock()

    def candidate_id() -> str:
        with candidate_lock:
            value = f"mcp_rate_limit_attempt_{len(attempted_ids)}"
            attempted_ids.append(value)
            return value

    monkeypatch.setattr(rate_limiter_adapter_module, "_new_evidence_id", candidate_id)
    engine_a = create_engine(postgres_fixture.engine.url, future=True, pool_size=10, max_overflow=10)
    engine_b = create_engine(postgres_fixture.engine.url, future=True, pool_size=10, max_overflow=10)
    services = (_service(engine_a, limit=25), _service(engine_b, limit=25))

    def consume(index: int) -> tuple[bool, str]:
        ctx = _context("tenant-test", f"req-postgres-{index}")
        try:
            decision = services[index % 2].consume_tool(ctx, plane="ontology", application_id="app-shared")
        except RateLimited as error:
            return False, str(error.details["evidenceId"])
        return True, decision.evidence_id

    try:
        with ThreadPoolExecutor(max_workers=16) as pool:
            decisions = list(pool.map(consume, range(80)))
    finally:
        engine_a.dispose()
        engine_b.dispose()

    allowed = [is_allowed for is_allowed, _evidence_id in decisions]
    response_evidence_ids = {evidence_id for _is_allowed, evidence_id in decisions}
    assert allowed.count(True) == 25
    assert allowed.count(False) == 55
    assert len(attempted_ids) == 80
    assert len(set(attempted_ids)) == 80
    with postgres_fixture.engine.connect() as conn:
        window = conn.execute(select(db.mcp_rate_limit_windows)).mappings().one()
        audit_count = _event_count(conn, db.audit_events, "resource_id", window["id"])
        outbox_count = _event_count(conn, db.outbox_events, "aggregate_id", window["id"])
    assert response_evidence_ids == {window["id"]}
    assert window["request_count"] == 80
    assert window["denied_count"] == 55
    assert audit_count == 55
    assert outbox_count == 55


def test_postgres_rls_hides_rate_windows_and_denial_evidence_between_tenants(
    postgres_fixture: Any,
) -> None:
    engine = postgres_fixture.engine
    with engine.begin() as conn:
        conn.execute(
            insert(db.tenants),
            {"id": "tenant-other", "name": "Other", "created_at": "2026-08-09T00:00:00Z"},
        )
    service = _service(engine, limit=1)
    for tenant_id in ("tenant-test", "tenant-other"):
        service.consume_endpoint(
            _context(tenant_id, f"req-{tenant_id}-allowed"),
            plane="builder",
            application_id="app-a",
        )
        with pytest.raises(RateLimited):
            service.consume_endpoint(
                _context(tenant_id, f"req-{tenant_id}-denied"),
                plane="builder",
                application_id="app-a",
            )

    role_name = f"foundry_lite_mcp_rate_limit_{uuid4().hex}"
    _grant_rls_role(engine, role_name)
    assert _rls_enabled(engine, db.mcp_rate_limit_windows.name)
    assert _rls_forced(engine, db.mcp_rate_limit_windows.name)
    for tenant_id in ("tenant-test", "tenant-other"):
        visible = _visible_denial_evidence(engine, role_name, tenant_id)
        assert visible == {
            "windowTenants": [tenant_id],
            "auditTenants": [tenant_id],
            "outboxTenants": [tenant_id],
        }


def _service(engine: Engine, *, limit: int) -> McpRateLimitService:
    return McpRateLimitService(
        engine=engine,
        mcp_rate_limiter=SqlAlchemyMcpRateLimiter(),
        runtime_repository=SqlAlchemyRuntimeRepository(engine),
        config=McpRateLimitConfig(endpoint_limit=limit, tool_limit=limit, window_seconds=60),
        clock=lambda: 121.25,
    )


def _context(tenant_id: str, request_id: str) -> RequestContext:
    return RequestContext(
        tenant_id=tenant_id,
        actor_user_id="actor-shared",
        request_id=request_id,
        roles=("admin",),
        application_id="app-shared",
        client_id="client-shared",
    )


def _event_count(conn: Connection, table: Any, identity_column: str, evidence_id: str) -> int:
    value = conn.execute(
        select(func.count())
        .select_from(table)
        .where(
            table.c.event_type == "mcp.rate_limit.denied",
            table.c[identity_column] == evidence_id,
        )
    ).scalar_one()
    return int(value)


def _visible_denial_evidence(engine: Engine, role_name: str, tenant_id: str) -> dict[str, list[str]]:
    with engine.begin() as conn:
        role = conn.dialect.identifier_preparer.quote(role_name)
        conn.execute(text(f"SET LOCAL ROLE {role}"))
        db.set_postgres_tenant_context(conn, tenant_id)
        return {
            "windowTenants": _tenant_values(conn, db.mcp_rate_limit_windows),
            "auditTenants": _tenant_values(conn, db.audit_events, is_denial_only=True),
            "outboxTenants": _tenant_values(conn, db.outbox_events, is_denial_only=True),
        }


def _tenant_values(conn: Connection, table: Any, *, is_denial_only: bool = False) -> list[str]:
    statement = select(table.c.tenant_id)
    if is_denial_only:
        statement = statement.where(table.c.event_type == "mcp.rate_limit.denied")
    return sorted(str(value) for value in conn.execute(statement).scalars())
