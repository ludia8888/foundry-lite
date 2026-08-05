"""Official MCP client -> API process -> PostgreSQL Ontology MCP proof."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from threading import Event
from typing import cast
from urllib.parse import urlsplit
from urllib.request import urlopen
from uuid import uuid4

import httpx2
import pytest
import yaml
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
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
    monkeypatch.setenv("FOUNDRY_LITE_DB_URL", db_url)
    monkeypatch.setenv("FOUNDRY_LITE_STORAGE_ROOT", str(tmp_path / "runtime"))
    from foundry_lite.application.foundry import FoundryLite
    from foundry_lite.infrastructure.local_runtime import create_runtime_core_dependencies

    foundry = FoundryLite(dependencies=create_runtime_core_dependencies(db_url=db_url, storage_root=tmp_path / "seed"))
    object_version = _prepare_catalog(foundry, tmp_path)
    application_id, headers = _create_mcp_application(foundry)
    port = _free_port()
    process = _start_api(db_url, tmp_path, port)
    try:
        _wait_api(port, process)
        evidence = asyncio.run(
            _exercise_official_client(
                f"http://127.0.0.1:{port}/mcp/ontology/{application_id}",
                headers,
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
        "object.Order.search",
    ]
    assert evidence["objectId"] == "ORDER-1"
    assert evidence["approvalStatus"] == "approval_required"
    assert str(evidence["planHash"]).startswith("sha256:")


def _create_mcp_application(foundry: object) -> tuple[str, dict[str, str]]:
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
    return application_id, {
        "X-Tenant-ID": ctx.tenant_id,
        "X-User-ID": ctx.actor_user_id,
        "X-Roles": ",".join(ctx.roles),
        "X-Foundry-Lite-App-ID": application_id,
        "X-Foundry-Lite-Client-ID": "official-mcp-live-client",
        "X-Foundry-Lite-Scopes": " ".join(scopes),
        "X-Request-ID": "official-mcp-client-live",
    }


async def _exercise_official_client(url: str, headers: Mapping[str, str], object_version: int) -> dict[str, object]:
    async with httpx2.AsyncClient(headers=dict(headers), timeout=15, trust_env=False) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read_stream, write_stream):
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
    object_payload = cast(dict[str, object], object_result.structured_content)
    approval_payload = cast(dict[str, object], approval_result.structured_content)
    return {
        "serverName": initialized.server_info.name,
        "toolNames": sorted(tool.name for tool in listed.tools),
        "objectId": object_payload["objectId"],
        "approvalStatus": approval_payload["status"],
        "planHash": approval_payload["planHash"],
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
                "permissions": {"allowedRoles": ["admin"]},
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


def _start_api(db_url: str, tmp_path: Path, port: int) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "FOUNDRY_LITE_DB_URL": db_url,
        "FOUNDRY_LITE_HOME": str(tmp_path / "api"),
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
