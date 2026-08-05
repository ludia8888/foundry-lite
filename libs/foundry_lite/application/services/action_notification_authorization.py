"""Recipient-by-recipient data authorization for after-commit notifications."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import cast

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.action_effect_executor import (
    ActionEffectExecutionRequest,
    ActionEffectPermanentError,
)
from foundry_lite.application.ports.action_notification_recipient_directory import (
    ActionNotificationPolicy,
    ActionNotificationRecipient,
    ActionNotificationRecipientDirectory,
)
from foundry_lite.application.services.action_protocols import ActionObjectRecordLookup, ActionOntologyLookup
from foundry_lite.application.services.object_store.row_policies import visible_record
from foundry_lite.domain.context import RequestContext
from foundry_lite.security.policy import PolicyService


@dataclass(frozen=True, slots=True)
class AuthorizedNotificationRequest:
    request: ActionEffectExecutionRequest
    evidence: Mapping[str, object]
    is_delivery_suppressed: bool


def authorize_notification_request(
    transaction: TransactionContext,
    policy: PolicyService,
    directory: ActionNotificationRecipientDirectory,
    objects: ActionObjectRecordLookup,
    ontology: ActionOntologyLookup,
    request: ActionEffectExecutionRequest,
) -> AuthorizedNotificationRequest:
    """Filter a registered policy against every affected Ontology object."""
    if request.effect.kind != "notification":
        return AuthorizedNotificationRequest(request, {}, False)
    recipient_policy = directory.policy_for(tenant_id=request.tenant_id, target_ref=request.effect.target_ref)
    if recipient_policy is None:
        raise ActionEffectPermanentError("Action notification recipient policy is unavailable")
    allowed, denied = _partition_recipients(transaction, policy, objects, ontology, request, recipient_policy)
    evidence = _authorization_evidence(recipient_policy, allowed, denied)
    if denied and recipient_policy.delivery_mode == "strict":
        raise ActionEffectPermanentError("Action notification strict policy denied one or more recipients")
    filtered = _request_with_recipients(request, allowed)
    return AuthorizedNotificationRequest(filtered, evidence, not allowed)


def _partition_recipients(
    transaction: TransactionContext,
    policy: PolicyService,
    objects: ActionObjectRecordLookup,
    ontology: ActionOntologyLookup,
    request: ActionEffectExecutionRequest,
    recipient_policy: ActionNotificationPolicy,
) -> tuple[tuple[ActionNotificationRecipient, ...], tuple[ActionNotificationRecipient, ...]]:
    edits = _committed_edits(request.committed_result)
    allowed: list[ActionNotificationRecipient] = []
    denied: list[ActionNotificationRecipient] = []
    for recipient in recipient_policy.recipients:
        if _can_read_all(transaction, policy, objects, ontology, request, recipient, edits):
            allowed.append(recipient)
        else:
            denied.append(recipient)
    return tuple(allowed), tuple(denied)


def _can_read_all(
    transaction: TransactionContext,
    policy: PolicyService,
    objects: ActionObjectRecordLookup,
    ontology: ActionOntologyLookup,
    request: ActionEffectExecutionRequest,
    recipient: ActionNotificationRecipient,
    edits: tuple[Mapping[str, object], ...],
) -> bool:
    ctx = RequestContext(
        tenant_id=request.tenant_id,
        actor_user_id=recipient.user_id,
        request_id=request.request_id,
        roles=recipient.roles,
    )
    if not policy.decide(ctx, "object:read").allowed:
        return False
    for edit in edits:
        object_type = str(edit["objectType"])
        object_id = str(edit["objectId"])
        type_row = ontology._active_object_type(transaction, ctx, object_type)
        record = objects._object_record(transaction, ctx, object_type, object_id)
        if visible_record(record, type_row, ctx.roles) is None:
            return False
    return True


def _committed_edits(raw: Mapping[str, object] | None) -> tuple[Mapping[str, object], ...]:
    if raw is None:
        return ()
    edits = raw.get("edits", ())
    if not isinstance(edits, Sequence) or isinstance(edits, str | bytes):
        raise ActionEffectPermanentError("Action notification committed edit evidence is invalid")
    result: list[Mapping[str, object]] = []
    for edit in cast(Sequence[object], edits):
        if not isinstance(edit, Mapping) or not edit.get("objectType") or not edit.get("objectId"):
            raise ActionEffectPermanentError("Action notification committed edit evidence is invalid")
        result.append(cast(Mapping[str, object], edit))
    return tuple(result)


def _request_with_recipients(
    request: ActionEffectExecutionRequest,
    recipients: tuple[ActionNotificationRecipient, ...],
) -> ActionEffectExecutionRequest:
    payload = dict(request.effect.payload)
    payload.pop("recipients", None)
    payload["recipients"] = [{"userId": recipient.user_id} for recipient in recipients]
    return replace(request, effect=replace(request.effect, payload=payload))


def _authorization_evidence(
    policy: ActionNotificationPolicy,
    allowed: tuple[ActionNotificationRecipient, ...],
    denied: tuple[ActionNotificationRecipient, ...],
) -> Mapping[str, object]:
    return {
        "policyRef": policy.target_ref,
        "deliveryMode": policy.delivery_mode,
        "requestedCount": len(policy.recipients),
        "authorizedCount": len(allowed),
        "deniedCount": len(denied),
        "authorizedRecipientHashes": [_recipient_hash(item.user_id) for item in allowed],
        "deniedRecipientHashes": [_recipient_hash(item.user_id) for item in denied],
    }


def _recipient_hash(user_id: str) -> str:
    return f"sha256:{hashlib.sha256(user_id.encode('utf-8')).hexdigest()}"
