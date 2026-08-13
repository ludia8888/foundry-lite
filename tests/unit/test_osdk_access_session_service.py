from __future__ import annotations

from contextlib import nullcontext

import pytest
from foundry_lite.application.services.osdk_access_session_service import OsdkAccessSessionService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied


class _Engine:
    def __init__(self) -> None:
        self.begin_count = 0

    def begin(self):  # type: ignore[no-untyped-def]
        self.begin_count += 1
        return nullcontext(object())


class _Sessions:
    def __init__(self) -> None:
        self.lookup_count = 0

    def session_by_id(self, **_kwargs: object) -> None:
        self.lookup_count += 1
        return None


def test_issuer_authoritative_session_does_not_query_local_oauth_ledger() -> None:
    engine = _Engine()
    sessions = _Sessions()
    service = OsdkAccessSessionService(engine=engine, oauth_session_repository=sessions)
    ctx = RequestContext(
        application_id="app-1",
        client_id="chatgpt-client",
        oauth_session_id="issuer-session:hashed",
        oauth_session_authority="issuer",
        is_human_oauth=True,
    )

    service.require_active(ctx, "app-1")

    assert engine.begin_count == 0
    assert sessions.lookup_count == 0


def test_local_authoritative_session_still_fails_closed_when_ledger_row_is_missing() -> None:
    engine = _Engine()
    sessions = _Sessions()
    service = OsdkAccessSessionService(engine=engine, oauth_session_repository=sessions)
    ctx = RequestContext(
        application_id="app-1",
        client_id="local-client",
        oauth_session_id="local-session-missing",
        oauth_session_authority="local",
    )

    with pytest.raises(PermissionDenied, match="access session is inactive"):
        service.require_active(ctx, "app-1")

    assert engine.begin_count == 1
    assert sessions.lookup_count == 1
