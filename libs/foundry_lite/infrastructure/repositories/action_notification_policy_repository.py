"""SQLAlchemy adapter for durable Action notification policy governance."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import and_, insert, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.action_notification_policy_repository import (
    ActionNotificationPolicyIdempotencyRecord,
    ActionNotificationPolicyIdempotencyRow,
    ActionNotificationPolicyIntegrityError,
    ActionNotificationPolicyRecord,
    ActionNotificationPolicyRepository,
    ActionNotificationPolicyRow,
    ActionNotificationPolicyStatus,
)
from foundry_lite.application.ports.action_notification_recipient_directory import (
    ActionNotificationPolicy,
    ActionNotificationRecipient,
    ActionNotificationRecipientDirectory,
    NotificationDeliveryMode,
)
from foundry_lite.application.state_transitions import (
    ACTION_NOTIFICATION_POLICY_ACTIVE,
    ACTION_NOTIFICATION_POLICY_DISABLED,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.status_cas import cas_status_update_many


class SqlAlchemyActionNotificationPolicyRepository(ActionNotificationPolicyRepository):
    """Own policy rows and expose active policies as the runtime directory."""

    profile_name = "durable-action-notification-policy-registry"

    def __init__(
        self,
        engine: Engine,
        *,
        fallback: ActionNotificationRecipientDirectory | None = None,
    ) -> None:
        self.engine = engine
        self.fallback = fallback

    def policy_by_name(
        self, *, transaction: Any, tenant_id: str, policy_name: str
    ) -> ActionNotificationPolicyRow | None:
        return _one_policy(
            transaction,
            db.action_notification_policies.c.tenant_id == tenant_id,
            db.action_notification_policies.c.policy_name == policy_name,
        )

    def policy_by_target(
        self, *, transaction: Any, tenant_id: str, target_ref: str
    ) -> ActionNotificationPolicyRow | None:
        return _one_policy(
            transaction,
            db.action_notification_policies.c.tenant_id == tenant_id,
            db.action_notification_policies.c.target_ref == target_ref,
        )

    def list_policies(
        self, *, transaction: Any, tenant_id: str, after_name: str | None, limit: int
    ) -> list[ActionNotificationPolicyRow]:
        table = db.action_notification_policies
        statement = select(table).where(table.c.tenant_id == tenant_id)
        if after_name is not None:
            statement = statement.where(table.c.policy_name > after_name)
        rows = transaction.execute(statement.order_by(table.c.policy_name).limit(limit)).mappings().all()
        return [cast(ActionNotificationPolicyRow, dict(row)) for row in rows]

    def insert_policy_if_absent(
        self, *, transaction: Any, record: ActionNotificationPolicyRecord
    ) -> ActionNotificationPolicyRow | None:
        values = _policy_values(record)
        inserted_id = transaction.execute(_insert_policy_or_ignore(transaction, values)).scalar_one_or_none()
        if inserted_id is None:
            name_conflict = self.policy_by_name(
                transaction=transaction,
                tenant_id=record.tenant_id,
                policy_name=record.policy_name,
            )
            target_conflict = self.policy_by_target(
                transaction=transaction,
                tenant_id=record.tenant_id,
                target_ref=record.target_ref,
            )
            if name_conflict is not None or target_conflict is not None:
                return None
            raise ActionNotificationPolicyIntegrityError(
                "policy insert was ignored without a matching business-key conflict"
            )
        return self.policy_by_name(
            transaction=transaction,
            tenant_id=record.tenant_id,
            policy_name=record.policy_name,
        )

    def update_policy(
        self,
        *,
        transaction: Any,
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
        table = db.action_notification_policies
        transition = ACTION_NOTIFICATION_POLICY_ACTIVE if status == "active" else ACTION_NOTIFICATION_POLICY_DISABLED
        updated = cas_status_update_many(
            transaction,
            table,
            tenant_id=tenant_id,
            transition=transition,
            values={
                "display_name": display_name,
                "delivery_mode": delivery_mode,
                "recipients": list(recipients),
                "version": table.c.version + 1,
                "config_fingerprint": config_fingerprint,
                "updated_by_user_id": actor_user_id,
                "updated_at": updated_at,
            },
            conditions=(
                table.c.policy_name == policy_name,
                table.c.config_fingerprint == expected_fingerprint,
            ),
        )
        if updated != 1:
            return None
        return self.policy_by_name(transaction=transaction, tenant_id=tenant_id, policy_name=policy_name)

    def idempotency_record(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        actor_user_id: str,
        operation: str,
        idempotency_key: str,
    ) -> ActionNotificationPolicyIdempotencyRow | None:
        table = db.action_notification_policy_idempotency_records
        row = (
            transaction.execute(
                select(table).where(
                    and_(
                        table.c.tenant_id == tenant_id,
                        table.c.actor_user_id == actor_user_id,
                        table.c.operation == operation,
                        table.c.idempotency_key == idempotency_key,
                    )
                )
            )
            .mappings()
            .first()
        )
        return cast(ActionNotificationPolicyIdempotencyRow, dict(row)) if row else None

    def insert_idempotency_or_existing(
        self, *, transaction: Any, record: ActionNotificationPolicyIdempotencyRecord
    ) -> ActionNotificationPolicyIdempotencyRow | None:
        statement = _insert_idempotency_or_ignore(transaction, _idempotency_values(record))
        inserted_id = transaction.execute(statement).scalar_one_or_none()
        if inserted_id == record.record_id:
            return None
        return self.idempotency_record(
            transaction=transaction,
            tenant_id=record.tenant_id,
            actor_user_id=record.actor_user_id,
            operation=record.operation,
            idempotency_key=record.idempotency_key,
        )

    def policy_for(self, *, tenant_id: str, target_ref: str) -> ActionNotificationPolicy | None:
        with self.engine.begin() as transaction:
            row = self.policy_by_target(transaction=transaction, tenant_id=tenant_id, target_ref=target_ref)
        if row is not None:
            return _directory_policy(row) if row["status"] == "active" else None
        return self.fallback.policy_for(tenant_id=tenant_id, target_ref=target_ref) if self.fallback else None


def _one_policy(transaction: Any, *clauses: Any) -> ActionNotificationPolicyRow | None:
    row = transaction.execute(select(db.action_notification_policies).where(and_(*clauses))).mappings().first()
    return cast(ActionNotificationPolicyRow, dict(row)) if row else None


def _insert_policy_or_ignore(transaction: Any, values: dict[str, object]) -> Any:
    table = db.action_notification_policies
    if transaction.dialect.name == "postgresql":
        return postgres_insert(table).values(**values).on_conflict_do_nothing().returning(table.c.id)
    if transaction.dialect.name == "sqlite":
        return sqlite_insert(table).values(**values).on_conflict_do_nothing().returning(table.c.id)
    return insert(table).values(**values).returning(table.c.id)


def _insert_idempotency_or_ignore(transaction: Any, values: dict[str, object]) -> Any:
    table = db.action_notification_policy_idempotency_records
    conflict = ("tenant_id", "actor_user_id", "operation", "idempotency_key")
    if transaction.dialect.name == "postgresql":
        return (
            postgres_insert(table)
            .values(**values)
            .on_conflict_do_nothing(index_elements=conflict)
            .returning(table.c.id)
        )
    if transaction.dialect.name == "sqlite":
        return (
            sqlite_insert(table).values(**values).on_conflict_do_nothing(index_elements=conflict).returning(table.c.id)
        )
    return insert(table).values(**values).returning(table.c.id)


def _policy_values(record: ActionNotificationPolicyRecord) -> dict[str, object]:
    return {
        "id": record.policy_id,
        "tenant_id": record.tenant_id,
        "policy_name": record.policy_name,
        "target_ref": record.target_ref,
        "display_name": record.display_name,
        "delivery_mode": record.delivery_mode,
        "recipients": list(record.recipients),
        "status": record.status,
        "version": record.version,
        "config_fingerprint": record.config_fingerprint,
        "created_by_user_id": record.actor_user_id,
        "updated_by_user_id": record.actor_user_id,
        "created_at": record.created_at,
        "updated_at": record.created_at,
    }


def _idempotency_values(record: ActionNotificationPolicyIdempotencyRecord) -> dict[str, object]:
    return {
        "id": record.record_id,
        "tenant_id": record.tenant_id,
        "actor_user_id": record.actor_user_id,
        "operation": record.operation,
        "idempotency_key": record.idempotency_key,
        "request_fingerprint": record.request_fingerprint,
        "response_json": record.response_json,
        "created_at": record.created_at,
    }


def _directory_policy(row: ActionNotificationPolicyRow) -> ActionNotificationPolicy:
    recipients = tuple(
        ActionNotificationRecipient(str(item["userId"]), _recipient_roles(item.get("roles")))
        for item in row["recipients"]
    )
    return ActionNotificationPolicy(
        row["target_ref"],
        cast(NotificationDeliveryMode, row["delivery_mode"]),
        recipients,
    )


def _recipient_roles(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(role) for role in value)
