"""Source and connector request schemas.

The public compatibility import remains ``foundry_lite_api.schemas``.  This
module owns the Source bounded-context models so that the compatibility module
does not also have to implement every product area's validation contract.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from foundry_lite_api.schema_types import JsonObject


class ConnectorSyncWorkflowStartRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dataset_ref: str = Field(alias="datasetRef")
    connector_name: str = Field(alias="connectorName")
    resource_name: str = Field(alias="resourceName")
    sync_name: str | None = Field(default=None, alias="syncName")


class RestConnectorAuthRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    mode: str = "none"
    token_secret_ref: str | None = Field(default=None, alias="tokenSecretRef")
    basic_credentials_secret_ref: str | None = Field(default=None, alias="basicCredentialsSecretRef")
    header_name: str | None = Field(default=None, alias="headerName")
    header_value_secret_ref: str | None = Field(default=None, alias="headerValueSecretRef")
    token: str | None = None
    header_value: str | None = Field(default=None, alias="headerValue")


class RestConnectorPaginationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items_path: str = Field(default="items", alias="itemsPath")
    next_cursor_path: str = Field(default="nextCursor", alias="nextCursorPath")
    cursor_query_param: str = Field(default="cursor", alias="cursorQueryParam")
    cursor_key: str = Field(default="cursor", alias="cursorKey")
    strategy: str = "cursor"
    max_pages_per_snapshot: int = Field(default=1, ge=1, le=100, alias="maxPagesPerSnapshot")


class RestConnectorConnectionCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    connector_name: str = Field(alias="connectorName")
    display_name: str = Field(alias="displayName")
    base_url: str = Field(alias="baseUrl")
    auth: RestConnectorAuthRequest = Field(default_factory=RestConnectorAuthRequest)
    rate_limit_per_minute: int | None = Field(default=None, alias="rateLimitPerMinute")
    allow_private_network: bool = Field(default=False, alias="allowPrivateNetwork")


class RestConnectorConnectionUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    display_name: str | None = Field(default=None, alias="displayName")
    base_url: str | None = Field(default=None, alias="baseUrl")
    auth: RestConnectorAuthRequest | None = None
    rate_limit_per_minute: int | None = Field(default=None, alias="rateLimitPerMinute")
    allow_private_network: bool | None = Field(default=None, alias="allowPrivateNetwork")
    status: str | None = None


class RestConnectorResourceUpsertRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dataset_ref: str = Field(alias="datasetRef")
    resource_path: str = Field(alias="resourcePath")
    pagination: RestConnectorPaginationRequest = Field(default_factory=RestConnectorPaginationRequest)
    schema_columns: list[str] = Field(default_factory=list, alias="schemaColumns")
    primary_key: list[str] = Field(default_factory=list, alias="primaryKey")


class ConnectorResourceSyncStartRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    sync_name: str | None = Field(default=None, alias="syncName")


class SourceWebhookListenerCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_name: str = Field(alias="sourceName")
    display_name: str = Field(alias="displayName")
    dataset_ref: str = Field(alias="datasetRef")
    connector_name: str = Field(alias="connectorName")
    resource_name: str = Field(alias="resourceName")
    signing_secret_ref: str = Field(alias="signingSecretRef")


class SourceDebeziumCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_name: str = Field(alias="sourceName")
    display_name: str = Field(alias="displayName")
    dataset_ref: str = Field(alias="datasetRef")
    stream_name: str = Field(alias="streamName")
    topic: str
    consumer_group: str = Field(default="foundry-lite-cdc", alias="consumerGroup")
    secret_refs: JsonObject = Field(default_factory=dict, alias="secretRefs")
    primary_key: list[str] = Field(default_factory=list, alias="primaryKey")


class SourceDebeziumSyncStartRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expected_config_fingerprint: str | None = Field(default=None, alias="expectedConfigFingerprint")
    after_offset: int | None = Field(default=None, alias="afterOffset")
    limit: int | None = None


class SourceDebeziumObjectIndexStartRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    object_type_api_name: str = Field(default="Order", alias="objectTypeApiName")
    expected_config_fingerprint: str | None = Field(default=None, alias="expectedConfigFingerprint")
    max_rows_per_version: int = Field(default=10_000, alias="maxRowsPerVersion")


class SourceCredentialCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    credential_name: str = Field(alias="credentialName")
    display_name: str = Field(alias="displayName")
    kind: str
    auth_scheme: str = Field(alias="authScheme")
    secret_value: str = Field(alias="secretValue")
    secret_name: str | None = Field(default=None, alias="secretName")


class SourceAgentRegisterRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agent_id: str = Field(alias="agentId")
    display_name: str = Field(alias="displayName")
    mode: str
    capabilities: JsonObject = Field(default_factory=dict)
    network_summary: JsonObject = Field(default_factory=dict, alias="networkSummary")


class SourceNetworkPolicyCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    policy_name: str = Field(alias="policyName")
    display_name: str = Field(alias="displayName")
    mode: str
    agent_id: str | None = Field(default=None, alias="agentId")
    allowed_hosts: list[str] = Field(default_factory=list, alias="allowedHosts")


class SourceExploreRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_name: str = Field(alias="sourceName")
    source_type: str = Field(alias="sourceType")
    request: JsonObject = Field(default_factory=dict)


class SourceManagedSyncCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    sync_name: str = Field(alias="syncName")
    source_name: str = Field(alias="sourceName")
    display_name: str = Field(alias="displayName")
    source_type: str = Field(alias="sourceType")
    capability: str
    mode: str = "APPEND"
    target_dataset_ref: str | None = Field(default=None, alias="targetDatasetRef")
    target_media_set_id: str | None = Field(default=None, alias="targetMediaSetId")
    schedule: JsonObject = Field(default_factory=dict)
    config_summary: JsonObject = Field(default_factory=dict, alias="configSummary")


class SourceManagedSyncRunStartRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    trigger_type: Literal["manual", "recovery"] = Field(default="manual", alias="triggerType")
    batch_limit: int | None = Field(default=None, alias="batchLimit")


class SourceManagedStreamingSyncStateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expected_config_fingerprint: str = Field(alias="expectedConfigFingerprint")


class SourceManagedSyncScheduleUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schedule: JsonObject
    expected_config_fingerprint: str = Field(alias="expectedConfigFingerprint")


class SourceManagedSyncScheduleStateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expected_config_fingerprint: str = Field(alias="expectedConfigFingerprint")


class SourceStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: Literal["active", "disabled"]
    expected_config_fingerprint: str = Field(alias="expectedConfigFingerprint")


class SourceConnectionTestRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expected_config_fingerprint: str = Field(alias="expectedConfigFingerprint")


class SourceSchedulerTickRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    max_runs: int = Field(default=50, ge=1, le=500, alias="maxRuns")


class SourceBatchFileManifestItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file_name: str = Field(alias="fileName")
    dataset_ref: str = Field(alias="datasetRef")


class SourceBatchFileManifest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_name: str = Field(alias="sourceName")
    display_name: str = Field(alias="displayName")
    sync_name: str | None = Field(default=None, alias="syncName")
    files: list[SourceBatchFileManifestItem]
