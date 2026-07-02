from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import OsdkApplicationBundle
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.infrastructure.auth import JwtOidcAuthConfig, JwtOidcAuthProvider
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite_api import main as api_main
from foundry_lite_api import runtime as api_runtime
from starlette.websockets import WebSocketDisconnect

from tests.conftest import prepare_indexed_demo

_REDIRECT_URI = "https://orders.example.test/oauth/callback"
_SUBSCRIBE_SCOPE = "osdk:object:Order:subscribe"


def test_object_subscription_websocket_route_streams_snapshot_with_app_scope(
    monkeypatch,
    tmp_path: Path,
) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = prepare_indexed_demo(foundry)
    app = _create_subscription_app(foundry, ctx, client_id="orders-ws-client")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)

    with client.websocket_connect(
        "/api/objects/Order/subscriptions/ws",
        headers={
            "X-Tenant-ID": ctx.tenant_id,
            "X-User-ID": ctx.actor_user_id,
            "X-Roles": ",".join(ctx.roles),
            "X-Foundry-Lite-App-ID": cast(str, app["application"]["id"]),
            "X-Foundry-Lite-Client-ID": "orders-ws-client",
            "X-Foundry-Lite-Scopes": _SUBSCRIBE_SCOPE,
        },
    ) as websocket:
        websocket.send_json({"pageSize": 1, "maxEvents": 1})
        event = websocket.receive_json()

    assert event["event"] == "snapshot"
    assert event["items"][0]["objectType"] == "Order"


def test_object_subscription_websocket_route_accepts_oauth_bearer_subprotocol(
    monkeypatch,
    tmp_path: Path,
) -> None:
    foundry, verifier = _oauth_foundry(tmp_path)
    ctx = prepare_indexed_demo(foundry)
    _create_subscription_app(foundry, ctx, client_id="orders-oauth-ws-client")
    token = _oauth_access_token(foundry, ctx)
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    monkeypatch.setattr(api_runtime, "auth_provider", verifier)
    client = TestClient(api_main.app)

    with client.websocket_connect(
        "/api/objects/Order/subscriptions/ws",
        subprotocols=["foundry-lite", f"bearer.{token}"],
    ) as websocket:
        websocket.send_json({"pageSize": 1, "maxEvents": 1})
        event = websocket.receive_json()

    assert event["event"] == "snapshot"


def test_object_subscription_websocket_route_returns_permission_error_without_subscribe_scope(
    monkeypatch,
    tmp_path: Path,
) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = prepare_indexed_demo(foundry)
    app = _create_subscription_app(foundry, ctx, client_id="orders-denied-ws-client")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)

    with client.websocket_connect(
        "/api/objects/Order/subscriptions/ws",
        headers={
            "X-Tenant-ID": ctx.tenant_id,
            "X-User-ID": ctx.actor_user_id,
            "X-Roles": ",".join(ctx.roles),
            "X-Foundry-Lite-App-ID": cast(str, app["application"]["id"]),
            "X-Foundry-Lite-Client-ID": "orders-denied-ws-client",
            "X-Foundry-Lite-Scopes": "osdk:object:Order:read",
        },
    ) as websocket:
        websocket.send_json({"pageSize": 1, "maxEvents": 1})
        event = websocket.receive_json()

    assert event["event"] == "error"
    assert event["error"]["code"] == "PERMISSION_DENIED"


def test_object_subscription_websocket_route_fanout_scale_uses_isolated_snapshots(
    monkeypatch,
    tmp_path: Path,
) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = prepare_indexed_demo(foundry)
    app = _create_subscription_app(foundry, ctx, client_id="orders-fanout-client")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    monkeypatch.setattr(api_runtime, "websocket_subscription_rate_limiter", api_main._ApiWindowRateLimiter())
    client = TestClient(api_main.app)
    events = []

    for index in range(8):
        with client.websocket_connect(
            "/api/objects/Order/subscriptions/ws",
            headers=_subscription_headers(ctx, app, "orders-fanout-client", user_id=f"fanout-user-{index}"),
        ) as websocket:
            websocket.send_json({"pageSize": 1, "maxEvents": 1})
            events.append(websocket.receive_json())

    assert [event["event"] for event in events] == ["snapshot"] * 8
    assert {event["items"][0]["objectType"] for event in events} == {"Order"}


def test_object_subscription_websocket_route_denies_cross_tenant_app_scope(
    monkeypatch,
    tmp_path: Path,
) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = prepare_indexed_demo(foundry)
    app = _create_subscription_app(foundry, ctx, client_id="orders-tenant-client")
    other_tenant = RequestContext(
        tenant_id="tenant-other",
        actor_user_id="other-user",
        roles=demo_admin_context().roles,
    )
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)

    with client.websocket_connect(
        "/api/objects/Order/subscriptions/ws",
        headers=_subscription_headers(other_tenant, app, "orders-tenant-client"),
    ) as websocket:
        websocket.send_json({"pageSize": 1, "maxEvents": 1})
        event = websocket.receive_json()

    assert event["event"] == "error"
    assert event["error"]["code"] == "PERMISSION_DENIED"


def test_object_subscription_websocket_route_rate_denial_is_terminal_event(
    monkeypatch,
    tmp_path: Path,
) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = prepare_indexed_demo(foundry)
    app = _create_subscription_app(foundry, ctx, client_id="orders-rate-client")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    monkeypatch.setattr(api_runtime, "WEBSOCKET_SUBSCRIPTION_CONNECT_LIMIT", 1)
    monkeypatch.setattr(api_runtime, "WEBSOCKET_SUBSCRIPTION_CONNECT_WINDOW_SECONDS", 60.0)
    monkeypatch.setattr(api_runtime, "websocket_subscription_rate_limiter", api_main._ApiWindowRateLimiter())
    client = TestClient(api_main.app)
    headers = _subscription_headers(ctx, app, "orders-rate-client")

    with client.websocket_connect("/api/objects/Order/subscriptions/ws", headers=headers) as websocket:
        websocket.send_json({"pageSize": 1, "maxEvents": 1})
        assert websocket.receive_json()["event"] == "snapshot"
    with client.websocket_connect("/api/objects/Order/subscriptions/ws", headers=headers) as websocket:
        websocket.send_json({"pageSize": 1, "maxEvents": 1})
        event = websocket.receive_json()

    assert event["event"] == "error"
    assert event["error"]["code"] == "RATE_LIMITED"
    assert event["error"]["details"]["retryAfterSeconds"] >= 1


def test_api_window_rate_limiter_prunes_expired_buckets(monkeypatch) -> None:
    # object_type comes from the URL path, so bucket keys are attacker-controlled;
    # without pruning the limiter grows one bucket per unique key forever (Tier 3
    # resource-ceiling item in the tricky failure modes checklist).
    limiter = api_main._ApiWindowRateLimiter()
    now = 1_000.0
    monkeypatch.setattr("foundry_lite_api.runtime.time", SimpleNamespace(monotonic=lambda: now))
    for index in range(300):
        limiter.retry_after_seconds(("tenant", f"object-{index}"), limit=5, window_seconds=60.0)
    assert len(limiter.buckets) == 300

    now = 2_000.0
    limiter.retry_after_seconds(("tenant", "fresh-object"), limit=5, window_seconds=60.0)

    assert set(limiter.buckets) == {("tenant", "fresh-object")}


def test_object_subscription_websocket_route_rejects_untrusted_browser_origin(
    monkeypatch,
    tmp_path: Path,
) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = prepare_indexed_demo(foundry)
    app = _create_subscription_app(foundry, ctx, client_id="orders-origin-client")
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/objects/Order/subscriptions/ws",
            headers={
                **_subscription_headers(ctx, app, "orders-origin-client"),
                "Origin": "https://evil.example.test",
            },
        ):
            pass


def _oauth_foundry(tmp_path: Path) -> tuple[FoundryLite, JwtOidcAuthProvider]:
    deps = create_local_core_dependencies(storage_root=tmp_path / "flite")
    foundry = FoundryLite(dependencies=deps)
    issuer = cast(Any, deps.oauth_token_issuer)
    verifier = JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer=cast(str, issuer.issuer),
            audience=cast(str, issuer.audience),
            jwks=issuer.public_jwks(),
        )
    )
    return foundry, verifier


def _create_subscription_app(foundry: FoundryLite, ctx: RequestContext, *, client_id: str) -> OsdkApplicationBundle:
    app = foundry.developer_console.create_osdk_application(
        app_api_name=client_id.replace("-", "_"),
        display_name=client_id,
        resources=({"resourceType": "object", "resourceApiName": "Order", "scopes": [_SUBSCRIBE_SCOPE]},),
        idempotency_key=f"{client_id}-app",
        ctx=ctx,
    )
    foundry.developer_console.create_osdk_application_client(
        cast(str, app["application"]["id"]),
        client_id=client_id,
        redirect_uris=(_REDIRECT_URI,),
        allowed_scopes=(_SUBSCRIBE_SCOPE,),
        idempotency_key=f"{client_id}-client",
        ctx=ctx,
    )
    return app


def _subscription_headers(
    ctx: RequestContext,
    app: OsdkApplicationBundle,
    client_id: str,
    *,
    user_id: str | None = None,
) -> dict[str, str]:
    return {
        "X-Tenant-ID": ctx.tenant_id,
        "X-User-ID": user_id or ctx.actor_user_id,
        "X-Roles": ",".join(ctx.roles),
        "X-Foundry-Lite-App-ID": cast(str, app["application"]["id"]),
        "X-Foundry-Lite-Client-ID": client_id,
        "X-Foundry-Lite-Scopes": _SUBSCRIBE_SCOPE,
    }


def _oauth_access_token(foundry: FoundryLite, ctx: RequestContext) -> str:
    verifier = "orders-ws-verifier"
    authorized = foundry.auth.osdk_oauth_authorize(
        client_id="orders-oauth-ws-client",
        redirect_uri=_REDIRECT_URI,
        code_challenge=_s256_challenge(verifier),
        scopes=(_SUBSCRIBE_SCOPE,),
        ctx=ctx,
    )
    token = foundry.auth.osdk_oauth_token(
        client_id="orders-oauth-ws-client",
        code=cast(str, authorized["code"]),
        redirect_uri=_REDIRECT_URI,
        code_verifier=verifier,
        ctx=ctx,
    )
    return cast(str, token["accessToken"])


def _s256_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
