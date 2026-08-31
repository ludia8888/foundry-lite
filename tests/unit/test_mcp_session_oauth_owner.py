"""Regression tests for hash-only MCP OAuth-session ownership."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from foundry_lite.application.services.aip.fde_mcp_sessions import FdeMcpSessionLedger
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.osdk_application_repository import (
    SqlAlchemyOsdkApplicationRepository,
)
from foundry_lite.security.tenant_context import current_tenant_id
from sqlalchemy import create_engine, event


def test_release_mcp_session_rejects_a_different_oauth_session_without_storing_raw_sid() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.create_database(engine)
    ledger = FdeMcpSessionLedger(
        engine,
        SqlAlchemyOsdkApplicationRepository(engine),
        plane="release",
    )
    ctx = _context()
    session_id = "mcp-release-oauth-owner-0001"

    ledger.open(ctx, "release-app", session_id)
    assert ledger.require_active(ctx, "release-app", session_id)["id"] == session_id

    with pytest.raises(NotFound, match="Governed Release MCP session not found"):
        ledger.require_active(
            replace(ctx, oauth_session_hash="oauth-session:sha256:different"),
            "release-app",
            session_id,
        )
    with pytest.raises(NotFound, match="Governed Release MCP session not found"):
        ledger.require_active(replace(ctx, oauth_session_authority="issuer"), "release-app", session_id)

    persisted = json.dumps(ledger.events(ctx, "release-app", session_id), sort_keys=True)
    assert "raw-oauth-session-must-not-persist" not in persisted
    assert "oauth-session:sha256:original" in persisted
    assert '"oauthSessionAuthority": "local"' in persisted


def test_release_mcp_session_binds_authenticated_tenant_before_each_transaction() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.create_database(engine)
    observed_tenants: list[str | None] = []
    event.listen(engine, "begin", lambda _conn: observed_tenants.append(current_tenant_id()))
    ledger = FdeMcpSessionLedger(
        engine,
        SqlAlchemyOsdkApplicationRepository(engine),
        plane="release",
    )
    ctx = _context()

    ledger.open(ctx, "release-app", "mcp-release-tenant-binding-0001")
    ledger.require_active(ctx, "release-app", "mcp-release-tenant-binding-0001")

    assert observed_tenants == [ctx.tenant_id, ctx.tenant_id]
    assert current_tenant_id() is None


def _context() -> RequestContext:
    return RequestContext(
        tenant_id="tenant-release-session",
        actor_user_id="release-reviewer",
        application_id="release-app",
        client_id="release-client",
        oauth_session_id="raw-oauth-session-must-not-persist",
        oauth_session_hash="oauth-session:sha256:original",
        oauth_session_authority="local",
    )
