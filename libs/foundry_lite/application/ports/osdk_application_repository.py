"""Application port contract for osdk application repository."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NotRequired, Protocol, TypedDict

from foundry_lite.application.ports.runtime_repository import RuntimeJsonObject
from foundry_lite.application.ports.transaction_context import TransactionContext

OsdkResourceType = Literal["object", "action", "link", "function", "connector"]
OsdkResourceOperation = Literal["read", "subscribe", "validate", "execute"]
OsdkLanguage = Literal["typescript", "python"]
OsdkReleaseStatus = Literal["released", "blocked"]


class OsdkApplicationRow(TypedDict):
    id: str
    tenant_id: str
    app_api_name: str
    display_name: str
    status: str
    owner_user_id: str
    created_idempotency_key: str
    created_at: str
    updated_at: str


class OsdkApplicationClientRow(TypedDict):
    id: str
    tenant_id: str
    app_id: str
    client_id: str
    status: str
    redirect_uris: NotRequired[list[str] | None]
    allowed_scopes: NotRequired[list[str] | None]
    access_token_ttl_seconds: NotRequired[int | None]
    refresh_token_ttl_seconds: NotRequired[int | None]
    created_at: str
    updated_at: NotRequired[str | None]


class OsdkApplicationResourceRow(TypedDict):
    id: str
    tenant_id: str
    app_id: str
    resource_type: OsdkResourceType
    resource_api_name: str
    scopes: list[str]
    created_at: str


class OsdkSdkVersionRow(TypedDict):
    id: str
    tenant_id: str
    app_id: str
    language: OsdkLanguage
    package_name: str
    version: str
    generator_name: str
    generator_version: str
    ontology_fingerprint: str
    resource_scope_snapshot: RuntimeJsonObject
    compatibility_status: str
    release_status: OsdkReleaseStatus
    artifact_hash: str | None
    artifact_uri: str | None
    created_idempotency_key: str
    created_at: str
    released_at: str | None


class OsdkReleaseArtifactRow(TypedDict):
    id: str
    tenant_id: str
    app_id: str
    sdk_version_id: str
    artifact_kind: str
    content_hash: str
    storage_uri: str
    metadata_json: RuntimeJsonObject
    created_at: str


class OsdkSdkReleaseChannelRow(TypedDict):
    id: str
    tenant_id: str
    app_id: str
    language: OsdkLanguage
    channel: str
    sdk_version_id: str
    promoted_at: str
    promoted_by_user_id: str


class OsdkSdkCompatibilityWindowRow(TypedDict):
    id: str
    tenant_id: str
    app_id: str
    language: OsdkLanguage
    from_sdk_version_id: str
    to_sdk_version_id: str
    status: str
    supported_until: str
    created_at: str


class OsdkReleaseArtifactDownloadTokenRow(TypedDict):
    id: str
    tenant_id: str
    artifact_id: str
    token_hash: str
    status: str
    expires_at: str
    created_at: str
    used_at: str | None


class OsdkDeveloperConsoleIdempotencyRow(TypedDict):
    id: str
    tenant_id: str
    operation: str
    idempotency_key: str
    request_hash: str
    response_json: RuntimeJsonObject
    status: str
    created_at: str


class OsdkApplicationBundle(TypedDict):
    application: OsdkApplicationRow
    clients: list[OsdkApplicationClientRow]
    resources: list[OsdkApplicationResourceRow]


class OsdkSdkVersionBundle(TypedDict):
    sdkVersion: OsdkSdkVersionRow
    artifacts: NotRequired[list[OsdkReleaseArtifactRow]]
    channels: NotRequired[list[OsdkSdkReleaseChannelRow]]


@dataclass(frozen=True)
class OsdkApplicationRecord:
    application_id: str
    tenant_id: str
    app_api_name: str
    display_name: str
    status: str
    owner_user_id: str
    created_idempotency_key: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class OsdkApplicationClientRecord:
    client_row_id: str
    tenant_id: str
    app_id: str
    client_id: str
    status: str
    created_at: str
    redirect_uris: tuple[str, ...] = ()
    allowed_scopes: tuple[str, ...] = ()
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 2_592_000
    updated_at: str | None = None


@dataclass(frozen=True)
class OsdkApplicationResourceRecord:
    resource_id: str
    tenant_id: str
    app_id: str
    resource_type: OsdkResourceType
    resource_api_name: str
    scopes: tuple[str, ...]
    created_at: str


@dataclass(frozen=True)
class OsdkSdkVersionRecord:
    sdk_version_id: str
    tenant_id: str
    app_id: str
    language: OsdkLanguage
    package_name: str
    version: str
    generator_name: str
    generator_version: str
    ontology_fingerprint: str
    resource_scope_snapshot: RuntimeJsonObject
    compatibility_status: str
    release_status: OsdkReleaseStatus
    artifact_hash: str | None
    artifact_uri: str | None
    created_idempotency_key: str
    created_at: str
    released_at: str | None


@dataclass(frozen=True)
class OsdkReleaseArtifactRecord:
    artifact_id: str
    tenant_id: str
    app_id: str
    sdk_version_id: str
    artifact_kind: str
    content_hash: str
    storage_uri: str
    metadata_json: RuntimeJsonObject
    created_at: str


@dataclass(frozen=True)
class OsdkSdkReleaseChannelRecord:
    channel_id: str
    tenant_id: str
    app_id: str
    language: OsdkLanguage
    channel: str
    sdk_version_id: str
    promoted_at: str
    promoted_by_user_id: str


@dataclass(frozen=True)
class OsdkSdkCompatibilityWindowRecord:
    window_id: str
    tenant_id: str
    app_id: str
    language: OsdkLanguage
    from_sdk_version_id: str
    to_sdk_version_id: str
    status: str
    supported_until: str
    created_at: str


@dataclass(frozen=True)
class OsdkReleaseArtifactDownloadTokenRecord:
    token_id: str
    tenant_id: str
    artifact_id: str
    token_hash: str
    status: str
    expires_at: str
    created_at: str


@dataclass(frozen=True)
class OsdkDeveloperConsoleIdempotencyRecord:
    record_id: str
    tenant_id: str
    operation: str
    idempotency_key: str
    request_hash: str
    response_json: RuntimeJsonObject
    status: str
    created_at: str


class OsdkApplicationRepository(Protocol):
    """Durable Developer Console-lite app and SDK-release registry."""

    def insert_application_or_get_existing(
        self, *, transaction: TransactionContext, record: OsdkApplicationRecord
    ) -> OsdkApplicationRow | None: ...

    def insert_client_or_get_existing(
        self, *, transaction: TransactionContext, record: OsdkApplicationClientRecord
    ) -> OsdkApplicationClientRow | None: ...

    def application_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, app_id: str
    ) -> OsdkApplicationRow | None: ...

    def list_applications(self, *, transaction: TransactionContext, tenant_id: str) -> list[OsdkApplicationRow]: ...

    def active_client(
        self, *, transaction: TransactionContext, tenant_id: str, client_id: str
    ) -> OsdkApplicationClientRow | None: ...

    def clients_for_application(
        self, *, transaction: TransactionContext, tenant_id: str, app_id: str
    ) -> list[OsdkApplicationClientRow]: ...

    def update_client(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        client_row_id: str,
        status: str,
        redirect_uris: tuple[str, ...],
        allowed_scopes: tuple[str, ...],
        access_token_ttl_seconds: int,
        refresh_token_ttl_seconds: int,
        updated_at: str,
    ) -> OsdkApplicationClientRow | None: ...

    def replace_resources(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        app_id: str,
        resources: tuple[OsdkApplicationResourceRecord, ...],
    ) -> None: ...

    def resources_for_application(
        self, *, transaction: TransactionContext, tenant_id: str, app_id: str
    ) -> list[OsdkApplicationResourceRow]: ...

    def insert_sdk_version_or_get_existing(
        self, *, transaction: TransactionContext, record: OsdkSdkVersionRecord
    ) -> OsdkSdkVersionRow | None: ...

    def sdk_version_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, version_id: str
    ) -> OsdkSdkVersionRow | None: ...

    def sdk_versions_for_application(
        self, *, transaction: TransactionContext, tenant_id: str, app_id: str
    ) -> list[OsdkSdkVersionRow]: ...

    def insert_release_artifact(
        self, *, transaction: TransactionContext, record: OsdkReleaseArtifactRecord
    ) -> OsdkReleaseArtifactRow: ...

    def release_artifact(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        sdk_version_id: str,
        artifact_kind: str,
    ) -> OsdkReleaseArtifactRow | None: ...

    def release_artifact_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, artifact_id: str
    ) -> OsdkReleaseArtifactRow | None: ...

    def upsert_release_channel(
        self, *, transaction: TransactionContext, record: OsdkSdkReleaseChannelRecord
    ) -> OsdkSdkReleaseChannelRow: ...

    def release_channels_for_application(
        self, *, transaction: TransactionContext, tenant_id: str, app_id: str, language: OsdkLanguage | None = None
    ) -> list[OsdkSdkReleaseChannelRow]: ...

    def insert_compatibility_window(
        self, *, transaction: TransactionContext, record: OsdkSdkCompatibilityWindowRecord
    ) -> OsdkSdkCompatibilityWindowRow: ...

    def compatibility_windows_for_application(
        self, *, transaction: TransactionContext, tenant_id: str, app_id: str
    ) -> list[OsdkSdkCompatibilityWindowRow]: ...

    def insert_artifact_download_token(
        self, *, transaction: TransactionContext, record: OsdkReleaseArtifactDownloadTokenRecord
    ) -> OsdkReleaseArtifactDownloadTokenRow: ...

    def insert_artifact_download_token_or_get_existing(
        self, *, transaction: TransactionContext, record: OsdkReleaseArtifactDownloadTokenRecord
    ) -> OsdkReleaseArtifactDownloadTokenRow: ...

    def artifact_download_token_by_hash(
        self, *, transaction: TransactionContext, tenant_id: str, token_hash: str
    ) -> OsdkReleaseArtifactDownloadTokenRow | None: ...

    def mark_artifact_download_token_used(
        self, *, transaction: TransactionContext, tenant_id: str, token_id: str, used_at: str
    ) -> None: ...

    def developer_console_idempotency_record(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        operation: str,
        idempotency_key: str,
    ) -> OsdkDeveloperConsoleIdempotencyRow | None: ...

    def insert_developer_console_idempotency_record(
        self, *, transaction: TransactionContext, record: OsdkDeveloperConsoleIdempotencyRecord
    ) -> OsdkDeveloperConsoleIdempotencyRow: ...
