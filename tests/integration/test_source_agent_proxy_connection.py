from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from typing import cast
from urllib.parse import urlsplit

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.infrastructure.adapters import RestPullConnectorAdapter
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite.infrastructure.secrets import EnvSecretProvider
from foundry_lite_worker.source_agent_proxy import SourceAgentProxyConfig, SourceAgentProxyServer
from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer

from tests.contracts.test_rest_connector_adapter_contract import MockRestServer


def test_managed_rest_source_uses_live_agent_tunnel_and_exposes_egress_history(tmp_path) -> None:
    with MockRestServer() as source_server:
        destination = _destination(source_server.base_url)
        with _agent_proxy(destination) as proxy_url:
            foundry = _foundry(tmp_path)
            ctx = demo_admin_context()
            _create_connector(foundry, source_server.base_url, ctx)
            _create_agent_route(foundry, proxy_url, destination, ctx, is_online=True)
            source = _create_managed_source(foundry, ctx)

            result = foundry.sources.test_connection(
                "agent_erp",
                expected_config_fingerprint=cast(str, source["configFingerprint"]),
                idempotency_key="test-agent-erp",
                ctx=ctx,
            )
            egress = foundry.sources.list_egress_attempts("agent_erp", ctx=ctx)
            sync_run = foundry.sources.start_managed_sync_run(
                "agent_erp_orders",
                idempotency_key="sync-agent-erp-orders",
                ctx=ctx,
            )
            metadata = _committed_transaction_metadata(
                foundry,
                ctx.tenant_id,
                cast(str, sync_run["datasetVersionId"]),
            )
            preview = foundry.datasets.preview("raw.agent_erp_orders", ctx=ctx)

    checks = cast(Mapping[str, object], result["checks"])
    assert result["status"] == "succeeded"
    assert checks["summary"] == {"passed": 5, "total": 5}
    assert egress[0]["status"] == "succeeded"
    assert egress[0]["origin"] == "agent-proxy"
    assert egress[0]["networkType"] == "agent_proxy"
    assert egress[0]["responseFlags"] == "NONE"
    assert cast(Mapping[str, object], egress[0]["networkResources"])["agentId"] == "agent_erp_runtime"
    assert sync_run["status"] == "succeeded"
    run_network = cast(Mapping[str, object], sync_run["networkEvidence"])
    assert run_network["origin"] == "agent-proxy"
    assert run_network["responseFlags"] == "NONE"
    assert run_network["source"] == "dataset_transaction.metadata"
    assert cast(Mapping[str, object], run_network["networkResources"])["agentId"] == "agent_erp_runtime"
    assert preview[0]["order_id"] == "O-1001"
    sync_network = cast(Mapping[str, object], metadata["connectorNetworkEvidence"])
    assert sync_network["origin"] == "agent-proxy"
    assert sync_network["responseFlags"] == "NONE"
    assert cast(Mapping[str, object], sync_network["networkResources"])["agentId"] == "agent_erp_runtime"
    assert len(source_server.requests) == 2


def test_agent_proxy_source_fails_closed_when_agent_has_no_heartbeat(tmp_path) -> None:
    with MockRestServer() as source_server:
        destination = _destination(source_server.base_url)
        foundry = _foundry(tmp_path)
        ctx = demo_admin_context()
        _create_connector(foundry, source_server.base_url, ctx)
        _create_agent_route(foundry, "http://127.0.0.1:8765", destination, ctx, is_online=False)
        source = _create_managed_source(foundry, ctx)

        result = foundry.sources.test_connection(
            "agent_erp",
            expected_config_fingerprint=cast(str, source["configFingerprint"]),
            idempotency_key="test-offline-agent-erp",
            ctx=ctx,
        )
        egress = foundry.sources.list_egress_attempts("agent_erp", ctx=ctx)
        sync_run = foundry.sources.start_managed_sync_run(
            "agent_erp_orders",
            idempotency_key="sync-offline-agent-erp-orders",
            ctx=ctx,
        )

    assert result["status"] == "failed"
    assert egress[0]["responseFlags"] == "AGENT_OFFLINE"
    assert egress[0]["status"] == "failed"
    assert sync_run["status"] == "failed"
    assert source_server.requests == []


def test_managed_postgres_source_diagnostic_and_sync_use_live_agent_tunnel(tmp_path) -> None:
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as postgres:
        database_url = _postgres_url(postgres)
        _seed_postgres_orders(database_url)
        destination = _destination(database_url)
        with _agent_proxy(destination) as proxy_url:
            foundry = _foundry(tmp_path)
            ctx = demo_admin_context()
            source = _create_managed_database_source(foundry, database_url, proxy_url, destination, ctx)

            diagnostic = foundry.sources.test_connection(
                "agent_postgres",
                expected_config_fingerprint=cast(str, source["configFingerprint"]),
                idempotency_key="test-agent-postgres",
                ctx=ctx,
            )
            source_config = cast(Mapping[str, object], source["configSummary"])
            exploration = foundry.sources.explore_source(
                source_name="agent_postgres",
                source_type="postgres_jdbc",
                request={
                    "databaseUrlSecretRef": source_config["databaseUrlSecretRef"],
                    "tableName": "orders",
                    "sampleLimit": 5,
                },
                ctx=ctx,
            )
            run = foundry.sources.start_managed_sync_run(
                "agent_postgres_orders",
                idempotency_key="sync-agent-postgres-orders",
                ctx=ctx,
            )
            preview = foundry.datasets.preview("raw.agent_postgres_orders", ctx=ctx)

    probe = cast(Mapping[str, object], cast(Mapping[str, object], diagnostic["checks"])["probe"])
    diagnostic_network = cast(Mapping[str, object], probe["networkEvidence"])
    exploration_result = cast(Mapping[str, object], exploration["resultSummary"])
    exploration_network = cast(Mapping[str, object], exploration_result["networkEvidence"])
    run_network = cast(Mapping[str, object], run["networkEvidence"])
    assert diagnostic["status"] == "succeeded"
    assert diagnostic_network["origin"] == "agent-proxy"
    assert diagnostic_network["responseFlags"] == "NONE"
    assert cast(int, diagnostic_network["bytesSent"]) > 0
    assert cast(int, diagnostic_network["bytesReceived"]) > 0
    assert cast(list[object], exploration_result["sample"]) == [{"id": 1, "status": "ready", "amount": 1200}]
    assert exploration_network["origin"] == "agent-proxy"
    assert exploration_network["responseFlags"] == "NONE"
    assert run["status"] == "succeeded"
    assert run_network["origin"] == "agent-proxy"
    assert run_network["responseFlags"] == "NONE"
    assert cast(Mapping[str, object], run_network["networkResources"])["agentId"] == "agent_erp_runtime"
    assert len(preview) == 1
    assert preview[0]["id"] == 1
    assert preview[0]["status"] == "ready"
    assert preview[0]["amount"] == 1200
    assert preview[0]["branch"] == "main"
    assert str(preview[0]["version"]).startswith("dsv_")


def _foundry(tmp_path) -> FoundryLite:
    secret_provider = EnvSecretProvider(environ={"FOUNDRY_LITE_SECRET_ERP_TOKEN": "token-v1"})
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "flite")
    return FoundryLite(
        dependencies=replace(
            dependencies,
            connector_adapter=RestPullConnectorAdapter(secret_provider=secret_provider),
            secret_provider=secret_provider,
        )
    )


def _create_connector(foundry: FoundryLite, base_url: str, ctx) -> None:
    foundry.connectors.create_connection(
        connector_name="agent_erp",
        display_name="Agent ERP",
        base_url=base_url,
        auth={"mode": "bearer", "tokenSecretRef": "erp-token"},
        idempotency_key="create-agent-erp",
        allow_private_network=True,
        ctx=ctx,
    )
    foundry.connectors.upsert_resource(
        "agent_erp",
        "orders",
        dataset_ref="raw.agent_erp_orders",
        resource_path="/orders",
        pagination={"strategy": "cursor"},
        primary_key=["order_id"],
        idempotency_key="upsert-agent-erp-orders",
        ctx=ctx,
    )


def _create_agent_route(foundry: FoundryLite, proxy_url: str, destination: str, ctx, *, is_online: bool) -> None:
    foundry.sources.register_agent(
        agent_id="agent_erp_runtime",
        display_name="Agent ERP runtime",
        mode="agent_proxy",
        capabilities={"transparentTcpTunnel": True, "httpConnect": True},
        network_summary={"proxyUrl": proxy_url, "heartbeatMode": "daemon"},
        idempotency_key="register-agent-erp-runtime",
        ctx=ctx,
    )
    if is_online:
        foundry.sources.heartbeat_agent("agent_erp_runtime", ctx=ctx)
    foundry.sources.create_network_policy(
        policy_name="agent_erp_policy",
        display_name="Agent ERP policy",
        mode="agent_proxy",
        agent_id="agent_erp_runtime",
        allowed_hosts=[destination],
        idempotency_key="create-agent-erp-policy",
        ctx=ctx,
    )


def _create_managed_source(foundry: FoundryLite, ctx) -> dict[str, object]:
    foundry.sources.create_managed_sync(
        sync_name="agent_erp_orders",
        source_name="agent_erp",
        display_name="Agent ERP orders",
        source_type="rest_api",
        capability="batch",
        mode="APPEND",
        target_dataset_ref="raw.agent_erp_orders",
        schedule={"mode": "manual"},
        config_summary={
            "connectionMode": "agent_proxy",
            "networkPolicyName": "agent_erp_policy",
            "agentId": "agent_erp_runtime",
            "connectorName": "agent_erp",
            "resourceName": "orders",
        },
        idempotency_key="create-agent-erp-managed-source",
        ctx=ctx,
    )
    return foundry.sources.get_source("agent_erp", ctx=ctx)


def _create_managed_database_source(
    foundry: FoundryLite,
    database_url: str,
    proxy_url: str,
    destination: str,
    ctx,
) -> dict[str, object]:
    credential = foundry.sources.create_credential(
        credential_name="agent_postgres_credential",
        display_name="Agent PostgreSQL credential",
        kind="postgres_jdbc",
        auth_scheme="database_url",
        secret_value=database_url,
        idempotency_key="create-agent-postgres-credential",
        ctx=ctx,
    )
    _create_agent_route(foundry, proxy_url, destination, ctx, is_online=True)
    secret_ref = cast(Mapping[str, object], credential["secretRef"])
    foundry.sources.create_managed_sync(
        sync_name="agent_postgres_orders",
        source_name="agent_postgres",
        display_name="Agent PostgreSQL orders",
        source_type="postgres_jdbc",
        capability="batch",
        mode="SNAPSHOT",
        target_dataset_ref="raw.agent_postgres_orders",
        schedule={"mode": "manual"},
        config_summary={
            "connectionMode": "agent_proxy",
            "networkPolicyName": "agent_erp_policy",
            "agentId": "agent_erp_runtime",
            "databaseUrlSecretRef": secret_ref["name"],
            "tableName": "orders",
        },
        idempotency_key="create-agent-postgres-managed-source",
        ctx=ctx,
    )
    return foundry.sources.get_source("agent_postgres", ctx=ctx)


def _destination(base_url: str) -> str:
    split = urlsplit(base_url)
    return f"{split.hostname}:{split.port}"


def _postgres_url(container: PostgresContainer) -> str:
    value = container.get_connection_url()
    return value.replace("postgresql://", "postgresql+psycopg://", 1)


def _seed_postgres_orders(database_url: str) -> None:
    engine = create_engine(database_url, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT, amount INTEGER)"))
            conn.execute(text("INSERT INTO orders (id, status, amount) VALUES (1, 'ready', 1200)"))
    finally:
        engine.dispose()


def _committed_transaction_metadata(
    foundry: FoundryLite,
    tenant_id: str,
    version_id: str,
) -> Mapping[str, object]:
    with foundry.engine.begin() as conn:
        row = foundry.dataset_transaction_repository.committed_transaction_by_version(
            transaction=conn,
            tenant_id=tenant_id,
            committed_version_id=version_id,
        )
    assert row is not None
    return row["metadata"]


@contextmanager
def _agent_proxy(destination: str) -> Iterator[str]:
    config = SourceAgentProxyConfig(
        agent_id="agent_erp_runtime",
        display_name="Agent ERP runtime",
        listen_host="127.0.0.1",
        listen_port=0,
        control_plane_url="http://127.0.0.1:8000",
        allowed_destinations=(destination,),
    )
    server = SourceAgentProxyServer(config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
