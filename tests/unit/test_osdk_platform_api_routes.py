from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import cast

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite_api import main as api_main
from foundry_lite_api import runtime as api_runtime
from foundry_lite_api import webhooks as api_webhooks


def test_developer_console_osdk_application_route_requires_idempotency_key(monkeypatch, tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)
    headers = _admin_headers()
    payload = {
        "appApiName": "OrdersApiApp",
        "displayName": "Orders API App",
        "clientId": "orders-api-client",
        "resources": [
            {
                "resourceType": "object",
                "resourceApiName": "Order",
                "scopes": ["osdk:object:Order:subscribe"],
            }
        ],
    }

    missing = client.post("/api/developer-console/osdk-applications", json=payload, headers=headers)
    created = client.post(
        "/api/developer-console/osdk-applications",
        json=payload,
        headers={**headers, "Idempotency-Key": "api-osdk-app-create"},
    )

    assert missing.status_code == 422
    assert created.status_code == 200
    assert created.json()["application"]["app_api_name"] == "OrdersApiApp"


def test_object_subscription_stream_route_fails_before_open_when_app_scope_is_missing(
    monkeypatch, tmp_path: Path
) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)

    response = client.post(
        "/api/objects/Order/subscriptions/stream",
        json={"pageSize": 1, "maxEvents": 1},
        headers={
            **_admin_headers(),
            "X-Foundry-Lite-App-ID": "osdk_app_missing",
            "X-Foundry-Lite-Client-ID": "orders-api-client",
            "X-Foundry-Lite-Scopes": "osdk:object:Order:subscribe",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "PERMISSION_DENIED"


def test_osdk_oauth_api_routes_issue_refresh_and_revoke_session(monkeypatch, tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    app = foundry.developer_console.create_osdk_application(
        app_api_name="OrdersOAuthApi",
        display_name="Orders OAuth API",
        resources=(
            {
                "resourceType": "object",
                "resourceApiName": "Order",
                "scopes": ["osdk:object:Order:read"],
            },
        ),
        idempotency_key="api-oauth-app",
        ctx=ctx,
    )
    foundry.developer_console.create_osdk_application_client(
        cast(str, app["application"]["id"]),
        client_id="orders-oauth-api-client",
        redirect_uris=("https://orders.example.test/oauth/callback",),
        allowed_scopes=("osdk:object:Order:read",),
        idempotency_key="api-oauth-client",
        ctx=ctx,
    )
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)
    verifier = "api-verifier"
    authorized = client.get(
        "/api/auth/osdk/oauth/authorize",
        params={
            "clientId": "orders-oauth-api-client",
            "redirectUri": "https://orders.example.test/oauth/callback",
            "codeChallenge": _s256(verifier),
            "scope": "osdk:object:Order:read",
        },
        headers=_admin_headers(),
    )
    exchanged = client.post(
        "/api/auth/osdk/oauth/token",
        json={
            "clientId": "orders-oauth-api-client",
            "code": authorized.json()["code"],
            "redirectUri": "https://orders.example.test/oauth/callback",
            "codeVerifier": verifier,
        },
        headers=_admin_headers(),
    )
    refreshed = client.post(
        "/api/auth/osdk/oauth/refresh",
        json={"refreshToken": exchanged.json()["refreshToken"]},
        headers=_admin_headers(),
    )
    revoked = client.post(
        "/api/auth/osdk/oauth/revoke",
        json={"refreshToken": refreshed.json()["refreshToken"]},
        headers=_admin_headers(),
    )

    assert authorized.status_code == 200
    assert exchanged.status_code == 200
    assert exchanged.json()["tokenType"] == "Bearer"
    assert refreshed.status_code == 200
    assert refreshed.json()["refreshToken"] != exchanged.json()["refreshToken"]
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"


def test_osdk_oauth_api_routes_rate_limit_returns_retryable_429(monkeypatch, tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    ctx = demo_admin_context()
    app = foundry.developer_console.create_osdk_application(
        app_api_name="OrdersOAuthRateApi",
        display_name="Orders OAuth Rate API",
        resources=(
            {
                "resourceType": "object",
                "resourceApiName": "Order",
                "scopes": ["osdk:object:Order:read"],
            },
        ),
        idempotency_key="api-oauth-rate-app",
        ctx=ctx,
    )
    foundry.developer_console.create_osdk_application_client(
        cast(str, app["application"]["id"]),
        client_id="orders-oauth-rate-client",
        redirect_uris=("https://orders.example.test/oauth/callback",),
        allowed_scopes=("osdk:object:Order:read",),
        idempotency_key="api-oauth-rate-client",
        ctx=ctx,
    )
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)
    params = {
        "clientId": "orders-oauth-rate-client",
        "redirectUri": "https://orders.example.test/oauth/callback",
        "codeChallenge": _s256("rate-verifier"),
        "scope": "osdk:object:Order:read",
    }

    for _index in range(5):
        assert client.get("/api/auth/osdk/oauth/authorize", params=params, headers=_admin_headers()).status_code == 200
    limited = client.get("/api/auth/osdk/oauth/authorize", params=params, headers=_admin_headers())

    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "RATE_LIMITED"
    assert limited.json()["detail"]["details"]["retryAfterSeconds"] >= 1


def test_developer_console_osdk_lifecycle_routes_round_trip(monkeypatch, tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)
    headers = _admin_headers()

    created = client.post(
        "/api/developer-console/osdk-applications",
        json={
            "appApiName": "OrdersLifecycleApi",
            "displayName": "Orders Lifecycle API",
            "clientId": "orders-lifecycle-default",
            "resources": [
                {
                    "resourceType": "object",
                    "resourceApiName": "Order",
                    "scopes": ["osdk:object:Order:read", "osdk:object:Order:subscribe"],
                }
            ],
        },
        headers={**headers, "Idempotency-Key": "api-lifecycle-app"},
    )
    app_id = created.json()["application"]["id"]
    listed = client.get("/api/developer-console/osdk-applications", headers=headers)
    fetched = client.get(f"/api/developer-console/osdk-applications/{app_id}", headers=headers)
    updated_resources = client.put(
        f"/api/developer-console/osdk-applications/{app_id}/resources",
        json={
            "resources": [
                {
                    "resourceType": "object",
                    "resourceApiName": "Order",
                    "scopes": ["osdk:object:Order:read"],
                }
            ]
        },
        headers={**headers, "Idempotency-Key": "api-lifecycle-resources"},
    )
    created_client = client.post(
        f"/api/developer-console/osdk-applications/{app_id}/clients",
        json={
            "clientId": "orders-lifecycle-web",
            "redirectUris": ["https://orders.example.test/callback"],
            "allowedScopes": ["osdk:object:Order:read"],
            "accessTokenTtlSeconds": 300,
            "refreshTokenTtlSeconds": 3600,
        },
        headers={**headers, "Idempotency-Key": "api-lifecycle-client"},
    )
    client_row_id = created_client.json()["id"]
    listed_clients = client.get(f"/api/developer-console/osdk-applications/{app_id}/clients", headers=headers)
    updated_client = client.put(
        f"/api/developer-console/osdk-applications/{app_id}/clients/{client_row_id}",
        json={
            "clientId": "orders-lifecycle-web",
            "redirectUris": ["https://orders.example.test/updated-callback"],
            "allowedScopes": ["osdk:object:Order:read"],
            "accessTokenTtlSeconds": 600,
            "refreshTokenTtlSeconds": 7200,
            "status": "active",
        },
        headers={**headers, "Idempotency-Key": "api-lifecycle-client-update"},
    )
    deactivated_client = client.post(
        f"/api/developer-console/osdk-applications/{app_id}/clients/{client_row_id}/deactivate",
        headers={**headers, "Idempotency-Key": "api-lifecycle-client-deactivate"},
    )
    sdk_created = client.post(
        f"/api/developer-console/osdk-applications/{app_id}/sdk-versions",
        json={"language": "typescript", "packageName": "@acme/orders-lifecycle", "requestedBump": "minor"},
        headers={**headers, "Idempotency-Key": "api-lifecycle-sdk"},
    )
    version_id = sdk_created.json()["sdkVersion"]["id"]
    sdk_listed = client.get(f"/api/developer-console/osdk-applications/{app_id}/sdk-versions", headers=headers)
    sdk_fetched = client.get(
        f"/api/developer-console/osdk-applications/{app_id}/sdk-versions/{version_id}", headers=headers
    )
    artifact = client.get(
        f"/api/developer-console/osdk-applications/{app_id}/sdk-versions/{version_id}/artifacts/typescript",
        headers=headers,
    )
    promoted = client.post(
        f"/api/developer-console/osdk-applications/{app_id}/sdk-versions/{version_id}/channels/stable",
        headers={**headers, "Idempotency-Key": "api-lifecycle-promote"},
    )
    channels = client.get(
        f"/api/developer-console/osdk-applications/{app_id}/sdk-release-channels",
        params={"language": "typescript"},
        headers=headers,
    )
    window = client.post(
        f"/api/developer-console/osdk-applications/{app_id}/sdk-compatibility-windows",
        json={
            "fromVersionId": version_id,
            "toVersionId": version_id,
            "supportedUntil": "2099-01-01T00:00:00+00:00",
        },
        headers={**headers, "Idempotency-Key": "api-lifecycle-window"},
    )
    windows = client.get(
        f"/api/developer-console/osdk-applications/{app_id}/sdk-compatibility-windows",
        headers=headers,
    )
    metadata = client.get(
        f"/api/developer-console/osdk-applications/{app_id}/sdk-install-metadata",
        headers=headers,
    )
    download_token = client.post(
        f"/api/developer-console/osdk-applications/{app_id}/sdk-versions/{version_id}/artifacts/typescript/download-token",
        json={"ttlSeconds": 60},
        headers={**headers, "Idempotency-Key": "api-lifecycle-download-token"},
    )
    downloaded = client.get(
        f"/api/developer-console/osdk-release-artifacts/download/{download_token.json()['downloadToken']}",
        headers=headers,
    )

    assert created.status_code == 200
    assert listed.status_code == 200 and listed.json()[0]["application"]["id"] == app_id
    assert fetched.status_code == 200 and fetched.json()["application"]["id"] == app_id
    assert updated_resources.status_code == 200
    assert updated_resources.json()["resources"][0]["scopes"] == ["osdk:object:Order:read"]
    assert created_client.status_code == 200
    assert listed_clients.status_code == 200
    assert updated_client.status_code == 200 and updated_client.json()["access_token_ttl_seconds"] == 600
    assert deactivated_client.status_code == 200 and deactivated_client.json()["status"] == "inactive"
    assert sdk_created.status_code == 200
    assert sdk_listed.status_code == 200 and sdk_listed.json()[0]["id"] == version_id
    assert sdk_fetched.status_code == 200 and sdk_fetched.json()["sdkVersion"]["id"] == version_id
    assert artifact.status_code == 200 and artifact.json()["artifact"]["artifact_kind"] == "typescript"
    assert promoted.status_code == 200 and promoted.json()["channel"] == "stable"
    assert channels.status_code == 200 and channels.json()[0]["channel"] == "stable"
    assert window.status_code == 200 and window.json()["status"] == "active"
    assert windows.status_code == 200 and windows.json()[0]["id"] == window.json()["id"]
    assert metadata.status_code == 200
    assert metadata.json()["sdkVersions"][0]["app_id"] == app_id
    assert metadata.json()["channels"][0]["channel"] == "stable"
    assert download_token.status_code == 200
    assert downloaded.status_code == 200
    assert downloaded.json()["artifact"]["content_hash"] == artifact.json()["artifact"]["content_hash"]


def test_developer_console_and_oauth_routes_normalize_error_paths(monkeypatch, tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "flite"))
    monkeypatch.setattr(api_runtime, "foundry", foundry)
    client = TestClient(api_main.app)
    headers = _admin_headers()
    missing_app = "osdk_app_missing"
    missing_version = "osdk_sdk_version_missing"

    responses = [
        client.get("/api/datasets/missing/dataset/quality-contract/contracts", headers=headers),
        client.post(
            "/api/datasets/missing/dataset/quality-contract/contracts",
            json={"contractKey": "orders-contract", "ownerUserId": "owner-1"},
            headers=headers,
        ),
        client.get(
            "/api/datasets/missing/dataset/quality-contract/contracts/contract-version-missing",
            headers=headers,
        ),
        client.post(
            "/api/datasets/missing/dataset/quality-contract/contracts/contract-version-missing/activate",
            headers=headers,
        ),
        client.post(
            "/api/auth/osdk/oauth/token",
            json={
                "clientId": "missing-client",
                "code": "oauth_code_missing",
                "redirectUri": "https://orders.example.test/oauth/callback",
                "codeVerifier": "verifier",
            },
            headers=headers,
        ),
        client.post("/api/auth/osdk/oauth/refresh", json={"refreshToken": "refresh_missing"}, headers=headers),
        client.post("/api/auth/osdk/oauth/revoke", json={"refreshToken": "refresh_missing"}, headers=headers),
        client.get(f"/api/developer-console/osdk-applications/{missing_app}", headers=headers),
        client.put(
            f"/api/developer-console/osdk-applications/{missing_app}/resources",
            json={"resources": []},
            headers={**headers, "Idempotency-Key": "missing-resources"},
        ),
        client.post(
            f"/api/developer-console/osdk-applications/{missing_app}/clients",
            json={"clientId": "orders-web"},
            headers={**headers, "Idempotency-Key": "missing-client-create"},
        ),
        client.put(
            f"/api/developer-console/osdk-applications/{missing_app}/clients/client-row-missing",
            json={"clientId": "orders-web", "status": "active"},
            headers={**headers, "Idempotency-Key": "missing-client-update"},
        ),
        client.post(
            f"/api/developer-console/osdk-applications/{missing_app}/clients/client-row-missing/deactivate",
            headers={**headers, "Idempotency-Key": "missing-client-deactivate"},
        ),
        client.post(
            f"/api/developer-console/osdk-applications/{missing_app}/sdk-versions",
            json={"language": "typescript"},
            headers={**headers, "Idempotency-Key": "missing-sdk-create"},
        ),
        client.get(
            f"/api/developer-console/osdk-applications/{missing_app}/sdk-versions/{missing_version}",
            headers=headers,
        ),
        client.get(
            f"/api/developer-console/osdk-applications/{missing_app}/sdk-versions/{missing_version}/artifacts/typescript",
            headers=headers,
        ),
        client.post(
            f"/api/developer-console/osdk-applications/{missing_app}/sdk-versions/{missing_version}/channels/stable",
            headers={**headers, "Idempotency-Key": "missing-promote"},
        ),
        client.post(
            f"/api/developer-console/osdk-applications/{missing_app}/sdk-compatibility-windows",
            json={"fromVersionId": missing_version, "toVersionId": missing_version},
            headers={**headers, "Idempotency-Key": "missing-window"},
        ),
        client.post(
            f"/api/developer-console/osdk-applications/{missing_app}/sdk-versions/{missing_version}/artifacts/typescript/download-token",
            json={"ttlSeconds": 60},
            headers={**headers, "Idempotency-Key": "missing-download-token"},
        ),
        client.get("/api/developer-console/osdk-release-artifacts/download/not-a-token", headers=headers),
        client.post(
            "/api/operations/dead-letter-records/missing-record/retry-transform",
            headers={**headers, "Idempotency-Key": "missing-transform-dlq-retry"},
        ),
        client.post("/api/transforms/missing-transform/run", headers=headers),
        client.post(
            "/api/actions/MissingAction/validate",
            json={
                "target": {"objectType": "Order", "objectId": "O-1"},
                "expectedObjectVersion": 1,
                "params": {},
            },
            headers=headers,
        ),
    ]

    assert {response.status_code for response in responses} <= {400, 403, 404}
    assert all("code" in response.json()["detail"] for response in responses)
    assert "oauth_code_missing" not in json.dumps([response.json() for response in responses], sort_keys=True)
    assert "refresh_missing" not in json.dumps([response.json() for response in responses], sort_keys=True)


def test_api_helper_edge_paths_are_token_safe(monkeypatch) -> None:
    bearer_ws = _Headers({"sec-websocket-protocol": "foundry-lite, bearer.token-secret"})
    plain_ws = _Headers({"sec-websocket-protocol": "foundry-lite"})
    manifest = api_main.SourceBatchFileManifest(
        sourceName="orders-batch",
        displayName="Orders Batch",
        files=[{"fileName": "orders.csv", "datasetRef": "raw.orders"}],
    )
    upload = UploadFile(filename="orders.csv", file=BytesIO(b"order_id\nO-1\n"))

    assert api_main._websocket_subprotocol_bearer(bearer_ws) == "Bearer token-secret"
    assert api_main._websocket_subprotocol_bearer(plain_ws) is None
    assert api_main._scope_query(None) == ()
    assert api_main._scope_query("read, write subscribe") == ("read", "write", "subscribe")
    assert list(api_main._sse_json_events([{"event": "snapshot", "watermark": 1}])) == [
        'event: snapshot\ndata: {"event": "snapshot", "watermark": 1}\n\n'
    ]
    assert list(api_main._with_first_event({"event": "first"}, [{"event": "next"}])) == [
        {"event": "first"},
        {"event": "next"},
    ]
    assert api_main._optional_json_form_object(" ", "probeMetadata") is None
    assert api_main._json_form_string_list('["id", ""]', "primaryKey") == ["id"]
    assert [item.file_name for item in api_main._source_batch_uploads(manifest, [upload])] == ["orders.csv"]
    with pytest.raises(ValidationFailed, match="JSON object"):
        api_main._json_form_object("[1]", "securityEnvelope")
    with pytest.raises(ValidationFailed, match="JSON string array"):
        api_main._json_form_string_list('["id", 1]', "primaryKey")
    with pytest.raises(ValidationFailed, match="missing upload"):
        api_main._source_batch_uploads(manifest, [])
    with pytest.raises(ValidationFailed, match="invalid webhook"):
        api_main._webhook_identity_header("bad principal", "X-Principal")


def test_api_webhook_body_and_request_context_helper_edges(monkeypatch) -> None:
    monkeypatch.setattr(api_webhooks, "max_webhook_body_bytes", lambda: 4)
    too_large = _StreamingRequest({"content-length": "5"}, [b"12345"])
    streamed_too_large = _StreamingRequest({}, [b"12", b"345"])
    service_request = _StreamingRequest({"X-Request-ID": "req-webhook"}, [])

    assert api_main._request_content_length(_StreamingRequest({}, [])) is None
    with pytest.raises(ValidationFailed, match="invalid content-length"):
        api_main._request_content_length(_StreamingRequest({"content-length": "bad"}, []))
    with pytest.raises(ValidationFailed, match="invalid content-length"):
        api_main._request_content_length(_StreamingRequest({"content-length": "-1"}, []))
    with pytest.raises(ValidationFailed, match="exceeds configured size"):
        asyncio.run(api_main._bounded_webhook_body(too_large))
    with pytest.raises(ValidationFailed, match="exceeds configured size"):
        asyncio.run(api_main._bounded_webhook_body(streamed_too_large))
    webhook_ctx = api_main._webhook_request_context(
        service_request,
        service_principal="orders-hook",
        service_tenant_id="tenant-hook",
    )
    with pytest.raises(ValidationFailed, match="requires tenant and principal"):
        api_main._webhook_request_context(service_request, service_principal="orders-hook", service_tenant_id=None)

    assert webhook_ctx.require_service_principal_signature is True
    assert webhook_ctx.ctx.tenant_id == "tenant-hook"
    assert webhook_ctx.ctx.actor_user_id.endswith("orders-hook")


def _admin_headers() -> dict[str, str]:
    return {
        "X-Tenant-ID": "tenant-demo",
        "X-User-ID": "api-admin",
        "X-Roles": "admin,data_engineer,ops_manager,finance",
    }


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class _Headers:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


class _StreamingRequest:
    def __init__(self, headers: dict[str, str], chunks: list[bytes]) -> None:
        self.headers = headers
        self._chunks = chunks
        self.state = type("State", (), {"request_id": headers.get("X-Request-ID", "req-test")})()

    async def stream(self):
        for chunk in self._chunks:
            yield chunk
