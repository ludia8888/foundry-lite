"""Persistence contract for governed Action notification policies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import TransactionContext

ActionNotificationPolicyStatus = Literal["active", "disabled"]


class ActionNotificationPolicyRow(TypedDict):
    id: str
    tenant_id: str
    policy_name: str
    target_ref: str
    display_name: str
    delivery_mode: str
    recipients: list[dict[str, object]]
    status: str
    version: int
    config_fingerprint: str
    created_by_user_id: str
    updated_by_user_id: str
    created_at: str
    updated_at: str


class ActionNotificationPolicyIdempotencyRow(TypedDict):
    id: str
    tenant_id: str
    actor_user_id: str
    operation: str
    idempotency_key: str
    request_fingerprint: str
    response_json: dict[str, object]
    created_at: str


@dataclass(frozen=True, slots=True)
class ActionNotificationPolicyRecord:
    policy_id: str
    tenant_id: str
    policy_name: str
    target_ref: str
    display_name: str
    delivery_mode: str
    recipients: tuple[dict[str, object], ...]
    status: ActionNotificationPolicyStatus
    version: int
    config_fingerprint: str
    actor_user_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ActionNotificationPolicyIdempotencyRecord:
    record_id: str
    tenant_id: str
    actor_user_id: str
    operation: str
    idempotency_key: str
    request_fingerprint: str
    response_json: dict[str, object]
    created_at: str


class ActionNotificationPolicyRepository(Protocol):
    """Tenant-scoped policy registry with CAS and durable mutation replay."""

    def policy_by_name(
        self, *, transaction: TransactionContext, tenant_id: str, policy_name: str
    ) -> ActionNotificationPolicyRow | None: ...

    def policy_by_target(
        self, *, transaction: TransactionContext, tenant_id: str, target_ref: str
    ) -> ActionNotificationPolicyRow | None: ...

    def list_policies(
        self, *, transaction: TransactionContext, tenant_id: str, after_name: str | None, limit: int
    ) -> list[ActionNotificationPolicyRow]: ...

    def insert_policy_if_absent(
        self, *, transaction: TransactionContext, record: ActionNotificationPolicyRecord
    ) -> ActionNotificationPolicyRow | None: ...

    def update_policy(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        policy_name: str,
        expected_fingerprint: str,
        display_name: str,
        delivery_mode: str,
        recipients: tuple[dict[str, object], ...],
        status: ActionNotificationPolicyStatus,
        config_fingerprint: str,
        actor_user_id: str,
        updated_at: str,
    ) -> ActionNotificationPolicyRow | None: ...

    def idempotency_record(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        actor_user_id: str,
        operation: str,
        idempotency_key: str,
    ) -> ActionNotificationPolicyIdempotencyRow | None: ...

    def insert_idempotency_or_existing(
        self, *, transaction: TransactionContext, record: ActionNotificationPolicyIdempotencyRecord
    ) -> ActionNotificationPolicyIdempotencyRow | None: ...


class UnavailableActionNotificationPolicyRepository:
    """Empty read surface and explicit mutation failure for unwired runtimes."""

    def policy_by_name(
        self, *, transaction: TransactionContext, tenant_id: str, policy_name: str
    ) -> ActionNotificationPolicyRow | None:
        del transaction, tenant_id, policy_name
        return None

    def policy_by_target(
        self, *, transaction: TransactionContext, tenant_id: str, target_ref: str
    ) -> ActionNotificationPolicyRow | None:
        del transaction, tenant_id, target_ref
        return None

    def list_policies(
        self, *, transaction: TransactionContext, tenant_id: str, after_name: str | None, limit: int
    ) -> list[ActionNotificationPolicyRow]:
        del transaction, tenant_id, after_name, limit
        return []

    def insert_policy_if_absent(
        self, *, transaction: TransactionContext, record: ActionNotificationPolicyRecord
    ) -> ActionNotificationPolicyRow | None:
        del transaction, record
        raise RuntimeError("Action notification policy repository unavailable")

    def update_policy(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        policy_name: str,
        expected_fingerprint: str,
        display_name: str,
        delivery_mode: str,
        recipients: tuple[dict[str, object], ...],
        status: ActionNotificationPolicyStatus,
        config_fingerprint: str,
        actor_user_id: str,
        updated_at: str,
    ) -> ActionNotificationPolicyRow | None:
        del (
            transaction,
            tenant_id,
            policy_name,
            expected_fingerprint,
            display_name,
            delivery_mode,
            recipients,
            status,
            config_fingerprint,
            actor_user_id,
            updated_at,
        )
        raise RuntimeError("Action notification policy repository unavailable")

    def idempotency_record(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        actor_user_id: str,
        operation: str,
        idempotency_key: str,
    ) -> ActionNotificationPolicyIdempotencyRow | None:
        del transaction, tenant_id, actor_user_id, operation, idempotency_key
        return None

    def insert_idempotency_or_existing(
        self, *, transaction: TransactionContext, record: ActionNotificationPolicyIdempotencyRecord
    ) -> ActionNotificationPolicyIdempotencyRow | None:
        del transaction, record
        raise RuntimeError("Action notification policy repository unavailable")
