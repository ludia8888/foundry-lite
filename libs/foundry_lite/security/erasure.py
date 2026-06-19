"""Plan tenant-scoped right-to-erasure work without storing raw subject values."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

ErasureResourceType = Literal[
    "dataset_row",
    "object_record",
    "search_document",
    "materialization_row",
    "dead_letter_record",
    "backup_snapshot",
    "audit_event",
]
ErasureActionType = Literal[
    "redact_dataset_row",
    "tombstone_object",
    "remove_search_document",
    "rebuild_materialization",
    "redact_dead_letter_payload",
    "defer_backup_expiration",
    "minimize_audit_evidence",
]
ErasureActionStatus = Literal["READY", "DEFERRED"]
ErasureManifestStatus = Literal["READY_TO_APPLY", "PENDING_RETENTION"]

_ERASURE_MANIFEST_NAMESPACE: Final = uuid.UUID("a8019d04-44b6-41d6-b9f8-a42e833e3878")
_PROTECTED_VALUE: Final = "***PROTECTED***"


@dataclass(frozen=True)
class ErasureRequest:
    """A privacy subject erasure request with idempotent, redacted evidence."""

    tenant_id: str
    request_id: str
    subject_kind: str
    subject_value: object
    requested_by: str
    idempotency_key: str
    reason: str
    requested_at: str

    @property
    def subject_hash(self) -> str:
        """Return the stable proof key for the protected subject value."""

        payload = {
            "tenantId": self.tenant_id,
            "subjectKind": self.subject_kind,
            "subjectValue": self.subject_value,
        }
        return _stable_hash(payload)

    def redacted_evidence(self) -> dict[str, object]:
        """Return operator evidence without exposing the raw subject value."""

        return {
            "tenantId": self.tenant_id,
            "requestId": self.request_id,
            "subjectKind": self.subject_kind,
            "subjectValue": _PROTECTED_VALUE,
            "subjectHash": self.subject_hash,
            "requestedBy": self.requested_by,
            "idempotencyKey": self.idempotency_key,
            "reason": self.reason,
            "requestedAt": self.requested_at,
        }


@dataclass(frozen=True)
class ErasureResourceRef:
    """A tenant-scoped record that must be erased, tombstoned, or rebuilt."""

    tenant_id: str
    resource_type: ErasureResourceType
    resource_id: str
    surface: str

    def manifest_payload(self) -> dict[str, object]:
        """Return the stable resource payload used in manifest hashing."""

        return {
            "tenantId": self.tenant_id,
            "resourceType": self.resource_type,
            "resourceId": self.resource_id,
            "surface": self.surface,
        }


@dataclass(frozen=True)
class ErasureResolution:
    """The resolved resources touched by one erasure request."""

    request: ErasureRequest
    resources: tuple[ErasureResourceRef, ...]

    def __post_init__(self) -> None:
        """Reject cross-tenant resource matches before a manifest is built."""

        mismatched = tuple(ref for ref in self.resources if ref.tenant_id != self.request.tenant_id)
        if mismatched:
            raise ValueError("erasure resolution cannot include resources from another tenant")


@dataclass(frozen=True)
class ErasureRetentionPolicy:
    """Retention rules for backups and the minimum legal audit record."""

    backup_retention_until: str
    crypto_shredding_key_ref: str
    audit_retention_basis: str = "legal_obligation"
    minimum_audit_fields: tuple[str, ...] = ("tenant_id", "request_id", "subject_hash", "action", "created_at")


@dataclass(frozen=True)
class ErasureManifestAction:
    """A single planned erasure action for one storage or projection surface."""

    action_type: ErasureActionType
    resource: ErasureResourceRef
    status: ErasureActionStatus
    reason: str
    evidence: Mapping[str, object]

    def redacted_evidence(self) -> dict[str, object]:
        """Return action evidence safe to store in operator artifacts."""

        return {
            "actionType": self.action_type,
            "resource": self.resource.manifest_payload(),
            "status": self.status,
            "reason": self.reason,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class ErasureManifest:
    """An idempotent erasure plan across serving, projection, and backup surfaces."""

    manifest_id: str
    tenant_id: str
    request_id: str
    subject_hash: str
    status: ErasureManifestStatus
    actions: tuple[ErasureManifestAction, ...]

    def redacted_evidence(self) -> dict[str, object]:
        """Return the full manifest payload without raw subject data."""

        return {
            "manifestId": self.manifest_id,
            "tenantId": self.tenant_id,
            "requestId": self.request_id,
            "subjectHash": self.subject_hash,
            "status": self.status,
            "actions": [action.redacted_evidence() for action in self.actions],
        }


def resolve_erasure_subject(
    request: ErasureRequest,
    records: Sequence[Mapping[str, object]],
    *,
    identity_fields: Sequence[str],
    resource_type: ErasureResourceType,
    surface: str,
    id_field: str = "id",
    tenant_field: str = "tenant_id",
) -> ErasureResolution:
    """Resolve subject matches from one tenant-scoped surface into resources."""

    resources = tuple(
        ErasureResourceRef(
            tenant_id=str(record[tenant_field]),
            resource_type=resource_type,
            resource_id=str(record[id_field]),
            surface=surface,
        )
        for record in records
        if _record_matches_subject(
            request,
            record,
            identity_fields=identity_fields,
            id_field=id_field,
            tenant_field=tenant_field,
        )
    )
    return ErasureResolution(request=request, resources=resources)


def build_erasure_manifest(
    request: ErasureRequest,
    resolutions: Sequence[ErasureResolution],
    *,
    retention_policy: ErasureRetentionPolicy,
) -> ErasureManifest:
    """Build a stable manifest that records immediate and deferred erasure work."""

    resources = _resources_for_request(request, resolutions)
    actions = tuple(
        sorted(
            (
                *(_action_for_resource(request, resource, retention_policy=retention_policy) for resource in resources),
                _audit_minimization_action(request, retention_policy=retention_policy),
            ),
            key=_action_sort_key,
        )
    )
    status: ErasureManifestStatus = "PENDING_RETENTION" if _has_deferred_action(actions) else "READY_TO_APPLY"
    return ErasureManifest(
        manifest_id=_manifest_id(request, actions=actions, retention_policy=retention_policy),
        tenant_id=request.tenant_id,
        request_id=request.request_id,
        subject_hash=request.subject_hash,
        status=status,
        actions=actions,
    )


def is_erased_resource(
    manifest: ErasureManifest,
    *,
    resource_type: ErasureResourceType,
    resource_id: str,
) -> bool:
    """Return whether a rebuild should exclude a resource already in the manifest."""

    return any(
        action.resource.resource_type == resource_type and action.resource.resource_id == resource_id
        for action in manifest.actions
        if action.action_type != "minimize_audit_evidence"
    )


def _record_matches_subject(
    request: ErasureRequest,
    record: Mapping[str, object],
    *,
    identity_fields: Sequence[str],
    id_field: str,
    tenant_field: str,
) -> bool:
    """Match one candidate record without crossing tenant boundaries."""

    if record.get(tenant_field) != request.tenant_id or id_field not in record:
        return False
    return any(record.get(field_name) == request.subject_value for field_name in identity_fields)


def _resources_for_request(
    request: ErasureRequest,
    resolutions: Sequence[ErasureResolution],
) -> tuple[ErasureResourceRef, ...]:
    """Merge already tenant-checked resolutions for the same request."""

    resources: list[ErasureResourceRef] = []
    for resolution in resolutions:
        if resolution.request != request:
            raise ValueError("erasure manifest cannot mix resolutions for different requests")
        resources.extend(resolution.resources)
    return tuple(sorted(resources, key=_resource_sort_key))


def _action_for_resource(
    request: ErasureRequest,
    resource: ErasureResourceRef,
    *,
    retention_policy: ErasureRetentionPolicy,
) -> ErasureManifestAction:
    """Map a resource to the concrete action the erasure executor would apply."""

    if resource.resource_type == "backup_snapshot":
        return _backup_deferred_action(request, resource, retention_policy=retention_policy)
    return ErasureManifestAction(
        action_type=_action_type(resource.resource_type),
        resource=resource,
        status="READY",
        reason="subject_erasure_requested",
        evidence={
            "requestId": request.request_id,
            "subjectHash": request.subject_hash,
        },
    )


def _backup_deferred_action(
    request: ErasureRequest,
    resource: ErasureResourceRef,
    *,
    retention_policy: ErasureRetentionPolicy,
) -> ErasureManifestAction:
    """Record why backups wait for retention expiry instead of immediate deletion."""

    return ErasureManifestAction(
        action_type="defer_backup_expiration",
        resource=resource,
        status="DEFERRED",
        reason="backup_retention_window_active",
        evidence={
            "requestId": request.request_id,
            "subjectHash": request.subject_hash,
            "retentionUntil": retention_policy.backup_retention_until,
            "cryptoShreddingKeyRef": retention_policy.crypto_shredding_key_ref,
        },
    )


def _audit_minimization_action(
    request: ErasureRequest,
    *,
    retention_policy: ErasureRetentionPolicy,
) -> ErasureManifestAction:
    """Retain only the minimum legal audit evidence for the erasure request."""

    return ErasureManifestAction(
        action_type="minimize_audit_evidence",
        resource=ErasureResourceRef(
            tenant_id=request.tenant_id,
            resource_type="audit_event",
            resource_id=request.request_id,
            surface="audit_events",
        ),
        status="READY",
        reason="retain_minimum_legal_audit_evidence",
        evidence={
            "requestId": request.request_id,
            "subjectHash": request.subject_hash,
            "auditRetentionBasis": retention_policy.audit_retention_basis,
            "minimumFields": list(retention_policy.minimum_audit_fields),
        },
    )


def _action_type(resource_type: ErasureResourceType) -> ErasureActionType:
    """Translate a resource class into its manifest action name."""

    action_types: dict[ErasureResourceType, ErasureActionType] = {
        "dataset_row": "redact_dataset_row",
        "object_record": "tombstone_object",
        "search_document": "remove_search_document",
        "materialization_row": "rebuild_materialization",
        "dead_letter_record": "redact_dead_letter_payload",
        "backup_snapshot": "defer_backup_expiration",
        "audit_event": "minimize_audit_evidence",
    }
    return action_types[resource_type]


def _manifest_id(
    request: ErasureRequest,
    *,
    actions: Sequence[ErasureManifestAction],
    retention_policy: ErasureRetentionPolicy,
) -> str:
    """Derive the retry-stable manifest id from redacted request evidence."""

    payload = {
        "request": request.redacted_evidence(),
        "actions": [action.redacted_evidence() for action in actions],
        "retention": {
            "backupRetentionUntil": retention_policy.backup_retention_until,
            "cryptoShreddingKeyRef": retention_policy.crypto_shredding_key_ref,
            "auditRetentionBasis": retention_policy.audit_retention_basis,
            "minimumAuditFields": list(retention_policy.minimum_audit_fields),
        },
    }
    return "erase_manifest_" + str(uuid.uuid5(_ERASURE_MANIFEST_NAMESPACE, _json_dumps(payload)))


def _has_deferred_action(actions: Sequence[ErasureManifestAction]) -> bool:
    """Return whether any action must wait for a retention window."""

    return any(action.status == "DEFERRED" for action in actions)


def _resource_sort_key(resource: ErasureResourceRef) -> tuple[str, str, str, str]:
    """Sort resources deterministically before manifest hashing."""

    return (resource.resource_type, resource.surface, resource.resource_id, resource.tenant_id)


def _action_sort_key(action: ErasureManifestAction) -> tuple[str, str, str, str]:
    """Sort actions deterministically before manifest hashing."""

    return (action.action_type, action.resource.resource_type, action.resource.surface, action.resource.resource_id)


def _stable_hash(payload: Mapping[str, object]) -> str:
    """Hash evidence payloads without leaking full protected values."""

    return "sha256:" + hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()[:16]


def _json_dumps(payload: object) -> str:
    """Dump JSON in the canonical shape used for stable ids."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
