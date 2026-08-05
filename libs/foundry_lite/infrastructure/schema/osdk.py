"""OSDK application, SDK release, and OAuth session tables."""

from __future__ import annotations

from sqlalchemy import JSON, Column, Integer, String, Table, UniqueConstraint

from foundry_lite.infrastructure.schema.base import metadata

osdk_applications = Table(
    "osdk_applications",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("app_api_name", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("status", String, nullable=False),
    Column("owner_user_id", String, nullable=False),
    Column("created_idempotency_key", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "app_api_name", name="uq_osdk_application_api_name"),
    UniqueConstraint("tenant_id", "created_idempotency_key", name="uq_osdk_application_create_key"),
)


osdk_application_clients = Table(
    "osdk_application_clients",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("app_id", String, nullable=False),
    Column("client_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("redirect_uris", JSON),
    Column("allowed_scopes", JSON),
    Column("access_token_ttl_seconds", Integer),
    Column("refresh_token_ttl_seconds", Integer),
    Column("current_secret_id", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String),
    UniqueConstraint("tenant_id", "client_id", name="uq_osdk_application_client_id"),
)


osdk_client_secret_versions = Table(
    "osdk_client_secret_versions",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("app_id", String, nullable=False),
    Column("client_row_id", String, nullable=False),
    Column("client_id", String, nullable=False),
    Column("vault_secret_name", String, nullable=False),
    Column("vault_secret_version", String, nullable=False),
    Column("status", String, nullable=False),
    Column("created_by_user_id", String, nullable=False),
    Column("rotation_reason", String),
    Column("created_at", String, nullable=False),
    Column("revoked_at", String),
    Column("last_used_at", String),
    UniqueConstraint(
        "tenant_id",
        "client_row_id",
        "vault_secret_version",
        name="uq_osdk_client_secret_version",
    ),
)


osdk_application_resources = Table(
    "osdk_application_resources",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("app_id", String, nullable=False),
    Column("resource_type", String, nullable=False),
    Column("resource_api_name", String, nullable=False),
    Column("scopes", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint(
        "tenant_id",
        "app_id",
        "resource_type",
        "resource_api_name",
        name="uq_osdk_application_resource",
    ),
)


osdk_mcp_servers = Table(
    "osdk_mcp_servers",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("app_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("description_markdown", String, nullable=False),
    Column("allowed_origins", JSON, nullable=False),
    Column("last_activity_at", String),
    Column("updated_by_user_id", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "app_id", name="uq_osdk_mcp_server_application"),
)


osdk_mcp_sessions = Table(
    "osdk_mcp_sessions",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("app_id", String, nullable=False),
    Column("client_id", String, nullable=False),
    Column("actor_user_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("last_sequence", Integer, nullable=False),
    Column("created_at", String, nullable=False),
    Column("last_seen_at", String, nullable=False),
    Column("terminated_at", String),
)


osdk_mcp_session_events = Table(
    "osdk_mcp_session_events",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("session_id", String, nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("event_type", String, nullable=False),
    Column("payload_json", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint("tenant_id", "session_id", "sequence", name="uq_osdk_mcp_session_event_sequence"),
)


osdk_mcp_tool_activations = Table(
    "osdk_mcp_tool_activations",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("app_id", String, nullable=False),
    Column("session_id", String, nullable=False),
    Column("client_id", String, nullable=False),
    Column("actor_user_id", String, nullable=False),
    Column("tool_id", String, nullable=False),
    Column("query_hash", String, nullable=False),
    Column("activated_at", String, nullable=False),
    UniqueConstraint(
        "tenant_id",
        "app_id",
        "session_id",
        "client_id",
        "actor_user_id",
        "tool_id",
        name="uq_osdk_mcp_tool_activation",
    ),
)


osdk_sdk_versions = Table(
    "osdk_sdk_versions",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("app_id", String, nullable=False),
    Column("language", String, nullable=False),
    Column("package_name", String, nullable=False),
    Column("version", String, nullable=False),
    Column("generator_name", String, nullable=False),
    Column("generator_version", String, nullable=False),
    Column("ontology_fingerprint", String, nullable=False),
    Column("resource_scope_snapshot", JSON, nullable=False),
    Column("compatibility_status", String, nullable=False),
    Column("release_status", String, nullable=False),
    Column("artifact_hash", String),
    Column("artifact_uri", String),
    Column("created_idempotency_key", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("released_at", String),
    UniqueConstraint("tenant_id", "app_id", "language", "version", name="uq_osdk_sdk_version"),
    UniqueConstraint("tenant_id", "app_id", "created_idempotency_key", name="uq_osdk_sdk_version_create_key"),
)


osdk_release_artifacts = Table(
    "osdk_release_artifacts",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("app_id", String, nullable=False),
    Column("sdk_version_id", String, nullable=False),
    Column("artifact_kind", String, nullable=False),
    Column("content_hash", String, nullable=False),
    Column("storage_uri", String, nullable=False),
    Column("metadata_json", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint("tenant_id", "sdk_version_id", "artifact_kind", name="uq_osdk_release_artifact"),
)


osdk_sdk_release_channels = Table(
    "osdk_sdk_release_channels",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("app_id", String, nullable=False),
    Column("language", String, nullable=False),
    Column("channel", String, nullable=False),
    Column("sdk_version_id", String, nullable=False),
    Column("promoted_at", String, nullable=False),
    Column("promoted_by_user_id", String, nullable=False),
    UniqueConstraint("tenant_id", "app_id", "language", "channel", name="uq_osdk_sdk_release_channel"),
)


osdk_sdk_compatibility_windows = Table(
    "osdk_sdk_compatibility_windows",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("app_id", String, nullable=False),
    Column("language", String, nullable=False),
    Column("from_sdk_version_id", String, nullable=False),
    Column("to_sdk_version_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("supported_until", String, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint(
        "tenant_id",
        "app_id",
        "language",
        "from_sdk_version_id",
        "to_sdk_version_id",
        name="uq_osdk_sdk_compatibility_window",
    ),
)


osdk_release_artifact_download_tokens = Table(
    "osdk_release_artifact_download_tokens",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("artifact_id", String, nullable=False),
    Column("token_hash", String, nullable=False),
    Column("status", String, nullable=False),
    Column("expires_at", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("used_at", String),
    UniqueConstraint("tenant_id", "token_hash", name="uq_osdk_release_artifact_download_token_hash"),
)


osdk_developer_console_idempotency_records = Table(
    "osdk_developer_console_idempotency_records",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("operation", String, nullable=False),
    Column("idempotency_key", String, nullable=False),
    Column("request_hash", String, nullable=False),
    Column("response_json", JSON, nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint(
        "tenant_id",
        "operation",
        "idempotency_key",
        name="uq_osdk_developer_console_idempotency",
    ),
)


osdk_oauth_authorization_codes = Table(
    "osdk_oauth_authorization_codes",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("code_hash", String, nullable=False),
    Column("app_id", String, nullable=False),
    Column("client_id", String, nullable=False),
    Column("actor_user_id", String, nullable=False),
    Column("roles", JSON, nullable=False),
    Column("redirect_uri", String, nullable=False),
    Column("code_challenge", String, nullable=False),
    Column("code_challenge_method", String, nullable=False),
    Column("scopes", JSON, nullable=False),
    Column("expires_at", String, nullable=False),
    Column("consumed_at", String),
    Column("created_at", String, nullable=False),
    UniqueConstraint("tenant_id", "code_hash", name="uq_osdk_oauth_authorization_code_hash"),
)


osdk_oauth_sessions = Table(
    "osdk_oauth_sessions",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("app_id", String, nullable=False),
    Column("client_id", String, nullable=False),
    Column("actor_user_id", String, nullable=False),
    Column("roles", JSON, nullable=False),
    Column("scopes", JSON, nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("last_refreshed_at", String),
    Column("expires_at", String, nullable=False),
    Column("revoked_at", String),
    Column("compromised_at", String),
    Column("compromise_reason", String),
)


osdk_oauth_refresh_tokens = Table(
    "osdk_oauth_refresh_tokens",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("session_id", String, nullable=False),
    Column("token_hash", String, nullable=False),
    Column("status", String, nullable=False),
    Column("expires_at", String, nullable=False),
    Column("replaced_by_token_id", String),
    Column("created_at", String, nullable=False),
    Column("used_at", String),
    Column("revoked_at", String),
    UniqueConstraint("tenant_id", "token_hash", name="uq_osdk_oauth_refresh_token_hash"),
)
