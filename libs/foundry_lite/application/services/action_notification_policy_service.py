"""Governed no-code registry for Action notification recipient policies."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports.action_notification_policy_repository import (
    ActionNotificationPolicyIdempotencyRecord,
    ActionNotificationPolicyRecord,
    ActionNotificationPolicyRepository,
    ActionNotificationPolicyRow,
    ActionNotificationPolicyStatus,
)
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.action_notification_policy_payloads import (
    decode_policy_cursor,
    encode_policy_cursor,
    normalize_policy_input,
    policy_audit_view,
    policy_config_fingerprint,
    policy_request_fingerprint,
    policy_view,
    require_idempotency_replay,
    require_policy_idempotency_key,
    require_policy_name,
)
from foundry_lite.application.services.action_protocols import ActionRuntimeBoundary
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound


class ActionNotificationPolicyService(CoreService):
    """Create and version tenant policies without embedding recipients in Actions."""

    required_dependencies = ("action_notification_policy_repository", "engine", "policy")
    required_collaborators = ("runtime_service",)
    action_notification_policy_repository: ActionNotificationPolicyRepository
    runtime_service: ActionRuntimeBoundary

    def list_policies(
        self, *, cursor: str | None = None, limit: int = 50, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        request_context = ctx or RequestContext()
        self.policy.require(request_context, "action:notification-policy:read")
        bounded = min(max(limit, 1), 100)
        after = decode_policy_cursor(cursor, request_context.tenant_id)
        with self.engine.begin() as transaction:
            rows = self.action_notification_policy_repository.list_policies(
                transaction=transaction,
                tenant_id=request_context.tenant_id,
                after_name=after,
                limit=bounded,
            )
        next_cursor = (
            encode_policy_cursor(request_context.tenant_id, rows[-1]["policy_name"]) if len(rows) == bounded else None
        )
        return {"items": [policy_view(row) for row in rows], "nextCursor": next_cursor}

    def get_policy(self, policy_name: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        request_context = ctx or RequestContext()
        self.policy.require(request_context, "action:notification-policy:read")
        name = require_policy_name(policy_name)
        with self.engine.begin() as transaction:
            return policy_view(self._require_policy(transaction, request_context, name))

    def create_policy(
        self,
        policy_name: str,
        *,
        display_name: str,
        delivery_mode: str,
        recipients: Sequence[Mapping[str, object]],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        request_context = self._require_manage(ctx, "create", policy_name)
        name = require_policy_name(policy_name)
        normalized = normalize_policy_input(
            display_name=display_name,
            delivery_mode=delivery_mode,
            recipients=recipients,
            status="active",
        )
        return self._create(request_context, name, normalized, require_policy_idempotency_key(idempotency_key))

    def update_policy(
        self,
        policy_name: str,
        *,
        display_name: str,
        delivery_mode: str,
        recipients: Sequence[Mapping[str, object]],
        status: str,
        expected_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        request_context = self._require_manage(ctx, "update", policy_name)
        name = require_policy_name(policy_name)
        normalized = normalize_policy_input(
            display_name=display_name,
            delivery_mode=delivery_mode,
            recipients=recipients,
            status=status,
        )
        return self._update(
            request_context,
            name,
            normalized,
            expected_fingerprint.strip(),
            require_policy_idempotency_key(idempotency_key),
            "update",
        )

    def disable_policy(
        self,
        policy_name: str,
        *,
        expected_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        request_context = self._require_manage(ctx, "disable", policy_name)
        name = require_policy_name(policy_name)
        with self.engine.begin() as transaction:
            current = self._require_policy(transaction, request_context, name)
        normalized = normalize_policy_input(
            display_name=current["display_name"],
            delivery_mode=current["delivery_mode"],
            recipients=current["recipients"],
            status="disabled",
        )
        return self._update(
            request_context,
            name,
            normalized,
            expected_fingerprint.strip(),
            require_policy_idempotency_key(idempotency_key),
            "disable",
        )

    def _create(
        self, ctx: RequestContext, name: str, normalized: Mapping[str, object], idempotency_key: str
    ) -> dict[str, object]:
        operation = f"create:{name}"
        request_fingerprint = policy_request_fingerprint(operation, name, normalized)
        with self.engine.begin() as transaction:
            replay = self._replay(transaction, ctx, operation, idempotency_key, request_fingerprint)
            if replay is not None:
                return replay
            row = self.action_notification_policy_repository.insert_policy_if_absent(
                transaction=transaction,
                record=_new_policy_record(ctx, name, normalized),
            )
            if row is None:
                replay = self._replay(transaction, ctx, operation, idempotency_key, request_fingerprint)
                if replay is not None:
                    return replay
                raise ConflictDetected("notification policy name already exists")
            response = policy_view(row)
            self._record_mutation(
                transaction, ctx, operation, idempotency_key, request_fingerprint, None, row, response
            )
            return response

    def _update(
        self,
        ctx: RequestContext,
        name: str,
        normalized: Mapping[str, object],
        expected_fingerprint: str,
        idempotency_key: str,
        verb: str,
    ) -> dict[str, object]:
        operation = f"{verb}:{name}"
        request = {**normalized, "expectedFingerprint": expected_fingerprint}
        request_fingerprint = policy_request_fingerprint(operation, name, request)
        with self.engine.begin() as transaction:
            replay = self._replay(transaction, ctx, operation, idempotency_key, request_fingerprint)
            if replay is not None:
                return replay
            before = self._require_policy(transaction, ctx, name)
            row = self._cas_update(transaction, ctx, name, normalized, expected_fingerprint)
            if row is None:
                replay = self._replay(transaction, ctx, operation, idempotency_key, request_fingerprint)
                if replay is not None:
                    return replay
                raise ConflictDetected("notification policy changed concurrently")
            response = policy_view(row)
            self._record_mutation(
                transaction, ctx, operation, idempotency_key, request_fingerprint, before, row, response
            )
            return response

    def _cas_update(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        name: str,
        normalized: Mapping[str, object],
        expected_fingerprint: str,
    ) -> ActionNotificationPolicyRow | None:
        next_fingerprint = policy_config_fingerprint(name, normalized)
        return self.action_notification_policy_repository.update_policy(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            policy_name=name,
            expected_fingerprint=expected_fingerprint,
            display_name=str(normalized["displayName"]),
            delivery_mode=str(normalized["deliveryMode"]),
            recipients=cast(tuple[dict[str, object], ...], normalized["recipients"]),
            status=cast(ActionNotificationPolicyStatus, normalized["status"]),
            config_fingerprint=next_fingerprint,
            actor_user_id=ctx.actor_user_id,
            updated_at=_now(),
        )

    def _record_mutation(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        operation: str,
        key: str,
        request_fingerprint: str,
        before: ActionNotificationPolicyRow | None,
        after: ActionNotificationPolicyRow,
        response: dict[str, object],
    ) -> None:
        event = f"action.notification_policy.{operation.split(':', 1)[0]}d"
        self._record_evidence(transaction, ctx, event, key, before, after)
        self._store_idempotency(transaction, ctx, operation, key, request_fingerprint, response)

    def _record_evidence(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        event: str,
        key: str,
        before: ActionNotificationPolicyRow | None,
        after: ActionNotificationPolicyRow,
    ) -> None:
        self.runtime_service._audit(
            transaction,
            ctx,
            event_type=event,
            resource_type="action_notification_policy",
            resource_id=after["id"],
            action="action_notification_policy_manage",
            before_ref=policy_audit_view(before) if before else None,
            after_ref=policy_audit_view(after),
            correlation_id=key,
        )
        self.runtime_service._outbox(
            transaction,
            ctx,
            event,
            "action_notification_policy",
            after["id"],
            policy_audit_view(after),
            idempotency_key=key,
            correlation_id=key,
        )

    def _store_idempotency(
        self,
        transaction: object,
        ctx: RequestContext,
        operation: str,
        key: str,
        request_fingerprint: str,
        response: dict[str, object],
    ) -> None:
        existing = self.action_notification_policy_repository.insert_idempotency_or_existing(
            transaction=transaction,
            record=ActionNotificationPolicyIdempotencyRecord(
                _new_id("action-notification-policy-idem"),
                ctx.tenant_id,
                ctx.actor_user_id,
                operation,
                key,
                request_fingerprint,
                response,
                _now(),
            ),
        )
        if existing is not None:
            require_idempotency_replay(existing, request_fingerprint)

    def _replay(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        operation: str,
        key: str,
        fingerprint: str,
    ) -> dict[str, object] | None:
        row = self.action_notification_policy_repository.idempotency_record(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.actor_user_id,
            operation=operation,
            idempotency_key=key,
        )
        return require_idempotency_replay(row, fingerprint) if row else None

    def _require_policy(
        self, transaction: TransactionContext, ctx: RequestContext, name: str
    ) -> ActionNotificationPolicyRow:
        row = self.action_notification_policy_repository.policy_by_name(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            policy_name=name,
        )
        if row is None:
            raise NotFound("notification policy not found")
        return row

    def _require_manage(self, ctx: RequestContext | None, operation: str, name: str) -> RequestContext:
        request_context = ctx or RequestContext()
        self.policy.require(request_context, "action:notification-policy:manage")
        self.runtime_service._require_write_traffic_open(
            request_context,
            operation=f"{operation}_action_notification_policy",
            resource_type="action_notification_policy",
            resource_id=name,
        )
        return request_context


def _new_policy_record(
    ctx: RequestContext, name: str, normalized: Mapping[str, object]
) -> ActionNotificationPolicyRecord:
    timestamp = _now()
    return ActionNotificationPolicyRecord(
        _new_id("action-notification-policy"),
        ctx.tenant_id,
        name,
        f"notification-policy:{name}",
        str(normalized["displayName"]),
        str(normalized["deliveryMode"]),
        cast(tuple[dict[str, object], ...], normalized["recipients"]),
        cast(ActionNotificationPolicyStatus, normalized["status"]),
        1,
        policy_config_fingerprint(name, normalized),
        ctx.actor_user_id,
        timestamp,
    )
