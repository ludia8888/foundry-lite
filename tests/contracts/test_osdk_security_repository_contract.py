"""Contract proof for OSDK credential and MCP-discovery security state."""

from foundry_lite.application.ports import (
    OsdkApplicationClientRecord,
    OsdkApplicationRecord,
)
from foundry_lite.application.ports.osdk_security_repository import (
    OsdkClientSecretVersionRecord,
    OsdkMcpToolActivationRecord,
    OsdkSecurityRepository,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyOsdkApplicationRepository
from sqlalchemy import create_engine


def test_osdk_security_repository_tracks_one_current_secret_and_scoped_activation() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    repository: OsdkSecurityRepository = SqlAlchemyOsdkApplicationRepository(engine)
    with engine.begin() as conn:
        repository_as_apps = SqlAlchemyOsdkApplicationRepository(engine)
        repository_as_apps.insert_application_or_get_existing(transaction=conn, record=_application())
        repository_as_apps.insert_client_or_get_existing(transaction=conn, record=_client())
        first = repository.activate_client_secret(transaction=conn, record=_secret("secret-1", "version-1"))
        second = repository.activate_client_secret(transaction=conn, record=_secret("secret-2", "version-2"))
        current = repository.current_client_secret(transaction=conn, tenant_id="tenant-a", client_row_id="client-row-1")
        versions = repository.client_secret_versions(
            transaction=conn, tenant_id="tenant-a", client_row_id="client-row-1"
        )
        is_new = repository.activate_mcp_tool(transaction=conn, record=_activation())
        is_replayed = repository.activate_mcp_tool(transaction=conn, record=_activation())
        activations = repository.mcp_tool_activations(
            transaction=conn,
            tenant_id="tenant-a",
            app_id="app-1",
            session_id="session-1",
            client_id="client-a",
            actor_user_id="user-a",
        )

    assert first["status"] == second["status"] == "active"
    assert current is not None and current["id"] == "secret-2"
    assert [row["status"] for row in versions] == ["active", "rotated"]
    assert is_new is True and is_replayed is False
    assert [row["tool_id"] for row in activations] == ["platform.docs.search"]


def _application() -> OsdkApplicationRecord:
    return OsdkApplicationRecord(
        application_id="app-1",
        tenant_id="tenant-a",
        app_api_name="SecurityApp",
        display_name="Security App",
        status="active",
        owner_user_id="user-a",
        created_idempotency_key="create-security-app",
        created_at="2026-08-04T00:00:00+00:00",
        updated_at="2026-08-04T00:00:00+00:00",
    )


def _client() -> OsdkApplicationClientRecord:
    return OsdkApplicationClientRecord(
        client_row_id="client-row-1",
        tenant_id="tenant-a",
        app_id="app-1",
        client_id="client-a",
        status="active",
        created_at="2026-08-04T00:00:00+00:00",
    )


def _secret(secret_id: str, version: str) -> OsdkClientSecretVersionRecord:
    return OsdkClientSecretVersionRecord(
        secret_id=secret_id,
        tenant_id="tenant-a",
        app_id="app-1",
        client_row_id="client-row-1",
        client_id="client-a",
        vault_secret_name="osdk_client_credentials/tenant-a/client-a",
        vault_secret_version=version,
        status="active",
        created_by_user_id="user-a",
        rotation_reason="contract test",
        created_at=f"2026-08-04T00:00:0{secret_id[-1]}+00:00",
    )


def _activation() -> OsdkMcpToolActivationRecord:
    return OsdkMcpToolActivationRecord(
        activation_id="activation-1",
        tenant_id="tenant-a",
        app_id="app-1",
        session_id="session-1",
        client_id="client-a",
        actor_user_id="user-a",
        tool_id="platform.docs.search",
        query_hash="sha256:query",
        activated_at="2026-08-04T00:01:00+00:00",
    )
