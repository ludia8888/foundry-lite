"""Application port contract for source management repository."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import TransactionContext


class SourceManagementAlreadyExistsError(Exception):
    """Raised when a tenant-scoped source management record already exists."""


class SourceCredentialRow(TypedDict):
    id: str
    tenant_id: str
    credential_name: str
    display_name: str
    kind: str
    auth_scheme: str
    secret_name: str
    secret_version: str
    status: str
    config_summary: Mapping[str, object]
    config_fingerprint: str
    created_at: str
    updated_at: str


class SourceAgentRow(TypedDict):
    id: str
    tenant_id: str
    agent_id: str
    display_name: str
    mode: str
    status: str
    capabilities: Mapping[str, object]
    network_summary: Mapping[str, object]
    last_heartbeat_at: str | None
    config_fingerprint: str
    created_at: str
    updated_at: str


class SourceNetworkPolicyRow(TypedDict):
    id: str
    tenant_id: str
    policy_name: str
    display_name: str
    mode: str
    agent_id: str | None
    allowed_hosts: Mapping[str, object]
    status: str
    config_fingerprint: str
    created_at: str
    updated_at: str


class SourceExplorationRunRow(TypedDict):
    id: str
    tenant_id: str
    source_name: str
    source_type: str
    request: Mapping[str, object]
    status: str
    result_summary: Mapping[str, object]
    error: Mapping[str, object] | None
    operations_path: str
    created_at: str


class SourceSyncRow(TypedDict):
    id: str
    tenant_id: str
    sync_name: str
    source_name: str
    display_name: str
    source_type: str
    capability: str
    target_dataset_ref: str | None
    target_media_set_id: str | None
    mode: str
    schedule: Mapping[str, object]
    config_summary: Mapping[str, object]
    config_fingerprint: str
    status: str
    last_run_id: str | None
    last_workflow_run_id: str | None
    checkpoint: Mapping[str, object]
    created_at: str
    updated_at: str


class SourceSyncRunRow(TypedDict):
    id: str
    tenant_id: str
    sync_name: str
    source_name: str
    source_type: str
    capability: str
    workflow_run_id: str | None
    dataset_version_id: str | None
    status: str
    trigger_type: str
    idempotency_key: str
    batch_limit: int | None
    checkpoint_start: Mapping[str, object]
    checkpoint_end: Mapping[str, object]
    result_summary: Mapping[str, object]
    error: Mapping[str, object] | None
    operations_path: str
    started_at: str
    completed_at: str | None
    created_at: str


@dataclass(frozen=True)
class SourceCredentialRecord:
    id: str
    tenant_id: str
    credential_name: str
    display_name: str
    kind: str
    auth_scheme: str
    secret_name: str
    secret_version: str
    status: str
    config_summary: Mapping[str, object]
    config_fingerprint: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SourceAgentRecord:
    id: str
    tenant_id: str
    agent_id: str
    display_name: str
    mode: str
    status: str
    capabilities: Mapping[str, object]
    network_summary: Mapping[str, object]
    last_heartbeat_at: str | None
    config_fingerprint: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SourceNetworkPolicyRecord:
    id: str
    tenant_id: str
    policy_name: str
    display_name: str
    mode: str
    agent_id: str | None
    allowed_hosts: Mapping[str, object]
    status: str
    config_fingerprint: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SourceExplorationRunRecord:
    id: str
    tenant_id: str
    source_name: str
    source_type: str
    request: Mapping[str, object]
    status: str
    result_summary: Mapping[str, object]
    error: Mapping[str, object] | None
    operations_path: str
    created_at: str


@dataclass(frozen=True)
class SourceSyncRecord:
    id: str
    tenant_id: str
    sync_name: str
    source_name: str
    display_name: str
    source_type: str
    capability: str
    target_dataset_ref: str | None
    target_media_set_id: str | None
    mode: str
    schedule: Mapping[str, object]
    config_summary: Mapping[str, object]
    config_fingerprint: str
    status: str
    last_run_id: str | None
    last_workflow_run_id: str | None
    checkpoint: Mapping[str, object]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SourceSyncRunRecord:
    id: str
    tenant_id: str
    sync_name: str
    source_name: str
    source_type: str
    capability: str
    workflow_run_id: str | None
    dataset_version_id: str | None
    status: str
    trigger_type: str
    idempotency_key: str
    batch_limit: int | None
    checkpoint_start: Mapping[str, object]
    checkpoint_end: Mapping[str, object]
    result_summary: Mapping[str, object]
    error: Mapping[str, object] | None
    operations_path: str
    started_at: str
    completed_at: str | None
    created_at: str


class SourceManagementRepository(Protocol):
    """Repository for Palantir-style Source management control-plane records."""

    def create_credential(self, *, transaction: TransactionContext, record: SourceCredentialRecord) -> None: ...

    def credential_by_name(
        self, *, transaction: TransactionContext, tenant_id: str, credential_name: str
    ) -> SourceCredentialRow | None: ...

    def list_credentials(self, *, tenant_id: str) -> list[SourceCredentialRow]: ...

    def create_agent(self, *, transaction: TransactionContext, record: SourceAgentRecord) -> None: ...

    def agent_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, agent_id: str
    ) -> SourceAgentRow | None: ...

    def list_agents(self, *, tenant_id: str) -> list[SourceAgentRow]: ...

    def update_agent_heartbeat(
        self, *, transaction: TransactionContext, tenant_id: str, agent_id: str, heartbeat_at: str
    ) -> SourceAgentRow | None: ...

    def create_network_policy(self, *, transaction: TransactionContext, record: SourceNetworkPolicyRecord) -> None: ...

    def network_policy_by_name(
        self, *, transaction: TransactionContext, tenant_id: str, policy_name: str
    ) -> SourceNetworkPolicyRow | None: ...

    def list_network_policies(self, *, tenant_id: str) -> list[SourceNetworkPolicyRow]: ...

    def create_exploration_run(
        self, *, transaction: TransactionContext, record: SourceExplorationRunRecord
    ) -> None: ...

    def create_sync(self, *, transaction: TransactionContext, record: SourceSyncRecord) -> None: ...

    def sync_by_name(
        self, *, transaction: TransactionContext, tenant_id: str, sync_name: str
    ) -> SourceSyncRow | None: ...

    def list_syncs(self, *, tenant_id: str) -> list[SourceSyncRow]: ...

    def update_sync_after_run(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        sync_name: str,
        run_id: str,
        workflow_run_id: str | None,
        checkpoint: Mapping[str, object],
        updated_at: str,
    ) -> SourceSyncRow | None: ...

    def create_sync_run(self, *, transaction: TransactionContext, record: SourceSyncRunRecord) -> None: ...

    def sync_run_by_idempotency_key(
        self, *, transaction: TransactionContext, tenant_id: str, sync_name: str, idempotency_key: str
    ) -> SourceSyncRunRow | None: ...

    def update_sync_run_result(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
        status: str,
        dataset_version_id: str | None,
        checkpoint_end: Mapping[str, object],
        result_summary: Mapping[str, object],
        error: Mapping[str, object] | None,
        completed_at: str,
        workflow_run_id: str | None = None,
    ) -> SourceSyncRunRow | None: ...

    def sync_run_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, run_id: str
    ) -> SourceSyncRunRow | None: ...

    def list_sync_runs(self, *, tenant_id: str, sync_name: str) -> list[SourceSyncRunRow]: ...
