"""Security-state repository contract for OSDK client secrets and MCP discovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import TransactionContext


class OsdkMcpToolActivationRow(TypedDict):
    id: str
    tenant_id: str
    app_id: str
    session_id: str
    client_id: str
    actor_user_id: str
    tool_id: str
    query_hash: str
    activated_at: str


class OsdkClientSecretVersionRow(TypedDict):
    id: str
    tenant_id: str
    app_id: str
    client_row_id: str
    client_id: str
    vault_secret_name: str
    vault_secret_version: str
    status: str
    created_by_user_id: str
    rotation_reason: str | None
    created_at: str
    revoked_at: str | None
    last_used_at: str | None


@dataclass(frozen=True)
class OsdkMcpToolActivationRecord:
    activation_id: str
    tenant_id: str
    app_id: str
    session_id: str
    client_id: str
    actor_user_id: str
    tool_id: str
    query_hash: str
    activated_at: str


@dataclass(frozen=True)
class OsdkClientSecretVersionRecord:
    secret_id: str
    tenant_id: str
    app_id: str
    client_row_id: str
    client_id: str
    vault_secret_name: str
    vault_secret_version: str
    status: str
    created_by_user_id: str
    rotation_reason: str | None
    created_at: str


class OsdkSecurityRepository(Protocol):
    """Persist the current credential pointer and session-scoped MCP activations."""

    def activate_client_secret(
        self, *, transaction: TransactionContext, record: OsdkClientSecretVersionRecord
    ) -> OsdkClientSecretVersionRow: ...

    def current_client_secret(
        self, *, transaction: TransactionContext, tenant_id: str, client_row_id: str
    ) -> OsdkClientSecretVersionRow | None: ...

    def client_secret_versions(
        self, *, transaction: TransactionContext, tenant_id: str, client_row_id: str
    ) -> list[OsdkClientSecretVersionRow]: ...

    def revoke_current_client_secret(
        self, *, transaction: TransactionContext, tenant_id: str, client_row_id: str, revoked_at: str
    ) -> OsdkClientSecretVersionRow | None: ...

    def mark_client_secret_used(
        self, *, transaction: TransactionContext, tenant_id: str, secret_id: str, used_at: str
    ) -> None: ...

    def activate_mcp_tool(self, *, transaction: TransactionContext, record: OsdkMcpToolActivationRecord) -> bool: ...

    def mcp_tool_activations(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        app_id: str,
        session_id: str,
        client_id: str,
        actor_user_id: str,
    ) -> list[OsdkMcpToolActivationRow]: ...
