"""Official MCP client -> API process -> PostgreSQL Ontology MCP proof."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from threading import Event
from typing import cast
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen
from uuid import uuid4

import httpx2
import jwt
import pytest
import yaml
from mcp import ClientSession
from mcp.client.auth.extensions.client_credentials import ClientCredentialsOAuthProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


@pytest.fixture
def ontology_mcp_live_database() -> Iterator[tuple[Engine, str]]:
    admin_url = os.getenv(
        "FOUNDRY_LITE_ONTOLOGY_MCP_LIVE_ADMIN_DB_URL",
        "postgresql+psycopg://foundry_lite:foundry_lite@127.0.0.1:15433/postgres",
    )
    endpoint = urlsplit(admin_url)
    _wait_port(endpoint.hostname or "127.0.0.1", endpoint.port or 15433)
    database_name = f"foundry_lite_ontology_mcp_{uuid4().hex[:12]}"
    admin = create_engine(admin_url, future=True, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    db_url = admin_url.rsplit("/", 1)[0] + f"/{database_name}"
    engine = create_engine(db_url, future=True)
    try:
        yield engine, db_url
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin.dispose()


def test_official_mcp_client_uses_postgres_application_scope_and_action_approval(
    ontology_mcp_live_database: tuple[Engine, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _engine, db_url = ontology_mcp_live_database
    runtime_root = tmp_path / "runtime"
    port = _free_port()
    public_base_url = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("FOUNDRY_LITE_DB_URL", db_url)
    monkeypatch.setenv("FOUNDRY_LITE_STORAGE_ROOT", str(runtime_root))
    monkeypatch.setenv("FOUNDRY_LITE_OAUTH_ISSUER", public_base_url)
    from foundry_lite.application.foundry import FoundryLite
    from foundry_lite.infrastructure.local_runtime import create_runtime_core_dependencies

    foundry = FoundryLite(dependencies=create_runtime_core_dependencies(db_url=db_url, storage_root=runtime_root))
    object_version = _prepare_catalog(foundry, tmp_path)
    application_id, client_id, client_secret, scopes, auth_environment = _create_mcp_application(
        foundry, public_base_url
    )
    process = _start_api(db_url, runtime_root, port, auth_environment)
    try:
        _wait_api(port, process)
        evidence = asyncio.run(
            _exercise_official_client(
                public_base_url,
                f"{public_base_url}/mcp/ontology/{application_id}",
                client_id,
                client_secret,
                scopes,
                object_version,
            )
        )
    finally:
        _stop_process(process)

    assert evidence["serverName"] == "foundry-lite-ontology-mcp"
    assert evidence["toolNames"] == [
        "action.ApproveOrderLive.apply",
        "action.ApproveOrderLive.plan",
        "action_approval.get",
        "action_run.get",
        "object.Order.get",
        "object.Order.links",
        "object.Order.search",
        "object.Order.searchAround",
        # The list is exact on purpose: the live official client must see the same governed
        # object-read surface as the in-process contract, including link traversal and unified
        # search. A tool appearing without anyone deciding it should remains a failure.
        "object.Order.unifiedSearch",
    ]
    assert evidence["objectId"] == "ORDER-1"
    assert evidence["approvalStatus"] == "approval_required"
    assert str(evidence["planHash"]).startswith("sha256:")
    assert evidence["authMode"] == "official_client_credentials_discovery"
    assert evidence["audience"] == f"{public_base_url}/mcp/ontology/{application_id}"
    assert evidence["issuer"] == public_base_url
    assert evidence["grantedScopes"] == sorted(scopes)


def _create_mcp_application(
    foundry: object,
    public_base_url: str,
) -> tuple[str, str, str, tuple[str, ...], dict[str, str]]:
    from foundry_lite.domain.context import demo_admin_context

    ctx = demo_admin_context()
    scopes = (
        "osdk:object:Order:read",
        "osdk:action:ApproveOrderLive:validate",
        "osdk:action:ApproveOrderLive:execute",
    )
    console = foundry.developer_console  # type: ignore[attr-defined]
    created = console.create_osdk_application(
        app_api_name="OfficialMcpPostgresLive",
        display_name="Official MCP PostgreSQL Live",
        client_id="official-mcp-live-client",
        resources=[
            {"resourceType": "object", "resourceApiName": "Order", "scopes": [scopes[0]]},
            {"resourceType": "action", "resourceApiName": "ApproveOrderLive", "scopes": list(scopes[1:])},
        ],
        idempotency_key="official-mcp-live-application",
        ctx=ctx,
    )
    application = cast(Mapping[str, object], created["application"])
    application_id = str(application["id"])
    console.configure_ontology_mcp_server(
        application_id,
        status="enabled",
        description_markdown="Official SDK PostgreSQL live proof.",
        allowed_origins=(),
        idempotency_key="official-mcp-live-server",
        ctx=ctx,
    )
    client_id = "official-mcp-live-service"
    machine_client = console.create_osdk_application_client(
        application_id,
        client_id=client_id,
        redirect_uris=(),
        allowed_scopes=scopes,
        access_token_ttl_seconds=120,
        idempotency_key="official-mcp-live-service-client",
        ctx=ctx,
    )
    rotated = console.rotate_osdk_application_client_secret(
        application_id,
        str(machine_client["id"]),
        reason="Official strict Bearer MCP live proof",
        idempotency_key="official-mcp-live-service-secret",
        ctx=ctx,
    )
    issuer = foundry._services.osdk_oauth_client_credentials.oauth_token_issuer  # type: ignore[attr-defined]
    auth_environment = {
        "FOUNDRY_LITE_AUTH_PROFILE": "jwt",
        "FOUNDRY_LITE_OAUTH_ISSUER": public_base_url,
        "FOUNDRY_LITE_OIDC_ISSUER": public_base_url,
        "FOUNDRY_LITE_OIDC_AUDIENCE": issuer.audience,
        "FOUNDRY_LITE_OIDC_JWKS_JSON": json.dumps(issuer.public_jwks()),
    }
    return application_id, client_id, str(rotated["clientSecret"]), scopes, auth_environment


class _MemoryTokenStorage:
    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client_info = client_info


async def _exercise_official_client(
    api_url: str,
    mcp_url: str,
    client_id: str,
    client_secret: str,
    scopes: tuple[str, ...],
    object_version: int,
) -> dict[str, object]:
    storage = _MemoryTokenStorage()
    provider = ClientCredentialsOAuthProvider(
        server_url=mcp_url,
        storage=storage,
        client_id=client_id,
        client_secret=client_secret,
    )
    request_trace: list[dict[str, object]] = []

    async def record_request(request: httpx2.Request) -> None:
        form_keys: list[str] = []
        if request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
            form_keys = sorted(parse_qs(request.content.decode("utf-8"), keep_blank_values=True))
        request_trace.append(
            {
                "method": request.method,
                "path": request.url.path,
                "authScheme": request.headers.get("authorization", "").partition(" ")[0],
                "formKeys": form_keys,
            }
        )

    async with httpx2.AsyncClient(
        base_url=api_url,
        headers={"X-Request-ID": "official-mcp-client-live"},
        auth=provider,
        event_hooks={"request": [record_request]},
        timeout=15,
        trust_env=False,
    ) as http_client:
        async with streamable_http_client(mcp_url, http_client=http_client) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                object_result = await session.call_tool("object.Order.get", {"objectId": "ORDER-1"})
                approval_result = await session.call_tool(
                    "action.ApproveOrderLive.apply",
                    {
                        "objectType": "Order",
                        "objectId": "ORDER-1",
                        "expectedObjectVersion": object_version,
                        "params": {"reason": "Official external MCP client requested approval"},
                    },
                )
    assert storage.tokens is not None
    claims = cast(dict[str, object], jwt.decode(storage.tokens.access_token, options={"verify_signature": False}))
    token_requests = [item for item in request_trace if item["path"] == "/api/auth/osdk/oauth/token"]
    assert token_requests == [
        {
            "method": "POST",
            "path": "/api/auth/osdk/oauth/token",
            "authScheme": "Basic",
            "formKeys": ["grant_type", "resource", "scope"],
        }
    ]
    assert not any("tenant_id" in cast(list[str], item["formKeys"]) for item in request_trace)
    assert any(item["path"].startswith("/.well-known/oauth-protected-resource/") for item in request_trace)
    assert any(item["path"] == "/.well-known/oauth-authorization-server" for item in request_trace)
    assert any(item["path"] == urlsplit(mcp_url).path and not item["authScheme"] for item in request_trace)
    object_payload = cast(dict[str, object], object_result.structured_content)
    approval_payload = cast(dict[str, object], approval_result.structured_content)
    return {
        "serverName": initialized.server_info.name,
        "toolNames": sorted(tool.name for tool in listed.tools),
        "objectId": object_payload["objectId"],
        "approvalStatus": approval_payload["status"],
        "planHash": approval_payload["planHash"],
        "authMode": "official_client_credentials_discovery",
        "audience": claims["aud"],
        "issuer": claims["iss"],
        "grantedScopes": sorted(str(claims["scope"]).split()),
    }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_port(host: str, port: int) -> None:
    for _ in range(300):
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return
        except OSError:
            Event().wait(0.1)
    raise AssertionError(f"timed out waiting for {host}:{port}")


def _prepare_catalog(foundry: object, tmp_path: Path) -> int:
    from foundry_lite.domain.context import demo_admin_context

    ctx = demo_admin_context()
    csv_path = tmp_path / "ontology-mcp-live-orders.csv"
    csv_path.write_text("order_id,status,operator_note\nORDER-1,PENDING,\n", encoding="utf-8")
    foundry.datasets.ensure("clean.ontology_mcp_live_orders", ctx=ctx, primary_key=["order_id"])  # type: ignore[attr-defined]
    foundry.datasets.upload_csv("clean.ontology_mcp_live_orders", str(csv_path), ctx=ctx)  # type: ignore[attr-defined]
    foundry.ontology.apply_text(yaml.safe_dump(_ontology_definition(), sort_keys=False), ctx=ctx)  # type: ignore[attr-defined]
    foundry.objects.reindex("Order", ctx=ctx)  # type: ignore[attr-defined]
    current = foundry.objects.get("Order", "ORDER-1", ctx=ctx)  # type: ignore[attr-defined]
    return int(current["objectVersion"])


def _ontology_definition() -> dict[str, object]:
    return {
        "objectTypes": [
            {
                "apiName": "Order",
                "primaryKey": "orderId",
                "backing": {
                    "dataset": "clean.ontology_mcp_live_orders",
                    "mode": "snapshot",
                    "primaryKeyColumns": ["order_id"],
                },
                "properties": [
                    {"apiName": "orderId", "column": "order_id", "type": "string", "nullable": False},
                    {
                        "apiName": "status",
                        "column": "status",
                        "type": "string",
                        "editable": True,
                        "editPolicy": "edit_wins",
                    },
                    {
                        "apiName": "operatorNote",
                        "column": "operator_note",
                        "type": "string",
                        "editable": True,
                        "editPolicy": "edit_wins",
                    },
                ],
            }
        ],
        "actionTypes": [
            {
                "apiName": "ApproveOrderLive",
                "contractVersion": 3,
                "target": "Order",
                "riskLevel": "high",
                "agentExecutionPolicy": "approval_required",
                "agentToolDescription": "Plan an order approval and request mandatory human review.",
                "permissions": {"allowedRoles": ["admin", "ops_manager"]},
                "parameters": [{"apiName": "reason", "type": "string", "required": True}],
                "rules": [
                    {
                        "kind": "modifyObject",
                        "ruleId": "approve-order",
                        "objectType": "Order",
                        "target": {"kind": "parameter", "parameter": "__target__"},
                        "assignments": [
                            {"property": "status", "value": {"kind": "literal", "value": "APPROVED"}},
                            {"property": "operatorNote", "value": {"kind": "parameter", "parameter": "reason"}},
                        ],
                    }
                ],
            }
        ],
    }


def _start_api(
    db_url: str,
    runtime_root: Path,
    port: int,
    auth_environment: Mapping[str, str],
) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        **auth_environment,
        "FOUNDRY_LITE_DB_URL": db_url,
        "FOUNDRY_LITE_HOME": str(runtime_root),
        "FOUNDRY_LITE_OTEL_DISABLED": "1",
        "PYTHONPATH": ".:libs:apps/api",
    }
    return subprocess.Popen(  # nosec B603 - fixed local integration-test argv.
        [sys.executable, "-m", "uvicorn", "foundry_lite_api.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=Path(__file__).parents[2],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _wait_api(port: int, process: subprocess.Popen[bytes]) -> None:
    for _ in range(200):
        if process.poll() is not None:
            raise AssertionError(_process_error(process))
        try:
            with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=0.25) as response:  # nosec B310
                if response.status == 200:
                    return
        except OSError:
            Event().wait(0.1)
    raise AssertionError("Ontology MCP API process did not become ready")


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _process_error(process: subprocess.Popen[bytes]) -> str:
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    return f"Ontology MCP API process exited early: {stderr}"
