from __future__ import annotations

from typing import cast

from foundry_lite.application.ports import (
    OsdkApplicationClientRecord,
    OsdkApplicationRecord,
    OsdkApplicationRepository,
    OsdkApplicationResourceRecord,
    OsdkDeveloperConsoleIdempotencyRecord,
    OsdkMcpServerRecord,
    OsdkMcpSessionRecord,
    OsdkReleaseArtifactDownloadTokenRecord,
    OsdkReleaseArtifactRecord,
    OsdkSdkCompatibilityWindowRecord,
    OsdkSdkReleaseChannelRecord,
    OsdkSdkVersionRecord,
)
from foundry_lite.application.ports.osdk_security_repository import (
    OsdkClientSecretVersionRecord,
    OsdkMcpToolActivationRecord,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyOsdkApplicationRepository
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def test_osdk_application_repository_contract_round_trips_application_scope_and_release_rows() -> None:
    engine = _engine()
    repository: OsdkApplicationRepository = SqlAlchemyOsdkApplicationRepository(engine)

    with engine.begin() as conn:
        assert repository.insert_application_or_get_existing(transaction=conn, record=_application()) is None
        duplicate = repository.insert_application_or_get_existing(transaction=conn, record=_application())
        assert repository.insert_client_or_get_existing(transaction=conn, record=_client()) is None
        client_duplicate = repository.insert_client_or_get_existing(transaction=conn, record=_client())
        repository.replace_resources(transaction=conn, tenant_id="tenant-a", app_id="app-1", resources=(_resource(),))
        mcp_server = repository.upsert_mcp_server(transaction=conn, record=_mcp_server("enabled"))
        updated_mcp_server = repository.upsert_mcp_server(transaction=conn, record=_mcp_server("disabled"))
        assert repository.insert_mcp_session_or_get_existing(transaction=conn, record=_mcp_session()) is None
        session_duplicate = repository.insert_mcp_session_or_get_existing(transaction=conn, record=_mcp_session())
        first_event = repository.append_mcp_session_event(
            transaction=conn,
            tenant_id="tenant-a",
            session_id="ontology-mcp-session-1",
            event_type="session.ready",
            payload={"applicationId": "app-1"},
            created_at="2026-07-01T00:09:00+00:00",
        )
        second_event = repository.append_mcp_session_event(
            transaction=conn,
            tenant_id="tenant-a",
            session_id="ontology-mcp-session-1",
            event_type="tool.completed",
            payload={"toolName": "object.Order.get"},
            created_at="2026-07-01T00:10:00+00:00",
        )
        session_events = repository.mcp_session_events_after(
            transaction=conn, tenant_id="tenant-a", session_id="ontology-mcp-session-1", after_sequence=1
        )
        terminated_session = repository.terminate_mcp_session(
            transaction=conn,
            tenant_id="tenant-a",
            session_id="ontology-mcp-session-1",
            terminated_at="2026-07-01T00:11:00+00:00",
        )
        assert repository.activate_mcp_tool(transaction=conn, record=_mcp_tool_activation()) is True
        assert repository.activate_mcp_tool(transaction=conn, record=_mcp_tool_activation()) is False
        mcp_tool_activations = repository.mcp_tool_activations(
            transaction=conn,
            tenant_id="tenant-a",
            app_id="app-1",
            session_id="ontology-mcp-session-1",
            client_id="orders-web",
            actor_user_id="user-a",
        )
        other_actor_activations = repository.mcp_tool_activations(
            transaction=conn,
            tenant_id="tenant-a",
            app_id="app-1",
            session_id="ontology-mcp-session-1",
            client_id="orders-web",
            actor_user_id="user-b",
        )
        first_client_secret = repository.activate_client_secret(
            transaction=conn,
            record=_client_secret("secret-1", "sha256:first", "2026-07-01T00:09:40+00:00"),
        )
        second_client_secret = repository.activate_client_secret(
            transaction=conn,
            record=_client_secret("secret-2", "sha256:second", "2026-07-01T00:09:50+00:00"),
        )
        repository.mark_client_secret_used(
            transaction=conn,
            tenant_id="tenant-a",
            secret_id="secret-2",
            used_at="2026-07-01T00:09:55+00:00",
        )
        current_client_secret = repository.current_client_secret(
            transaction=conn,
            tenant_id="tenant-a",
            client_row_id="client-row-1",
        )
        client_secret_versions = repository.client_secret_versions(
            transaction=conn,
            tenant_id="tenant-a",
            client_row_id="client-row-1",
        )
        revoked_client_secret = repository.revoke_current_client_secret(
            transaction=conn,
            tenant_id="tenant-a",
            client_row_id="client-row-1",
            revoked_at="2026-07-01T00:10:00+00:00",
        )
        repository.touch_mcp_server_activity(
            transaction=conn,
            tenant_id="tenant-a",
            app_id="app-1",
            observed_at="2026-07-01T00:12:00+00:00",
        )
        assert repository.insert_sdk_version_or_get_existing(transaction=conn, record=_sdk_version()) is None
        sdk_duplicate = repository.insert_sdk_version_or_get_existing(transaction=conn, record=_sdk_version())
        artifact = repository.insert_release_artifact(transaction=conn, record=_artifact())
        channel = repository.upsert_release_channel(transaction=conn, record=_channel())
        window = repository.insert_compatibility_window(transaction=conn, record=_window())
        token = repository.insert_artifact_download_token(transaction=conn, record=_download_token("token-1"))
        replay_token = repository.insert_artifact_download_token_or_get_existing(
            transaction=conn, record=_download_token("token-2")
        )
        idem = repository.insert_developer_console_idempotency_record(transaction=conn, record=_idempotency())
        repository.mark_artifact_download_token_used(
            transaction=conn,
            tenant_id="tenant-a",
            token_id="token-1",
            used_at="2026-07-01T00:10:00+00:00",
        )

        app = repository.application_by_id(transaction=conn, tenant_id="tenant-a", app_id="app-1")
        apps = repository.list_applications(transaction=conn, tenant_id="tenant-a")
        client = repository.active_client(transaction=conn, tenant_id="tenant-a", client_id="orders-web")
        clients = repository.clients_for_application(transaction=conn, tenant_id="tenant-a", app_id="app-1")
        updated_client = repository.update_client(
            transaction=conn,
            tenant_id="tenant-a",
            client_row_id="client-row-1",
            status="inactive",
            redirect_uris=("https://orders.example.test/new-callback",),
            allowed_scopes=("osdk:object:Order:read",),
            access_token_ttl_seconds=300,
            refresh_token_ttl_seconds=1800,
            updated_at="2026-07-01T00:11:00+00:00",
        )
        resources = repository.resources_for_application(transaction=conn, tenant_id="tenant-a", app_id="app-1")
        fetched_mcp_server = repository.mcp_server_for_application(
            transaction=conn, tenant_id="tenant-a", app_id="app-1"
        )
        mcp_servers = repository.list_mcp_servers(transaction=conn, tenant_id="tenant-a")
        version = repository.sdk_version_by_id(transaction=conn, tenant_id="tenant-a", version_id="sdk-1")
        versions = repository.sdk_versions_for_application(transaction=conn, tenant_id="tenant-a", app_id="app-1")
        artifact_by_id = repository.release_artifact_by_id(
            transaction=conn, tenant_id="tenant-a", artifact_id="artifact-1"
        )
        fetched_artifact = repository.release_artifact(
            transaction=conn, tenant_id="tenant-a", sdk_version_id="sdk-1", artifact_kind="typescript"
        )
        channels = repository.release_channels_for_application(
            transaction=conn, tenant_id="tenant-a", app_id="app-1", language="typescript"
        )
        windows = repository.compatibility_windows_for_application(
            transaction=conn, tenant_id="tenant-a", app_id="app-1"
        )
        used_token = repository.artifact_download_token_by_hash(
            transaction=conn, tenant_id="tenant-a", token_hash="sha256:download"
        )
        idem_replay = repository.developer_console_idempotency_record(
            transaction=conn,
            tenant_id="tenant-a",
            operation="osdk.sdk.promote",
            idempotency_key="promote-key",
        )

    assert duplicate is not None and duplicate["id"] == "app-1"
    assert client_duplicate is not None and client_duplicate["id"] == "client-row-1"
    assert sdk_duplicate is not None and sdk_duplicate["id"] == "sdk-1"
    assert app is not None and app["app_api_name"] == "OrdersApp"
    assert [row["id"] for row in apps] == ["app-1"]
    assert client is not None and client["client_id"] == "orders-web"
    assert len(clients) == 1
    assert updated_client is not None and updated_client["status"] == "inactive"
    assert resources[0]["scopes"] == ["osdk:object:Order:read"]
    assert mcp_server["status"] == "enabled"
    assert updated_mcp_server["id"] == "mcp-server-1"
    assert fetched_mcp_server is not None and fetched_mcp_server["status"] == "disabled"
    assert fetched_mcp_server["last_activity_at"] == "2026-07-01T00:12:00+00:00"
    assert [row["id"] for row in mcp_servers] == ["mcp-server-1"]
    assert session_duplicate is not None and session_duplicate["status"] == "active"
    assert first_event is not None and first_event["sequence"] == 1
    assert second_event is not None and second_event["sequence"] == 2
    assert [event["event_type"] for event in session_events] == ["tool.completed"]
    assert terminated_session is not None and terminated_session["status"] == "terminated"
    assert [row["tool_id"] for row in mcp_tool_activations] == ["platform.docs.search"]
    assert other_actor_activations == []
    assert first_client_secret["status"] == "active"
    assert second_client_secret["status"] == "active"
    assert current_client_secret is not None and current_client_secret["last_used_at"] == "2026-07-01T00:09:55+00:00"
    assert [row["status"] for row in client_secret_versions] == ["active", "rotated"]
    assert revoked_client_secret is not None and revoked_client_secret["status"] == "revoked"
    assert version is not None and version["version"] == "1.0.0"
    assert [row["id"] for row in versions] == ["sdk-1"]
    assert artifact_by_id is not None
    assert fetched_artifact is not None
    assert artifact["id"] == artifact_by_id["id"] == fetched_artifact["id"] == "artifact-1"
    assert channel["channel"] == channels[0]["channel"] == "stable"
    assert window["id"] == windows[0]["id"] == "window-1"
    assert token["id"] == replay_token["id"] == "token-1"
    assert used_token is not None and used_token["used_at"] == "2026-07-01T00:10:00+00:00"
    assert idem["id"] == cast(dict[str, object], idem_replay)["id"] == "idem-1"


def _engine() -> Engine:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    return engine


def _application() -> OsdkApplicationRecord:
    return OsdkApplicationRecord(
        application_id="app-1",
        tenant_id="tenant-a",
        app_api_name="OrdersApp",
        display_name="Orders App",
        status="active",
        owner_user_id="user-a",
        created_idempotency_key="app-create-key",
        created_at="2026-07-01T00:00:00+00:00",
        updated_at="2026-07-01T00:00:00+00:00",
    )


def _client() -> OsdkApplicationClientRecord:
    return OsdkApplicationClientRecord(
        client_row_id="client-row-1",
        tenant_id="tenant-a",
        app_id="app-1",
        client_id="orders-web",
        status="active",
        created_at="2026-07-01T00:00:00+00:00",
        redirect_uris=("https://orders.example.test/callback",),
        allowed_scopes=("osdk:object:Order:read",),
    )


def _resource() -> OsdkApplicationResourceRecord:
    return OsdkApplicationResourceRecord(
        resource_id="resource-1",
        tenant_id="tenant-a",
        app_id="app-1",
        resource_type="object",
        resource_api_name="Order",
        scopes=("osdk:object:Order:read",),
        created_at="2026-07-01T00:00:00+00:00",
    )


def _mcp_server(status: str) -> OsdkMcpServerRecord:
    return OsdkMcpServerRecord(
        server_id="mcp-server-1",
        tenant_id="tenant-a",
        app_id="app-1",
        status=status,
        description_markdown="Orders agent MCP server",
        allowed_origins=("https://chat.example.test",),
        last_activity_at=None,
        updated_by_user_id="user-a",
        created_at="2026-07-01T00:00:00+00:00",
        updated_at="2026-07-01T00:08:00+00:00",
    )


def _mcp_session() -> OsdkMcpSessionRecord:
    return OsdkMcpSessionRecord(
        session_id="ontology-mcp-session-1",
        tenant_id="tenant-a",
        app_id="app-1",
        client_id="orders-web",
        actor_user_id="user-a",
        status="active",
        created_at="2026-07-01T00:08:00+00:00",
        last_seen_at="2026-07-01T00:08:00+00:00",
    )


def _mcp_tool_activation() -> OsdkMcpToolActivationRecord:
    return OsdkMcpToolActivationRecord(
        activation_id="mcp-tool-activation-1",
        tenant_id="tenant-a",
        app_id="app-1",
        session_id="ontology-mcp-session-1",
        client_id="orders-web",
        actor_user_id="user-a",
        tool_id="platform.docs.search",
        query_hash="sha256:query",
        activated_at="2026-07-01T00:09:30+00:00",
    )


def _client_secret(secret_id: str, version: str, created_at: str) -> OsdkClientSecretVersionRecord:
    return OsdkClientSecretVersionRecord(
        secret_id=secret_id,
        tenant_id="tenant-a",
        app_id="app-1",
        client_row_id="client-row-1",
        client_id="orders-web",
        vault_secret_name="osdk_client_credentials/tenant-a/orders-web",
        vault_secret_version=version,
        status="active",
        created_by_user_id="user-a",
        rotation_reason="scheduled rotation",
        created_at=created_at,
    )


def _sdk_version() -> OsdkSdkVersionRecord:
    return OsdkSdkVersionRecord(
        sdk_version_id="sdk-1",
        tenant_id="tenant-a",
        app_id="app-1",
        language="typescript",
        package_name="@tenant/orders-osdk",
        version="1.0.0",
        generator_name="foundry-lite-osdk",
        generator_version="2026.07.01",
        ontology_fingerprint="fp-1",
        resource_scope_snapshot={"objects": ["Order"]},
        compatibility_status="compatible",
        release_status="released",
        artifact_hash="sha256:artifact",
        artifact_uri="local://artifact",
        created_idempotency_key="sdk-create-key",
        created_at="2026-07-01T00:01:00+00:00",
        released_at="2026-07-01T00:02:00+00:00",
    )


def _artifact() -> OsdkReleaseArtifactRecord:
    return OsdkReleaseArtifactRecord(
        artifact_id="artifact-1",
        tenant_id="tenant-a",
        app_id="app-1",
        sdk_version_id="sdk-1",
        artifact_kind="typescript",
        content_hash="sha256:artifact",
        storage_uri="local://artifact",
        metadata_json={"package": "@tenant/orders-osdk"},
        created_at="2026-07-01T00:03:00+00:00",
    )


def _channel() -> OsdkSdkReleaseChannelRecord:
    return OsdkSdkReleaseChannelRecord(
        channel_id="channel-1",
        tenant_id="tenant-a",
        app_id="app-1",
        language="typescript",
        channel="stable",
        sdk_version_id="sdk-1",
        promoted_at="2026-07-01T00:04:00+00:00",
        promoted_by_user_id="user-a",
    )


def _window() -> OsdkSdkCompatibilityWindowRecord:
    return OsdkSdkCompatibilityWindowRecord(
        window_id="window-1",
        tenant_id="tenant-a",
        app_id="app-1",
        language="typescript",
        from_sdk_version_id="sdk-1",
        to_sdk_version_id="sdk-1",
        status="active",
        supported_until="2026-08-01T00:00:00+00:00",
        created_at="2026-07-01T00:05:00+00:00",
    )


def _download_token(token_id: str) -> OsdkReleaseArtifactDownloadTokenRecord:
    return OsdkReleaseArtifactDownloadTokenRecord(
        token_id=token_id,
        tenant_id="tenant-a",
        artifact_id="artifact-1",
        token_hash="sha256:download",
        status="active",
        expires_at="2026-07-01T01:00:00+00:00",
        created_at="2026-07-01T00:06:00+00:00",
    )


def _idempotency() -> OsdkDeveloperConsoleIdempotencyRecord:
    return OsdkDeveloperConsoleIdempotencyRecord(
        record_id="idem-1",
        tenant_id="tenant-a",
        operation="osdk.sdk.promote",
        idempotency_key="promote-key",
        request_hash="sha256:request",
        response_json={"status": "stable"},
        status="SUCCEEDED",
        created_at="2026-07-01T00:07:00+00:00",
    )
